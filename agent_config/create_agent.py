"""Create or update the Retell LLM and voice agent for one prompt version and language.

Usage:
    python agent_config/create_agent.py --prompt v3 --lang hi --dry-run
    python agent_config/create_agent.py --prompt v3 --lang hi
    python agent_config/create_agent.py --prompt v3 --lang en

There is one agent per language. A single-language agent hears more accurately
than a mixed one (Retell, "Configure a multilingual agent"), and the backend
already knows each borrower's language before it dials.

The first real run for a language creates a Retell LLM and an agent and saves
their IDs under that language in agent_config/retell_ids.json. Later runs
update the same LLM and agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import call_setup  # noqa: E402
from backend.config import IST  # noqa: E402
from backend.db import Store  # noqa: E402

CONFIG_DIR = ROOT / "agent_config"
PROMPT_DIR = ROOT / "prompts" / "agent"
IDS_FILE = CONFIG_DIR / "retell_ids.json"

DEFAULT_AGENT_MODEL = "gemini-3.6-flash"
WEBHOOK_PATH = "/webhooks/retell"

LANGUAGES = {
    "hi": {"locale": "hi-IN", "name": "Hindi", "test_borrower": "KF-TW-000184"},
    "en": {"locale": "en-IN", "name": "English", "test_borrower": "KF-TW-000235"},
}


def load_json(name: str) -> Any:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def prompt_path(version: str, lang: str) -> Path:
    """v3-hi.md if it exists, otherwise the shared v3.md."""
    per_lang = PROMPT_DIR / f"{version}-{lang}.md"
    return per_lang if per_lang.exists() else PROMPT_DIR / f"{version}.md"


def load_prompt(version: str, lang: str) -> str:
    path = prompt_path(version, lang)
    if not path.exists():
        sys.exit(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def load_tools(backend_url: str) -> list[dict[str, Any]]:
    raw = (CONFIG_DIR / "tools.json").read_text(encoding="utf-8")
    return json.loads(raw.replace("${BACKEND_URL}", backend_url.rstrip("/")))


def default_variables(lang: str) -> dict[str, str]:
    """A fictional borrower, so a test call started from the Retell dashboard has every variable filled."""
    store = Store(":memory:")
    store.load_borrowers()
    borrower = store.borrower(LANGUAGES[lang]["test_borrower"])
    assert borrower is not None
    borrower = {**borrower, "preferred_language": lang}
    return call_setup.dynamic_variables(borrower, datetime.now(IST))


def build_llm_payload(prompt_version: str, lang: str, backend_url: str, model: str) -> dict[str, Any]:
    settings = load_json("agent_settings.json")["llm"]
    return {
        "general_prompt": load_prompt(prompt_version, lang),
        "general_tools": load_tools(backend_url),
        "start_speaker": settings["start_speaker"],
        "begin_message": settings["begin_message"],
        "tool_call_strict_mode": settings["tool_call_strict_mode"],
        "default_dynamic_variables": default_variables(lang),
        # retell-sdk 6.2.9 has no model_config argument yet, so it goes in extra_body.
        "extra_body": {
            "model_config": {
                "type": "cascading",
                "primary": {"model": model, "tier": "standard"},
                "temperature": settings["temperature"],
            }
        },
    }


def build_agent_payload(prompt_version: str, lang: str, backend_url: str, llm_id: str, voice_id: str) -> dict[str, Any]:
    settings = load_json("agent_settings.json")["agent"]
    payload = dict(settings)
    payload["agent_name"] = f"{settings['agent_name']} ({prompt_version}, {LANGUAGES[lang]['name']})"
    payload["language"] = LANGUAGES[lang]["locale"]
    payload["response_engine"] = {"type": "retell-llm", "llm_id": llm_id}
    payload["voice_id"] = voice_id
    payload["webhook_url"] = backend_url.rstrip("/") + WEBHOOK_PATH
    payload["post_call_analysis_data"] = load_json("post_call_fields.json")
    return payload


def pick_voice(client: Any) -> str:
    """Use RETELL_VOICE_ID if set, otherwise the first Indian female voice."""
    pinned = os.environ.get("RETELL_VOICE_ID")
    if pinned:
        return pinned
    voices = [v for v in client.voice.list() if v.gender == "female" and "indian" in (v.accent or "").lower()]
    if not voices:
        sys.exit("No Indian female voice found. Pick one in the dashboard and set RETELL_VOICE_ID.")
    print("Indian female voices found:")
    for v in voices:
        print(f"  {v.voice_id}  {v.voice_name}  ({v.provider})  {v.preview_audio_url or ''}")
    print(f"Using {voices[0].voice_id}. Set RETELL_VOICE_ID to pin a different one.")
    return voices[0].voice_id


def load_ids() -> dict[str, Any]:
    if not IDS_FILE.exists():
        return {}
    ids = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    if "agent_id" in ids:  # the first agent was created before agents were split by language
        ids = {"hi": ids}
    return ids


def save_ids(ids: dict[str, Any]) -> None:
    IDS_FILE.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", required=True, help="Prompt version, for example v3")
    parser.add_argument("--lang", required=True, choices=sorted(LANGUAGES), help="Agent language")
    parser.add_argument("--dry-run", action="store_true", help="Print the payloads and stop")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    model = os.environ.get("AGENT_MODEL", DEFAULT_AGENT_MODEL)
    backend_url = os.environ.get("BACKEND_URL", "")
    print(f"Prompt file: {prompt_path(args.prompt, args.lang).relative_to(ROOT)}")

    if args.dry_run:
        backend_url = backend_url or "https://example.ngrok.app"
        llm = build_llm_payload(args.prompt, args.lang, backend_url, model)
        agent = build_agent_payload(args.prompt, args.lang, backend_url, "<llm_id>", "<voice_id>")
        print(json.dumps({"llm": llm, "agent": agent}, indent=2, ensure_ascii=False))
        return

    if not os.environ.get("RETELL_API_KEY"):
        sys.exit("RETELL_API_KEY is not set. Add it to .env.")
    if not backend_url.startswith("https://"):
        sys.exit("BACKEND_URL must be the public https URL of the backend, for example your ngrok URL.")

    from retell import Retell

    client = Retell(api_key=os.environ["RETELL_API_KEY"])
    all_ids = load_ids()
    ids = all_ids.setdefault(args.lang, {})
    llm_payload = build_llm_payload(args.prompt, args.lang, backend_url, model)

    if ids.get("llm_id"):
        llm = client.llm.update(llm_id=ids["llm_id"], **llm_payload)
        print(f"Updated LLM {llm.llm_id}")
    else:
        llm = client.llm.create(**llm_payload)
        ids["llm_id"] = llm.llm_id
        save_ids(all_ids)
        print(f"Created LLM {llm.llm_id}")

    agent_payload = build_agent_payload(args.prompt, args.lang, backend_url, llm.llm_id, pick_voice(client))
    if ids.get("agent_id"):
        agent = client.agent.update(agent_id=ids["agent_id"], **agent_payload)
        print(f"Updated agent {agent.agent_id}")
    else:
        agent = client.agent.create(**agent_payload)
        ids["agent_id"] = agent.agent_id
        print(f"Created agent {agent.agent_id}")

    ids["prompt_version"] = args.prompt
    save_ids(all_ids)
    print(f"{LANGUAGES[args.lang]['name']} agent ID: {agent.agent_id}")


if __name__ == "__main__":
    main()
