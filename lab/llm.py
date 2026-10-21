"""Model access for the test lab.

Three roles, each behind a small interface so tests can swap in scripted fakes:
- the agent, which holds a conversation with tools
- the simulated borrower, which only writes text
- the judges, which return JSON

The real implementation talks to any OpenAI-compatible chat API. The default is
the Groq free tier; Gemini, OpenRouter or a local Ollama server work by changing
LLM_BASE_URL and the model names in .env. Free tiers limit requests and tokens
per minute and per day, so every call goes through a rate limiter, and a limit
that does not clear stops the suite so it can resume later.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class AgentReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class AgentModel(Protocol):
    name: str

    def start(self, system: str, tools: list[dict[str, Any]], opening: str) -> None: ...
    def add_user(self, text: str) -> None: ...
    def generate(self) -> AgentReply: ...
    def add_tool_results(self, results: list[tuple[ToolCall, dict[str, Any]]]) -> None: ...


class TextModel(Protocol):
    name: str

    def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        """turns are (role, text) with role "user" or "model"."""
        ...

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class QuotaExhausted(RuntimeError):
    """Raised when a daily free-tier limit is hit. The suite stops and resumes on the next run."""


class RateLimiter:
    """Keeps one model's requests under its per-minute limits.

    Free tiers cap both requests and input tokens per minute (Groq: 7,000 input
    tokens a minute for the agent model). One agent turn sends 3,000 to 6,000
    tokens, so counting requests alone is not enough: before each request the
    limiter waits until the last minute's input tokens plus this request's
    estimate fit under the cap. A per-minute 429 is waited out and retried;
    only a daily limit raises QuotaExhausted.
    """

    MAX_MINUTE_RETRIES = 8

    def __init__(self, rpm: int, tpm: int = 0) -> None:
        self.interval = 60.0 / max(rpm, 1)
        self.tpm = tpm
        self._lock = threading.Lock()
        self._next = 0.0
        self._window: list[tuple[float, int]] = []  # (time, input tokens) over the last minute

    def _wait_for_tokens(self, estimate: int) -> None:
        if not self.tpm:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._window = [(t, n) for t, n in self._window if now - t < 60]
                used = sum(n for _, n in self._window)
                if used + estimate <= self.tpm or not self._window:
                    return
                wait = 60 - (now - self._window[0][0]) + 0.5
            time.sleep(max(wait, 0.5))

    def record(self, tokens: int) -> None:
        with self._lock:
            self._window.append((time.monotonic(), tokens))

    def call(self, fn: Callable[[], Any], estimate: int = 0) -> Any:
        import openai

        for attempt in range(self.MAX_MINUTE_RETRIES + 1):
            self._wait_for_tokens(estimate)
            with self._lock:
                now = time.monotonic()
                wait = self._next - now
                self._next = max(now, self._next) + self.interval
            if wait > 0:
                time.sleep(wait)
            try:
                resp = fn()
            except openai.RateLimitError as e:
                message = str(e).lower()
                if "per day" in message or attempt == self.MAX_MINUTE_RETRIES:
                    raise QuotaExhausted(str(e)) from e
                found = re.search(r"try again in ([\d.]+)s", message)
                time.sleep(float(found.group(1)) + 1 if found else 15)
                continue
            usage = getattr(resp, "usage", None)
            self.record(getattr(usage, "prompt_tokens", 0) or estimate)
            return resp
        raise AssertionError("unreachable")


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _clean(text: str | None) -> str:
    # Some open models put their reasoning in <think> tags inside the reply.
    return _THINK.sub("", text or "").strip()


# OpenAI-compatible implementation


def _estimate_tokens(*parts: Any) -> int:
    """Rough input size for the per-minute budget. Devanagari costs more tokens per character, so err high."""
    return int(len(json.dumps(parts, ensure_ascii=False)) / 2.5) + 50


def _client() -> Any:
    from openai import OpenAI

    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise RuntimeError("LLM_API_KEY is not set. Get a free key at console.groq.com/keys and add it to .env.")
    # Short timeout so a stalled request is retried instead of hanging for ten minutes.
    return OpenAI(api_key=key, base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL), max_retries=2, timeout=60)


def _functions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


class ChatAgent:
    def __init__(self, client: Any, model: str, temperature: float, limiter: RateLimiter) -> None:
        self.client, self.name, self.temperature, self.limiter = client, model, temperature, limiter
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[dict[str, Any]], opening: str) -> None:
        self.tools = _functions(tools)
        # The opening line is fixed text in Retell (begin_message), so it enters history as an assistant turn.
        self.messages = [{"role": "system", "content": system}, {"role": "assistant", "content": opening}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def generate(self) -> AgentReply:
        resp = self.limiter.call(
            lambda: self.client.chat.completions.create(
                model=self.name, messages=self.messages, tools=self.tools, tool_choice="auto", temperature=self.temperature
            ),
            estimate=_estimate_tokens(self.messages, self.tools),
        )
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        self.messages.append(entry)
        return AgentReply(text=_clean(msg.content), tool_calls=calls)

    def add_tool_results(self, results: list[tuple[ToolCall, dict[str, Any]]]) -> None:
        for call, result in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})


class ChatText:
    def __init__(self, client: Any, model: str, temperature: float, limiter: RateLimiter) -> None:
        self.client, self.name, self.temperature, self.limiter = client, model, temperature, limiter

    def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": "assistant" if r == "model" else "user", "content": t} for r, t in turns]
        resp = self.limiter.call(
            lambda: self.client.chat.completions.create(model=self.name, messages=messages, temperature=self.temperature),
            estimate=_estimate_tokens(messages),
        )
        return _clean(resp.choices[0].message.content)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        # JSON mode guarantees valid JSON; the prompt states the fields, and the caller checks them.
        instruction = f"{system}\nReply with one JSON object only, matching this schema: {json.dumps(schema)}"
        resp = self.limiter.call(
            lambda: self.client.chat.completions.create(
                model=self.name,
                messages=[{"role": "system", "content": instruction}, {"role": "user", "content": prompt}],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            ),
            estimate=_estimate_tokens([instruction, prompt]),
        )
        try:
            return json.loads(_clean(resp.choices[0].message.content))
        except json.JSONDecodeError:
            return {}


@dataclass
class Models:
    agent_factory: Callable[[], AgentModel]
    borrower: TextModel
    judge: TextModel


def env_models() -> dict[str, str]:
    return {
        "agent": os.environ.get("LAB_AGENT_MODEL", "qwen/qwen3.8-27b"),
        "sim": os.environ.get("SIM_MODEL", "openai/gpt-oss-20b"),
        "judge": os.environ.get("JUDGE_MODEL", "openai/gpt-oss-120b"),
    }


def chat_models(agent_temperature: float) -> Models:
    client = _client()
    names = env_models()
    def limiter(role: str, rpm: str) -> RateLimiter:
        return RateLimiter(int(os.environ.get(f"{role}_RPM", rpm)), int(os.environ.get(f"{role}_ITPM", "6500")))

    agent_limiter = limiter("AGENT", "20")
    sim = ChatText(client, names["sim"], 0.9, limiter("SIM", "25"))
    judge = ChatText(client, names["judge"], 0.0, limiter("JUDGE", "20"))
    return Models(
        agent_factory=lambda: ChatAgent(client, names["agent"], agent_temperature, agent_limiter),
        borrower=sim,
        judge=judge,
    )


# Scripted fakes for offline tests


class ScriptedAgent:
    name = "scripted-agent"

    def __init__(self, replies: list[AgentReply]) -> None:
        self.replies = list(replies)
        self.seen: list[Any] = []

    def start(self, system: str, tools: list[dict[str, Any]], opening: str) -> None:
        self.system, self.tools = system, tools

    def add_user(self, text: str) -> None:
        self.seen.append(("user", text))

    def generate(self) -> AgentReply:
        return self.replies.pop(0) if self.replies else AgentReply(tool_calls=[ToolCall("end", "end_call", {})])

    def add_tool_results(self, results: list[tuple[ToolCall, dict[str, Any]]]) -> None:
        self.seen.append(("tool", results))


class ScriptedText:
    name = "scripted-text"

    def __init__(self, replies: list[str] | None = None, json_replies: list[dict[str, Any]] | None = None) -> None:
        self.replies = list(replies or [])
        self.json_replies = list(json_replies or [])
        self.prompts: list[str] = []

    def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        return self.replies.pop(0) if self.replies else "[END_CALL]"

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.json_replies.pop(0) if self.json_replies else {"reasoning": "No issue found.", "verdict": "Pass"}
