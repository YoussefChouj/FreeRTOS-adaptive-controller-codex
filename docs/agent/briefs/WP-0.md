You are the MANAGER (account B, headless). Spec: docs/agent/ceo-manager-architecture.md. Today: 2026-10-01.

Goal: a usage meter, so that both seats see `ctx 110k/150k · 5h 10% · wk 77% (acct A|B)` without spending a tool call.

Facts the CEO measured on 2026-10-01:
- Desktop (account A) has the MCP tool `mcp__ccd_session_mgmt__get_usage`. It returns the plan windows (5-hour and weekly %, reset time) and the context size. The CLI does not have this tool.
- `%APPDATA%/Claude/plan-usage-history.json` holds desktop-app samples. They are keyed per org: A = `c59b21…`, B = `bbe3f1…`. Field `fh` = 5-hour %, `sd` = weekly %. Samples are ~15 min apart. B's last known sample is 09-30 19:05, so its freshness for B is unknown.
- The existing meter is `C:/Users/Acer/.claude/scripts/context_meter.py` (242 lines). Its modes are resume, hook, posttool, precompact and statusline, all wired in `~/.claude/settings.json`.
- The desktop sets env `CLAUDE_CODE_ENTRYPOINT=claude-desktop`. The CLI value is unknown; find out.

Tasks:
1. Find how the CLI, logged into account B, can read its own 5-hour and weekly %. Candidates: the fields in the statusline stdin JSON (official docs: code.claude.com/docs/en/statusline), `/usage`, and local files. Prove it: give a doc quote under 15 words plus URL, or a local test with raw output. Do not guess.
2. Write `tools/usage_report.py` in this repo, plus pytest tests that use a small fixture. It must print, per account:
   - the latest 5-hour % and weekly % and the age of that sample;
   - the first-request base context of the last 5 session transcripts in `~/.claude/projects/<proj>/*.jsonl` (input + cache_creation + cache_read of the first assistant `usage`).
3. Write a PROPOSED patch that makes context_meter.py's hook and statusline lines also show the 5-hour % and weekly % for the active account. Save it as `docs/agent/reports/WP-0-context_meter.patch`. Do NOT edit anything under `~/.claude`.

Acceptance:
- `python tools/usage_report.py` prints both orgs.
- Your new tests pass; run only those tests.
- The report says which data source works for B, with evidence.

Scope: this git worktree only (branch wp/0-usage-meter). Commit there.

Forbidden:
- flashing firmware, the 8081 dashboard, motors;
- edits under `~/.claude`, auto-memory;
- git push, the main branch;
- printing tokens or credentials.

Lane: small task, do it inline; no workers needed. Effort: high. Max 40 turns.

Report to: `docs/agent/reports/WP-0.md`, at most 40 lines, in this format:
Status: DONE | PARTIAL | BLOCKED
Commits: <sha — subject>
Verification: <command> -> <raw pass/fail line>
B-side limits source: <what works, with evidence>
Deviations / open questions:
