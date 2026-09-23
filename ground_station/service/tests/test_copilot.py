"""Tests for the co-pilot backend (ground_station.service.copilot).

All tests use FakeLLM -- zero network traffic.
"""
import json
import os
import re
import threading
import time
from unittest.mock import MagicMock

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore
from ground_station.service.copilot import (
    Copilot,
    FakeLLM,
    LLMError,
    HTTPError,
    RateLimitedError,
    OpenAILLM,
    build_copilot,
    _has_key,
    _short_error,
)


def _schema():
    return StreamSchema(1, 1, 4,
                        (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)


@pytest.fixture()
def service():
    svc = GroundStationService(store=SessionStore(), schemas=[_schema()],
                               source="sim")
    svc.start()
    return svc


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def say_log():
    """Return a list and a say_fn that appends (text, source) tuples."""
    log: list[tuple[str, str]] = []
    def say_fn(text: str, source: str) -> dict:
        log.append((text, source))
        return {"seq": len(log), "text": text, "source": source}
    return log, say_fn


@pytest.fixture()
def state_fn():
    """Return a state_fn that gives a minimal snapshot."""
    def fn() -> dict:
        return {
            "control": {"mode": "supervised"},
            "running_plan": None,
            "pending_approvals": [],
            "last_messages": [],
        }
    return fn


@pytest.fixture()
def copilot(say_log, state_fn):
    """Build a Copilot wired to FakeLLM + the say log + state_fn."""
    llm = FakeLLM(reply="copilot says hello")
    log, say_fn = say_log
    return copilot_with(log, say_fn, llm, state_fn)


def copilot_with(log, say_fn, llm, state_fn):
    """Helper to build a Copilot and return (copilot, llm, log)."""
    c = Copilot(llm, state_fn, say_fn)
    return c, llm, log


# ── Core: reply posted through say_fn ──────────────────────────────────────

def test_reply_is_posted(copilot):
    """A normal operator message produces a reply in the say log."""
    c, llm, log = copilot
    reply = c.handle("what is the flight mode?")
    assert reply == "copilot says hello"
    # say_fn was called with the reply
    assert len(log) == 1
    text, source = log[0]
    assert text == "copilot says hello"
    assert source == "agent:copilot"


def test_handle_returns_the_reply_text(copilot):
    """handle() returns the raw LLM reply for inspection."""
    c, llm, _ = copilot
    assert c.handle("hello") == "copilot says hello"


# ── No key means off ──────────────────────────────────────────────────────

def test_no_key_built_copilot_returns_none():
    """build_copilot returns None when no COPILOT_API_KEY is set."""
    old = os.environ.pop("COPILOT_API_KEY", None)
    os.environ.pop("COPILOT_OFF", None)
    try:
        mock_state = MagicMock()
        mock_say = MagicMock()
        result = build_copilot(agent_state_fn=mock_state, say_fn=mock_say)
        assert result is None
    finally:
        if old is not None:
            os.environ["COPILOT_API_KEY"] = old


def test_copilot_off_env_disables_build():
    """COPILOT_OFF=1 makes build_copilot return None even with a key."""
    os.environ["COPILOT_API_KEY"] = "fakekey123"
    os.environ["COPILOT_OFF"] = "1"
    try:
        mock_state = MagicMock()
        mock_say = MagicMock()
        result = build_copilot(agent_state_fn=mock_state, say_fn=mock_say)
        assert result is None
    finally:
        os.environ.pop("COPILOT_API_KEY", None)
        os.environ.pop("COPILOT_OFF", None)


def test_disabled_copilot_handle_is_noop(copilot):
    """Calling handle on a disabled copilot is a no-op."""
    c, _, log = copilot
    c.disable()
    reply = c.handle("test")
    assert reply == ""
    assert len(log) == 0


# ── LLM exception → short error in chat ────────────────────────────────────

def test_llm_exception_becomes_error_message(copilot):
    """When the LLM raises, a short error is posted in chat."""
    c, llm, log = copilot
    llm.raise_on = True
    reply = c.handle("test")
    assert "co-pilot error" in reply.lower() or "error" in reply.lower()
    # The error was posted via say_fn
    assert len(log) == 1
    assert "error" in log[0][0].lower()


# ── Key never appears in messages or logs ──────────────────────────────────

def test_key_never_in_log(copilot):
    """The API key must never appear in any message or log."""
    c, llm, log = copilot
    c.handle("test")
    for text, _source in log:
        assert "fakekey" not in text.lower()


def test_key_never_in_error_message(copilot):
    """Even on LLM error, the key must not leak into the error text."""
    c, llm, log = copilot
    llm.raise_on = True
    c.handle("test")
    for text, _source in log:
        assert "fakekey" not in text.lower()
        # Check no long token-like substrings (40+ chars of alphanum).
        tokens = re.findall(r"[A-Za-z0-9_\-]{40,}", text)
        assert tokens == [], f"token leak in: {text!r}"


# ── Operator echo not re-answered (no loops) ──────────────────────────────
# This tests the copilot layer itself -- the agent.py integration test
# verifies that agent messages don't trigger copilot.


def test_copilot_can_reply_twice_to_separate_messages(copilot):
    """Two separate operator messages both get replies."""
    c, llm, log = copilot
    c.handle("first")
    c.handle("second")
    assert len(log) == 2
    assert llm.call_count == 2


# ── History management ─────────────────────────────────────────────────────

def test_history_keeps_recent_turns(say_log, state_fn):
    """The copilot maintains a conversation history."""
    log, say_fn = say_log
    # Track what messages were sent to the LLM.
    captured_messages: list[list[dict]] = []

    class TrackingLLM(FakeLLM):
        def __call__(self, messages):
            captured_messages.append(list(messages))
            return "reply"

    llm = TrackingLLM()
    c = Copilot(llm, state_fn, say_fn)

    c.handle("msg1")
    c.handle("msg2")
    # Second call should include first turn in history.
    assert len(captured_messages) == 2, f"captured {len(captured_messages)}"
    second_messages = captured_messages[1]
    # Should have system + user (msg1) + assistant (reply) + user (msg2)
    roles = [m["role"] for m in second_messages]
    assert "user" in roles  # msg1 should be in there


# ── FakeLLM call_count ─────────────────────────────────────────────────────

def test_fake_llm_counts_calls(copilot):
    """FakeLLM tracks how many times it was called."""
    c, llm, _ = copilot
    c.handle("a")
    c.handle("b")
    c.handle("c")
    assert llm.call_count == 3


# ── Token cap / context trimming ───────────────────────────────────────────

def test_copilot_trims_oversized_history(copilot):
    """Very long conversation history gets trimmed before sending to LLM."""
    c, llm, _ = copilot
    # Generate lots of turns to exceed _TOKEN_CAP_CHARACTERS.
    long_text = "x" * 500
    for i in range(20):
        c.handle(long_text)
    # Should not raise -- context is trimmed.
    assert llm.call_count == 20


# ── OpenAILLM structure (mocked network) ────────────────────────────────────

def test_openai_llm_builds_correct_request():
    """OpenAILLM sends correct POST to /chat/completions with Bearer auth."""
    import urllib
    original = urllib.request.build_opener
    call_log: list[dict] = []

    def mock_opener(*args, **kwargs):
        class MockResp:
            def open(self, req, timeout=None):
                call_log.append({
                    "url": req.full_url,
                    "method": req.get_method(),
                    "headers": dict(req.headers) if req.headers else {},
                    "data": req.data,
                })
                # Return a valid OpenAI-style response.
                class FakeResp:
                    def read(self):
                        return json.dumps({
                            "choices": [{"message": {"content": "mocked"}}],
                        }).encode()
                return FakeResp()
        m = MagicMock()
        m.open = MockResp().open
        return m

    urllib.request.build_opener = mock_opener
    try:
        llm = OpenAILLM(
            base_url="https://example.com/api/v1",
            model="test-model",
            key="secret-key-12345",
            timeout=5.0,
        )
        llm([{"role": "user", "content": "hi"}])
    finally:
        urllib.request.build_opener = original

    assert len(call_log) == 1
    req = call_log[0]
    assert "/chat/completions" in req["url"]
    assert req["method"] == "POST"
    assert "Bearer secret-key-12345" in req.get("headers", {}).get("Authorization", "")
    body = json.loads(req["data"])
    assert body["model"] == "test-model"


def test_openai_llm_http_error_becomes_http_error():
    """HTTP 4xx/5xx from the endpoint raises HTTPError."""
    import urllib
    import urllib.error as _urllib_err

    class FakeHTTPError(_urllib_err.HTTPError):
        def __init__(self, url):
            super().__init__(url, 429, "Too Many Requests", {}, None)
        def read(self):
            return json.dumps({
                "error": {"message": "rate limit exceeded"},
            }).encode()

    class MockOpener:
        def open(self, req, timeout=None):
            raise FakeHTTPError(req.full_url)

    original = urllib.request.build_opener
    urllib.request.build_opener = lambda *a, **k: MockOpener()
    try:
        llm = OpenAILLM(
            base_url="https://api.example.com/v1",
            model="test",
            key="k",
            timeout=5,
        )
        with pytest.raises(HTTPError, match="rate limit"):
            llm([{"role": "user", "content": "x"}])
    finally:
        urllib.request.build_opener = original


def test_openai_llm_rate_limit_works():
    """Consecutive calls within 6 s raise RateLimitedError."""
    llm = OpenAILLM(
        base_url="https://api.example.com/v1",
        model="test",
        key="k",
        timeout=5,
    )
    # First call: mock success (we're testing rate limit, not network)
    import urllib
    original = urllib.request.build_opener
    class FastMock:
        def open(self, req, timeout=None):
            class FR:
                def read(self):
                    return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            return FR()
    urllib.request.build_opener = lambda *a, **k: FastMock()
    try:
        llm([{"role": "user", "content": "first"}])
        # Second call immediately: should be rate-limited.
        with pytest.raises(RateLimitedError, match="busy|rate"):
            llm([{"role": "user", "content": "second"}])
    finally:
        urllib.request.build_opener = original


# ── AgentManager integration (copilot fires on operator notes) ─────────────

def test_agent_manager_fires_copilot_on_operator_note(service):
    """When receive_operator_note is called, the copilot worker processes it."""
    from ground_station.service.agent import build_agent_manager
    from ground_station.service.api import ApiServer

    llm = FakeLLM(reply="copilot reply")
    agent = build_agent_manager(service, copilot=llm)

    say_log: list[tuple[str, str]] = []
    def say_fn(text, source):
        say_log.append((text, source))
        return {"seq": len(say_log), "text": text}
    llm2 = FakeLLM(reply="copilot says hi")
    agent2 = build_agent_manager(service, copilot=llm2)
    agent2.copilot = Copilot(llm2, lambda: {"control": {"mode": "on"}}, say_fn)
    agent2.start()
    try:
        agent2.receive_operator_note("hello from operator", "note", "operator")
        # Wait for worker to process.
        time.sleep(0.5)
        # Check that the say_fn received the copilot reply.
        assert any(t == "copilot says hi" for t, _ in say_log)
    finally:
        agent2.stop()


def test_agent_manager_does_not_fire_copilot_on_own_messages(service):
    """Copilot does not re-process its own replies (no echo loops)."""
    from ground_station.service.agent import build_agent_manager

    llm = FakeLLM(reply="copilot answer")
    agent = build_agent_manager(service, copilot=llm)
    agent.copilot = Copilot(
        llm,
        lambda: {"control": {"mode": "on"}},
        lambda text, source: None,
    )
    agent.start()
    try:
        # Send an agent:copilot message -- should NOT trigger copilot.
        agent.receive_operator_note("copilot said something", "note",
                                    "agent:copilot")
        time.sleep(0.3)
        # The LLM should not have been called.
        assert llm.call_count == 0
    finally:
        agent.stop()


# ── build_copilot factory ──────────────────────────────────────────────────

def test_build_copilot_with_key_returns_copilot():
    """When key is set and COPILOT_OFF is not, build_copilot returns an instance."""
    os.environ["COPILOT_API_KEY"] = "testkey"
    os.environ.pop("COPILOT_OFF", None)
    try:
        mock_state = MagicMock(return_value={})
        mock_say = MagicMock()
        result = build_copilot(agent_state_fn=mock_state, say_fn=mock_say)
        assert result is not None
        assert isinstance(result, Copilot)
    finally:
        os.environ.pop("COPILOT_API_KEY", None)


def test_build_copilot_uses_env_defaults():
    """build_copilot picks up COPILOT_MODEL and COPILOT_BASE_URL from env."""
    os.environ["COPILOT_API_KEY"] = "k"
    os.environ["COPILOT_MODEL"] = "custom-model"
    os.environ["COPILOT_BASE_URL"] = "https://custom.api"
    os.environ.pop("COPILOT_OFF", None)
    try:
        mock_state = MagicMock(return_value={})
        mock_say = MagicMock()
        cop = build_copilot(agent_state_fn=mock_state, say_fn=mock_say)
        assert cop is not None
        assert isinstance(cop._llm, OpenAILLM)
        assert cop._llm._model == "custom-model"
        assert cop._llm._base == "https://custom.api"
    finally:
        os.environ.pop("COPILOT_API_KEY", None)
        os.environ.pop("COPILOT_MODEL", None)
        os.environ.pop("COPILOT_BASE_URL", None)


# ── _short_error redaction ─────────────────────────────────────────────────
#
# _short_error feeds the operator's chat.  Its input can be a request URL or an
# upstream error body, so it is the last place an API key can escape.  Every key
# literal below is fake.

# 45 chars after the prefix, so it survives a 117-char cut only in part.
_FAKE_LONG = "sk-fake-" + "q7w8e9r1t2y3u4i5o6p7a8s9d0f1g2h3j4k5l6z7x"
_FAKE_SLICE = "q7w8e9r1t2y3u4i5o6p7"  # 20 chars, distinctive


def test_short_error_redacts_key_straddling_the_truncation_point():
    # The regression.  This prefix is 88 chars, so the key starts before the
    # 117-char cut and runs past it: truncate-then-redact left a 29-char
    # fragment, too short for the old {40,} rule, and printed it in chat.
    out = _short_error("upstream refused: " + "x" * 69 + " " + _FAKE_LONG)
    assert _FAKE_SLICE not in out
    assert _FAKE_LONG not in out


def test_short_error_redacts_key_shorter_than_the_old_threshold():
    # 24 chars: under the old {40,} rule, never redacted at all.
    key = "sk-fake-abcd1234efgh5678"
    out = _short_error("connect failed for " + key)
    assert key not in out
    assert "REDACTED" in out


def test_short_error_redacts_bearer_token_and_keeps_the_label():
    out = _short_error("Authorization: Bearer abc123def")
    assert "abc123def" not in out
    # The label survives, so the operator still learns which header failed.
    assert out == "Authorization: Bearer REDACTED"


def test_short_error_redacts_labelled_query_parameters():
    for label in ("api_key=", "api-key:", "token=", "key="):
        out = _short_error("GET https://x/v1?" + label + "tok_9f2b1c")
        assert "tok_9f2b1c" not in out, label
        assert "REDACTED" in out, label


def test_short_error_passes_through_a_message_with_no_key():
    msg = "plain timeout after 30s"
    assert _short_error(msg) == msg


def test_short_error_returns_only_the_first_line():
    assert _short_error("first line only\nTraceback...\n  File x") == "first line only"


def test_short_error_never_exceeds_120_chars():
    for msg in ("short", "y" * 500, "z" * 400 + " " + _FAKE_LONG):
        assert len(_short_error(msg)) <= 120


# ── wiring and conversation memory ─────────────────────────────────────────

def test_key_file_supplies_key_and_base_url(tmp_path, monkeypatch):
    """Without COPILOT_API_KEY the Hetzner entries of the key file are used."""
    f = tmp_path / "agent-keys.env"
    f.write_text("export HETZNER_API_KEY='file-key'\nHETZNER_BASE_URL=https://h.example/api/v1\n")
    monkeypatch.delenv("COPILOT_API_KEY", raising=False)
    monkeypatch.delenv("COPILOT_OFF", raising=False)
    monkeypatch.delenv("COPILOT_BASE_URL", raising=False)
    monkeypatch.setenv("COPILOT_KEY_FILE", str(f))
    cop = build_copilot(agent_state_fn=lambda: {}, say_fn=MagicMock())
    assert cop is not None
    assert cop._llm._key == "file-key"
    assert cop._llm._base == "https://h.example/api/v1"


def test_history_reaches_the_llm_once_per_turn():
    """Earlier turns are sent, and the new message is not duplicated."""
    seen = []

    class _Rec(FakeLLM):
        def __call__(self, messages):
            seen.append(messages)
            return "ok"

    cop = Copilot(_Rec(), lambda: {}, MagicMock())
    cop.handle("first")
    cop.handle("second")
    roles = [(m["role"], m["content"]) for m in seen[-1][1:]]
    assert roles == [("user", "first"), ("assistant", "ok"), ("user", "second")]


def test_api_server_builds_the_copilot(service, monkeypatch):
    """ApiServer wires a co-pilot when a key exists; notes get an answer."""
    from ground_station.service.api import ApiServer
    from ground_station.service import copilot as copilot_mod

    monkeypatch.setenv("COPILOT_API_KEY", "k")
    monkeypatch.delenv("COPILOT_OFF", raising=False)
    monkeypatch.setattr(copilot_mod, "OpenAILLM", lambda **kw: FakeLLM("pong"))
    api = ApiServer(service, port=0)
    try:
        assert api.agent.copilot is not None
        state = api._copilot_state()
        assert "telemetry" in state
    finally:
        api.server.server_close()


def test_copilot_off_says_so_once(service, monkeypatch):
    """With no key the operator gets one explanation instead of silence."""
    from ground_station.service.api import ApiServer

    monkeypatch.delenv("COPILOT_API_KEY", raising=False)
    monkeypatch.setenv("COPILOT_KEY_FILE", "")
    api = ApiServer(service, port=0)
    try:
        api.agent.receive_operator_note("hello", "note", "operator")
        api.agent.receive_operator_note("again", "note", "operator")
        agent_msgs = [n for n in api.agent.notes_since(0) if n.get("kind") == "agent"]
        assert len(agent_msgs) == 1
        assert "Co-pilot is off" in agent_msgs[0]["text"]
    finally:
        api.server.server_close()
