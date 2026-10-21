from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import Store
from tests.conftest import NOW, SUNIL, at

API_KEY = "key_test_only"


def sign(body: str, key: str = API_KEY, ts_ms: int | None = None) -> str:
    """Signature exactly as Retell documents it: HMAC-SHA256(body + timestamp) with the API key."""
    ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    digest = hmac.new(key.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()
    return f"v={ts},d={digest}"


def make_client(store: Store, tmp_path: Path, now=NOW, key: str = API_KEY) -> TestClient:
    settings = Settings(retell_api_key=key, db_path=tmp_path / "unused.db", human_desk_number="", agent_ids_file=tmp_path / "ids.json")
    return TestClient(create_app(settings, store, clock=lambda: now))


def post(client: TestClient, path: str, payload: dict, signature: str | None = None):
    body = json.dumps(payload)
    return client.post(path, content=body, headers={"x-retell-signature": signature or sign(body), "content-type": "application/json"})


def tool_payload(name: str, args: dict, call_id: str = "call_1", call_type: str = "web_call") -> dict:
    return {
        "name": name,
        "args": args,
        "call": {"call_id": call_id, "call_type": call_type, "metadata": {"borrower_id": SUNIL}},
    }


@pytest.fixture
def client(store: Store, tmp_path: Path) -> TestClient:
    return make_client(store, tmp_path)


class TestSignature:
    def test_valid_signature_accepted(self, client) -> None:
        r = post(client, "/tools/verify_identity", tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1991"}))
        assert r.status_code == 200 and r.json()["verified"] is True

    def test_missing_signature_rejected(self, client) -> None:
        r = client.post("/tools/verify_identity", content="{}")
        assert r.status_code == 401

    def test_wrong_key_rejected(self, client) -> None:
        body = tool_payload("verify_identity", {})
        r = post(client, "/tools/verify_identity", body, signature=sign(json.dumps(body), key="other_key"))
        assert r.status_code == 401

    def test_tampered_body_rejected(self, client) -> None:
        good = json.dumps(tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1990"}))
        sig = sign(good)
        tampered = good.replace("1990", "1991")
        r = client.post("/tools/verify_identity", content=tampered, headers={"x-retell-signature": sig})
        assert r.status_code == 401

    def test_old_timestamp_rejected(self, client) -> None:
        body = json.dumps(tool_payload("verify_identity", {}))
        ten_min_ago = int(time.time() * 1000) - 10 * 60 * 1000
        r = client.post("/tools/verify_identity", content=body, headers={"x-retell-signature": sign(body, ts_ms=ten_min_ago)})
        assert r.status_code == 401

    def test_no_api_key_configured(self, store, tmp_path) -> None:
        r = post(make_client(store, tmp_path, key=""), "/tools/verify_identity", tool_payload("verify_identity", {}))
        assert r.status_code == 503


class TestToolEndpoints:
    def test_full_happy_path(self, client, store) -> None:
        post(client, "/tools/verify_identity", tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1991"}))
        r = post(client, "/tools/log_promise_to_pay", tool_payload("log_promise_to_pay", {"date_iso": "2026-10-24", "amount_inr": 4500, "mode": "payment_link"}))
        assert r.json() == {"accepted": True, "partial": False}
        assert store.one("SELECT call_id, promised_date FROM promises") == {"call_id": "call_1", "promised_date": "2026-10-24"}

    def test_borrower_from_dynamic_variables_when_no_metadata(self, client) -> None:
        payload = tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1991"})
        payload["call"].pop("metadata")
        payload["call"]["retell_llm_dynamic_variables"] = {"borrower_id": SUNIL}
        assert post(client, "/tools/verify_identity", payload).json()["verified"] is True

    def test_borrower_from_fixed_tool_argument(self, client, store) -> None:
        # Retell dashboard test calls have no metadata; the tool's const argument carries the borrower.
        payload = tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1991", "borrower_id": SUNIL})
        payload["call"].pop("metadata")
        assert post(client, "/tools/verify_identity", payload).json()["verified"] is True
        logged = json.loads(store.one("SELECT args_json FROM tool_events")["args_json"])
        assert "borrower_id" not in logged

    def test_unfilled_template_is_ignored(self, client) -> None:
        payload = tool_payload("verify_identity", {"answer_type": "birth_year", "answer": "1991", "borrower_id": "{{borrower_id}}"})
        payload["call"].pop("metadata")
        assert post(client, "/tools/verify_identity", payload).json()["ok"] is False

    def test_identity_gate_through_http(self, client) -> None:
        r = post(client, "/tools/send_payment_link", tool_payload("send_payment_link", {"channel": "sms"}))
        assert r.status_code == 200 and r.json()["ok"] is False


class TestWebhook:
    def call(self, **extra) -> dict:
        return {
            "call_id": "call_9",
            "call_type": "web_call",
            "agent_id": "agent_x",
            "metadata": {"borrower_id": SUNIL},
            "start_timestamp": 1792560000000,
            **extra,
        }

    def test_call_lifecycle(self, client, store) -> None:
        assert post(client, "/webhooks/retell", {"event": "call_started", "call": self.call()}).status_code == 204
        transcript = [
            {"role": "agent", "content": "Namaste, main Meera ... Yeh call record ho rahi hai. Kya meri baat Sunil Verma ji se ho rahi hai?"},
            {"role": "user", "content": "Haan"},
        ]
        post(client, "/webhooks/retell", {"event": "call_ended", "call": self.call(end_timestamp=1792560090000, disconnection_reason="agent_hangup", transcript_object=transcript, recording_url="https://example.invalid/rec.wav")})
        analysis = {"call_summary": "Promise to pay.", "user_sentiment": "Neutral", "custom_analysis_data": {"outcome": "promise_to_pay"}}
        post(client, "/webhooks/retell", {"event": "call_analyzed", "call": self.call(call_analysis=analysis)})

        row = store.one("SELECT * FROM calls WHERE call_id = 'call_9'")
        assert row is not None
        assert row["borrower_id"] == SUNIL
        assert row["recording_notice_given"] == 1
        assert row["outcome"] == "promise_to_pay"
        assert row["user_sentiment"] == "Neutral"
        assert row["started_at"].endswith("+05:30")
        assert len(store.all("SELECT * FROM webhook_events")) == 3

    def test_missing_recording_notice_is_flagged(self, client, store) -> None:
        transcript = [{"role": "agent", "content": "Hello, am I speaking with Sunil?"}]
        post(client, "/webhooks/retell", {"event": "call_ended", "call": self.call(transcript_object=transcript)})
        assert store.one("SELECT recording_notice_given FROM calls") == {"recording_notice_given": 0}

    def test_repeated_event_is_safe(self, client, store) -> None:
        for _ in range(2):
            post(client, "/webhooks/retell", {"event": "call_started", "call": self.call()})
        assert len(store.all("SELECT * FROM calls")) == 1

    def test_unsigned_webhook_rejected(self, client) -> None:
        r = client.post("/webhooks/retell", content=json.dumps({"event": "call_started", "call": self.call()}))
        assert r.status_code == 401


class TestWebCallPage:
    def test_not_served_to_remote_clients(self, client) -> None:
        # TestClient connects as "testclient", which is not a local address.
        assert client.get("/webcall").status_code == 403
        assert client.post("/webcall/start", json={"borrower_id": SUNIL}).status_code == 403

    def test_not_served_through_a_tunnel(self, client) -> None:
        client.app.state.local_hosts.add("testclient")
        r = client.get("/webcall", headers={"x-forwarded-for": "203.0.113.5"})
        assert r.status_code == 403

    def test_served_locally(self, client) -> None:
        client.app.state.local_hosts.add("testclient")
        assert client.get("/webcall").status_code == 200

    def test_start_refused_at_9pm_and_logged(self, store, tmp_path) -> None:
        client = make_client(store, tmp_path, now=at(21))
        client.app.state.local_hosts.add("testclient")
        r = client.post("/webcall/start", json={"borrower_id": SUNIL})
        assert r.status_code == 409 and r.json()["reason"] == "outside_hours"
        assert store.one("SELECT status, refusal_reason FROM call_attempts") == {"status": "refused", "refusal_reason": "outside_hours"}
