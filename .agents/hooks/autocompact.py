#!/usr/bin/env python3
"""Antigravity PreInvocation Auto-Compaction Hook (160,000-token threshold).

Reads the PreInvocation JSON payload from stdin, estimates active un-compacted
context tokens since the last checkpoint in transcript_full.jsonl (or
transcript.jsonl) plus base system/tool overhead, and when total active tokens
reach >= 160,000, returns an injected HookCheckpoint step:

  {"injectSteps": [{"checkpoint": {"sessionSummary": "..."}}]}

This triggers native trajectory compaction automatically inside Antigravity
while running unattended/overnight, preserving the user's goal, handoff state,
and recent progress without stopping execution.
"""

import json
import os
import sys
from pathlib import Path

# Default auto-compaction ceiling: 160,000 tokens (override via AGY_AUTOCOMPACT_TOKENS)
TOKEN_LIMIT = int(os.environ.get("AGY_AUTOCOMPACT_TOKENS", "160000"))
# Measured fixed system prompt + tool/MCP schema overhead per invocation (~60k tokens)
BASE_OVERHEAD_TOKENS = int(os.environ.get("AGY_BASE_OVERHEAD_TOKENS", "60000"))
CHARS_PER_TOKEN = 4


def _read_tail_text(path: Path, max_chars: int = 2500) -> str:
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            return text[:max_chars]
    except Exception:
        pass
    return ""


def _load_transcript_steps(transcript_path: Path):
    """Return list of parsed JSONL steps from transcript_full.jsonl or transcript.jsonl."""
    full_path = transcript_path.with_name("transcript_full.jsonl")
    target = full_path if full_path.is_file() else transcript_path
    if not target.is_file():
        return []
    steps = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    steps.append((len(raw), json.loads(raw)))
                except Exception:
                    steps.append((len(raw), {}))
    except Exception:
        return []
    return steps


def _build_session_summary(steps_since_cp, workspace_root: Path, active_tokens: int) -> str:
    user_inputs = []
    planner_notes = []
    tools_run = []

    for _, step in steps_since_cp:
        stype = str(step.get("type", "")).upper()
        content = str(step.get("content", "")).strip()
        if "USER_INPUT" in stype and content:
            user_inputs.append(content[:600])
        elif "PLANNER_RESPONSE" in stype:
            if content:
                planner_notes.append(content[:500])
            for tc in step.get("tool_calls") or []:
                if isinstance(tc, dict):
                    tname = tc.get("name") or tc.get("tool_name") or ""
                    if tname:
                        tools_run.append(str(tname))

    handoff_text = _read_tail_text(workspace_root / "docs" / "agent" / "HANDOFF.md", 2000)
    overnight_text = _read_tail_text(workspace_root / ".agent-ops" / "OVERNIGHT.md", 1200)

    parts = [
        f"[AUTO-COMPACTED CHECKPOINT at ~{active_tokens:,} tokens (limit: {TOKEN_LIMIT:,} tokens)]",
        "Continue autonomous execution seamlessly from this checkpoint without stopping.",
    ]
    if user_inputs:
        parts.append("## User Goal & Recent Instructions\n" + "\n---\n".join(user_inputs[-3:]))
    if planner_notes:
        parts.append("## Recent Agent Progress (Last Turns)\n" + "\n---\n".join(planner_notes[-4:]))
    if tools_run:
        parts.append(f"## Recent Tools Executed ({len(tools_run)} calls)\n" + ", ".join(tools_run[-15:]))
    if handoff_text:
        parts.append("## Current docs/agent/HANDOFF.md\n" + handoff_text)
    elif overnight_text:
        parts.append("## Current .agent-ops/OVERNIGHT.md\n" + overnight_text)

    return "\n\n".join(parts)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    transcript_str = payload.get("transcriptPath") or ""
    workspaces = payload.get("workspacePaths") or []
    workspace_root = Path(workspaces[0]) if workspaces else Path.cwd()

    if not transcript_str:
        print("{}")
        return 0

    transcript_path = Path(transcript_str)
    steps = _load_transcript_steps(transcript_path)
    if not steps:
        print("{}")
        return 0

    # Locate the last checkpoint step in the transcript
    last_cp_idx = -1
    for idx, (_, step) in enumerate(steps):
        stype = str(step.get("type", "")).upper()
        if "CHECKPOINT" in stype or "COMPACT" in stype or step.get("checkpoint"):
            last_cp_idx = idx

    # Also check our sidecar state file in case the injected checkpoint hasn't flushed to JSONL yet
    state_file = transcript_path.parent / "autocompact_state.json"
    last_recorded_step_count = -1
    try:
        if state_file.is_file():
            st = json.loads(state_file.read_text(encoding="utf-8"))
            last_recorded_step_count = int(st.get("compacted_at_step_count", -1))
    except Exception:
        pass

    start_idx = max(last_cp_idx + 1, last_recorded_step_count if last_recorded_step_count <= len(steps) else 0)
    active_steps = steps[start_idx:]

    # Estimate tokens in active trajectory since last checkpoint
    active_chars = sum(char_len for char_len, _ in active_steps)
    active_tokens = BASE_OVERHEAD_TOKENS + (active_chars // CHARS_PER_TOKEN)

    if active_tokens < TOKEN_LIMIT or len(active_steps) < 4:
        print("{}")
        return 0

    # Record compaction marker so we don't re-trigger on the immediate next step
    try:
        state_file.write_text(
            json.dumps(
                {
                    "compacted_at_step_count": len(steps),
                    "active_tokens_at_compaction": active_tokens,
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    summary = _build_session_summary(active_steps, workspace_root, active_tokens)
    result = {
        "injectSteps": [
            {
                "checkpoint": {
                    "sessionSummary": summary,
                }
            }
        ]
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
