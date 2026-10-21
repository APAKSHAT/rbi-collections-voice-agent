"""FastAPI app: custom function endpoints, Retell webhooks, and a local-only web call page.

Run: uvicorn backend.app:create_app --factory --port 8000
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from backend import calls
from backend.config import IST, Settings
from backend.db import Store, now_iso
from backend.tools import CallContext, run_tool

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "::1"}


def _ms_to_iso(ms: int | None) -> str | None:
    return now_iso(datetime.fromtimestamp(ms / 1000, IST)) if ms else None


def _borrower_id(call: dict[str, Any]) -> str:
    return (call.get("metadata") or {}).get("borrower_id") or (
        call.get("retell_llm_dynamic_variables") or {}
    ).get("borrower_id", "")


def _recording_notice_given(transcript: list[dict[str, Any]]) -> bool | None:
    first_agent = next((t for t in transcript if t.get("role") == "agent"), None)
    if first_agent is None:
        return None
    text = first_agent.get("content", "").lower()
    return "record" in text or "रिकॉर्ड" in text


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(IST),
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    if store.one("SELECT COUNT(*) AS n FROM borrowers")["n"] == 0:  # type: ignore[index]
        store.load_borrowers()

    app = FastAPI(title="Kaveri Finance collections backend")
    app.state.store = store
    app.state.local_hosts = set(LOCAL_HOSTS)

    def verify(raw: bytes, request: Request) -> None:
        if not settings.retell_api_key:
            raise HTTPException(503, "RETELL_API_KEY is not set")
        from retell import Retell

        signature = request.headers.get("x-retell-signature", "")
        ok = Retell(api_key=settings.retell_api_key).verify(
            raw.decode("utf-8"), api_key=settings.retell_api_key, signature=signature
        )
        if not ok:
            raise HTTPException(401, "Invalid signature")

    def local_only(request: Request) -> None:
        # The web call page spends Retell credit, so it must not be reachable through ngrok.
        host = request.client.host if request.client else ""
        if host not in app.state.local_hosts or "x-forwarded-for" in request.headers:
            raise HTTPException(403, "Local access only")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tools/{tool_name}")
    async def tool(tool_name: str, request: Request) -> JSONResponse:
        raw = await request.body()
        verify(raw, request)
        body = json.loads(raw)
        call = body.get("call") or {}
        args = dict(body.get("args") or {})
        # Every tool also carries borrower_id as a fixed argument filled from the call's variables,
        # so dashboard test calls without metadata still resolve to a borrower.
        arg_borrower = args.pop("borrower_id", "")
        if arg_borrower.startswith("{{"):
            arg_borrower = ""
        ctx = CallContext(
            call_id=call.get("call_id", ""),
            borrower_id=_borrower_id(call) or arg_borrower,
            call_type=call.get("call_type", ""),
            human_desk_number=settings.human_desk_number,
        )
        return JSONResponse(run_tool(store, ctx, tool_name, args, clock()))

    @app.post("/webhooks/retell")
    async def webhook(request: Request) -> Response:
        raw = await request.body()
        verify(raw, request)
        body = json.loads(raw)
        event, call = body.get("event", ""), body.get("call") or {}
        call_id = call.get("call_id", "")
        now = clock()
        store.insert(
            "webhook_events",
            {"call_id": call_id, "event": event, "payload_json": raw.decode("utf-8"), "received_at": now_iso(now)},
        )
        fields: dict[str, Any] = {
            "borrower_id": _borrower_id(call) or None,
            "call_type": call.get("call_type"),
            "agent_id": call.get("agent_id"),
            "agent_version": call.get("agent_version"),
            "started_at": _ms_to_iso(call.get("start_timestamp")),
        }
        if event == "call_ended":
            transcript = call.get("transcript_object") or []
            notice = _recording_notice_given(transcript)
            fields.update(
                ended_at=_ms_to_iso(call.get("end_timestamp")),
                disconnection_reason=call.get("disconnection_reason"),
                transcript_json=json.dumps(transcript, ensure_ascii=False),
                recording_url=call.get("recording_url"),
                recording_notice_given=None if notice is None else int(notice),
            )
        if event == "call_analyzed":
            analysis = call.get("call_analysis") or {}
            fields.update(
                analysis_json=json.dumps(analysis, ensure_ascii=False),
                outcome=(analysis.get("custom_analysis_data") or {}).get("outcome"),
                user_sentiment=analysis.get("user_sentiment"),
                call_summary=analysis.get("call_summary"),
            )
        store.upsert_call(call_id, {k: v for k, v in fields.items() if v is not None}, now)
        return Response(status_code=204)

    @app.get("/webcall")
    def webcall_page(request: Request) -> FileResponse:
        local_only(request)
        return FileResponse(STATIC / "webcall.html")

    @app.post("/webcall/start")
    async def webcall_start(request: Request) -> JSONResponse:
        local_only(request)
        borrower_id = (await request.json()).get("borrower_id", "")
        now = clock()
        decision, borrower = calls.precheck(store, borrower_id, now)
        if not decision.ok or borrower is None:
            calls.record_attempt(store, borrower_id, "web", now, decision)
            return JSONResponse({"ok": False, "reason": decision.reason, "message": decision.message}, status_code=409)
        web = calls.create_web_call(settings, borrower, now)
        calls.record_attempt(store, borrower_id, "web", now, decision, web["call_id"])
        return JSONResponse({"ok": True, **web})

    return app

