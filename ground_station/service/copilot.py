"""Co-pilot backend: LLM-powered agent replies to operator messages.

This module is the heart of the dashboard co-pilot drawer.  An operator types
a message into the chat UI, it lands on ``POST /api/session/note`` (``kind``
``"note"`` or ``"goal"``), and the co-pilot hook fires in a background thread
to produce a reply that is posted as an agent message through the existing
``say`` path.

Two LLM adapters ship with this module:

* ``OpenAILLM`` -- standard-lib ``urllib``, OpenAI-compatible ``POST`` to any
  ``/chat/completions`` endpoint (Hetzner Inference, OpenRouter, …).
* ``FakeLLM`` -- deterministic echo, used in every test (no network).

When the co-pilot is **off** (no ``COPILOT_API_KEY`` set) nothing fires: the
service is unchanged and the operator sees only their own messages.

Environment variables
---------------------
``COPILOT_API_KEY``  (required for the live model)
``COPILOT_MODEL``    (default ``Qwen/Qwen3.6-35B-A3B-FP8``)
``COPILOT_BASE_URL`` (default ``https://inference.hetzner.com/api/v1``)
``COPILOT_OFF``      (any non-empty value disables the co-pilot entirely,
                      even when a key is set)

All adapters honour ``HTTPS_PROXY`` / ``http_proxy`` / ``https_proxy``
environment variables -- ``urllib.build_opener(ProxyHandler({}))`` picks them
up automatically.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# ── LLM adapter interface ──────────────────────────────────────────────────
# Every adapter must expose a ``__call__(messages: list[dict]) -> str`` that
# returns the assistant's reply text (or raises on failure).


class LLMAdapter:
    """Abstract LLM adapter: ``__call__(messages) -> str``."""

    def __call__(self, messages: list[dict]) -> str:
        raise NotImplementedError


class OpenAILLM(LLMAdapter):
    """OpenAI-compatible ``/chat/completions`` via stdlib ``urllib``.

    Honours HTTPS_PROXY / http_proxy / https_proxy env vars.
    Rate-limited to 1 request per 6 s (Hetzner 10 req/min).
    """

    def __init__(self, base_url: str = "", model: str = "",
                 key: str = "", timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._key = key  # stored; never logged or echoed back
        self._timeout = timeout
        # Rate limiter: 1 req per 6 s (Hetzner 10 req/min).
        self._last_request = 0.0
        self._lock = threading.Lock()
        # Semaphore: 1 in-flight request at a time.
        self._sem = threading.Semaphore(1)

    def __call__(self, messages: list[dict]) -> str:
        # Rate limit: if less than 6 s since last request, return a busy signal.
        with self._lock:
            now = time.monotonic()
            since_last = now - self._last_request
            if since_last < 6.0:
                self._rate_limited_time = 6.0 - since_last
                raise RateLimitedError(
                    f"busy, try again (rate limited, {self._rate_limited_time:.0f}s cooldown)")
            self._last_request = now

        # Build the request payload.
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self._base}/chat/completions"

        # ProxyHandler({}) means "use env vars only" -- never force a proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key}",
        }
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")

        try:
            resp = opener.open(req, timeout=self._timeout)
            raw = resp.read()
        except urllib.error.HTTPError as exc:  # noqa: F821
            raw = b"{}"
            try:
                raw = exc.read()
            except Exception:
                pass
            try:
                err = json.loads(raw or b"{}")
                detail = err.get("error", {}).get("message", str(exc))
            except Exception:
                detail = str(exc)
            raise HTTPError(f"LLM error: {detail}")
        except urllib.error.URLError as exc:  # noqa: F821
            raise HTTPError(f"LLM network error: {exc.reason}")

        try:
            result = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise HTTPError("LLM returned invalid JSON")

        choices = result.get("choices")
        if not choices:
            raise HTTPError("LLM response has no choices")
        msg = choices[0].get("message", {})
        return str(msg.get("content", ""))


class FakeLLM(LLMAdapter):
    """Deterministic reply adapter for tests (no network).

    ``__call__`` returns a fixed ``reply`` string for every invocation,
    or raises ``FakeException`` when ``raise_on=True``.
    """

    def __init__(self, reply: str = "I can help with that.",
                 raise_on: bool = False) -> None:
        self.reply = reply
        self.raise_on = raise_on
        self.call_count = 0

    def __call__(self, messages: list[dict]) -> str:
        self.call_count += 1
        if self.raise_on:
            raise LLMError("fake LLM error for testing")
        return self.reply


class LLMError(Exception):
    """Base exception for LLM errors."""


class HTTPError(LLMError):
    """HTTP/network error from the LLM endpoint."""


class RateLimitedError(LLMError):
    """Rate-limited by the LLM provider."""


# ── Copilot ────────────────────────────────────────────────────────────────
# The copilot is the glue between the agent-manager and an LLM adapter.
# Public interface:
#   Copilot(llm, agent_state_fn, say_fn)
#   copilot.handle(message: str) -> str   (also posts via say_fn)


_MAX_HISTORY_TURNS = 20          # last ~20 operator↔assistant turns
_TOKEN_CAP_CHARACTERS = 4000     # rough character budget for context


class Copilot:
    """LLM-powered co-pilot for the dashboard chat.

    Parameters
    ----------
    llm : LLMAdapter
        The LLM to call for every reply.
    agent_state_fn : callable[[], dict]
        Returns a state snapshot (same shape as ``agent.agent_state()``).
    say_fn : callable[[str, str], dict]
        Posts a message into the existing agent-message pipeline.  Signature
        mirrors ``agent.AgentManager.add_agent_message``:
        ``(text, source) -> dict``.

    The copilot maintains an in-memory conversation history (last
    ``_MAX_HISTORY_TURNS`` operator↔assistant turns), builds a system prompt
    that includes a trimmed state snapshot, and delegates to the LLM.

    All errors are caught and posted as short error messages in the chat.
    The API key is never logged, echoed, or returned.
    """

    def __init__(self, llm: LLMAdapter,
                 agent_state_fn: Callable[[], dict[str, Any]],
                 say_fn: Callable[[str, str], dict]) -> None:
        self._llm = llm
        self._state_fn = agent_state_fn
        self._say = say_fn
        self._lock = threading.Lock()
        # [(role, content), ...]  oldest gets pruned past _MAX_HISTORY_TURNS.
        self._history: list[tuple[str, str]] = []
        self._enabled = True

    # -- public API ---------------------------------------------------------
    def handle(self, message: str) -> str:
        """Process an operator message and return the copilot reply.

        The reply is **also** posted via ``say_fn`` inside this call.
        Returns the raw reply text (for tests to inspect).
        """
        if not self._enabled:
            return ""

        # Record the operator message in history.
        with self._lock:
            self._history.append(("user", str(message)))
            # Trim history to keep last ~20 turns (40 entries).
            while len(self._history) > _MAX_HISTORY_TURNS * 2:
                self._history.pop(0)

        # Build the conversation list for the LLM.
        messages = self._build_messages(message)

        try:
            reply = self._llm(messages)
        except (LLMError, Exception) as exc:  # noqa: BLE001
            # On error, post a short friendly message; never log the key.
            err_text = _short_error(str(exc))
            with self._lock:
                self._history.append(("assistant", err_text))
                while len(self._history) > _MAX_HISTORY_TURNS * 2:
                    self._history.pop(0)
            # Post error in chat.
            self._say(f"Co-pilot error: {err_text}", "agent:copilot")
            return err_text

        # Record assistant reply in history and post it.
        with self._lock:
            self._history.append(("assistant", reply))
            while len(self._history) > _MAX_HISTORY_TURNS * 2:
                self._history.pop(0)
        self._say(reply, "agent:copilot")
        return reply

    def disable(self) -> None:
        """Shut the copilot down gracefully."""
        self._enabled = False

    # -- internal -----------------------------------------------------------
    def _build_messages(self, user_text: str) -> list[dict]:
        """Build the message list to send to the LLM.

        Includes a system prompt with state snapshot, history, and rules.
        """
        # Gather state snapshot (cheap read-only call).
        state = {}
        try:
            state = self._state_fn()
        except Exception:
            pass

        system = self._system_prompt(state)

        # Assemble the conversation.
        messages: list[dict] = [{"role": "system", "content": system}]
        # Add recent history (keep it bounded by character cap).
        content_parts: list[str] = []
        for role, text in self._history:
            entry = f"{role}: {text}"
            if sum(len(p) for p in content_parts) + len(entry) > _TOKEN_CAP_CHARACTERS:
                break
            content_parts.append(entry)
        messages.append({"role": "user", "content": user_text})
        return messages

    def _system_prompt(self, state: dict) -> str:
        """Build the system prompt from the state snapshot."""
        parts: list[str] = [
            "You are a co-pilot assistant for a UAV ground-station dashboard.",
            "",
            "RULES:",
            "- Answer questions based on the dashboard state and conversation.",
            "- You may PROPOSE a plan using standard plan-creation rules "
            "(operator approval is required for critical actions).",
            "- You must NOT arm the drone, call probe tools, or flash firmware.",
            "- If you are unsure, say so; never fabricate telemetry values.",
            "- Keep responses concise and operator-friendly.",
            "",
        ]

        # Include a trimmed state snapshot.
        parts.append("CURRENT STATE:")
        if state:
            # Control mode and running plan are the most useful bits.
            control = state.get("control", {})
            parts.append(f"  Mode: {control.get('mode', '?')}")
            running = state.get("running_plan")
            if running:
                parts.append(f"  Running plan: {running.get('title', '?')} "
                             f"({running.get('status', '?')})")
            approvals = state.get("pending_approvals", [])
            if approvals:
                parts.append(f"  Pending approvals: {len(approvals)}")
            # Include last few messages for context.
            last_msgs = state.get("last_messages", [])
            if last_msgs:
                recent_texts = [
                    m.get("text", "") for m in last_msgs[-5:]
                ]
                if recent_texts:
                    parts.append(f"  Recent messages: {'; '.join(recent_texts)[:200]}")
        else:
            parts.append("  (no state snapshot available)")

        parts.append("")
        return "\n".join(parts)


# A token introduced by a label we recognise: "Bearer x", "api_key=x",
# "api-key: x", "token=x", "key=x".  The label is kept (it tells the operator
# what failed); only the value after it is replaced.  `\b` before the
# alternation stops the bare `key` branch from matching inside `api_key`,
# where `_` is a word character and so offers no boundary.
_LABELLED_SECRET_RE = re.compile(
    r"(?i)(\b(?:bearer|api[_-]?key|token|key)\b\s*[:=]?\s*)(\S+)")
# Vendor-prefixed keys carry no label of their own.
_SK_SECRET_RE = re.compile(r"\bsk-\S+")
# Backstop for anything else key-shaped.  20 is short enough to catch the
# shorter vendor keys and long enough not to eat ordinary words.
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


def _short_error(detail: str) -> str:
    """Return a short, operator-friendly error message (max 120 chars).

    The detail can be a request URL or an upstream error body, either of which
    may carry the API key, so redact before shortening.  Truncating first would
    cut a key that straddles the limit down to a fragment too short for the
    length-based backstop to catch, leaking it into the operator's chat.
    """
    cleaned = detail.split("\n")[0].strip()
    cleaned = _SK_SECRET_RE.sub("REDACTED", cleaned)
    cleaned = _LABELLED_SECRET_RE.sub(lambda m: m.group(1) + "REDACTED", cleaned)
    cleaned = _LONG_TOKEN_RE.sub("REDACTED", cleaned)
    if len(cleaned) > 120:
        cleaned = cleaned[:117] + "..."
    return cleaned


# ── Factory / wiring helpers ───────────────────────────────────────────────


def _default_model() -> str:
    return os.environ.get("COPILOT_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8")


def _default_base_url() -> str:
    return os.environ.get("COPILOT_BASE_URL",
                            "https://inference.hetzner.com/api/v1")


def _has_key() -> bool:
    """Return True when a co-pilot API key is configured and copilot is ON."""
    if os.environ.get("COPILOT_OFF", ""):
        return False
    key = os.environ.get("COPILOT_API_KEY", "")
    return bool(key.strip())


def build_copilot(llm: LLMAdapter | None = None,
                  agent_state_fn: Callable[[], dict] | None = None,
                  say_fn: Callable[[str, str], dict] | None = None,
                  ) -> Copilot | None:
    """Create a Copilot instance from env vars and the given hooks.

    Returns ``None`` when the co-pilot is off (no key or COPILOT_OFF set).
    """
    if not _has_key():
        return None

    if llm is None:
        key = os.environ.get("COPILOT_API_KEY", "")
        llm = OpenAILLM(
            base_url=_default_base_url(),
            model=_default_model(),
            key=key,
        )

    if agent_state_fn is None or say_fn is None:
        return None

    return Copilot(llm, agent_state_fn, say_fn)
