# CEO research notes

Moved out of `ceo-manager-architecture.md` on 2026-10-14 so the rules doc stays short. Background only: the rules live there.

## Measured 2026-10-14 (desktop CEO session)

- Base context: 57.0k on the first request, before the trim below.
- Auto-compact fires at 117.6k–125.6k (preTokens of past `compact_boundary` entries), with rare overshoots to 132–147k. The 150k rule was unreachable; thresholds are now 90k / 110k.
- Applied: 73 `skillOverrides` (keys are skill *directory* names), mattpocock plugin off (`sync_mp_skills.ps1` still installs new skills and reports upstream changes), `permissions.deny: ["Agent"]`, serena disabled for this project, parked-skill index at `~/.claude/skills/CATALOG.md`.
- Still to toggle by hand: synced plugins (Customize → Plugins) and the Claude Docs and MDN connectors. Re-measure with `/context` in a new session.

## A. Trimming the base context (was section 4)

Measured contributors are the first-request totals above. The per-item split is **not measured yet**. Run `/context` in an interactive `claude` terminal to get it, then re-run the first-request script after each change.

Observed in the desktop system prompt, from biggest suspected cost to smallest:

1. **Synced claude.ai plugins.** These are not in `settings.json`; they live in `~/.claude/plugins/synced/…`. There are 17 plugins with about 110 skills and 16 agent types: brightdata (21 skills), skipper (27 skills and 10 agents, Next.js/Supabase/React Native), paper-review-system (bilingual agent examples), design, engineering and productivity (12 connectors awaiting OAuth), firecrawl, desktop-commander, spanner, browser-use, superpowers, napkin-math and more. Disable them in the desktop app's plugin settings (Customize → Plugins), except the few you use weekly.
2. **Superpowers SessionStart injection.** A full "using-superpowers" skill is injected into every session, and it pushes extra skill loads.
3. **Three memory systems at once.** Auto-memory (32 files, index loaded every session), the remember plugin (`.remember/`, 122 KB, SessionStart injects a preview plus `now.md`), and repo `HANDOFF.md`/`.claude_state.md`. Per *repo-is-source-of-truth*, keep the repo files and a short MEMORY.md index, and disable the remember plugin.
4. **Duplicated skills.** `mattpocock-skills` duplicates local diagnosing-bugs, grilling, codebase-design, domain-modeling and code-review.
5. **User-scope MCP `paper-search`.** It registers 60 tools, and arxiv already covers most of that. Move it to project scope in the paper projects, or remove it.
6. **Desktop-only surfaces.** Built-in browser tools plus their safety policy, computer-use, Claude-in-Chrome, the Claude Docs connector, and about 250 deferred tool names. This is most of the desktop-vs-CLI gap. Turn off the connectors the Code tab does not need.
7. **Explanatory output style.** It adds an insight block to every reply, which costs output tokens. Use the default style for the CEO, and switch to Explanatory only when you want teaching.

For the Manager, trim at invocation instead of editing shared config: `--disable-slash-commands` (no skill listing) and `--strict-mcp-config` (no MCP). Note that `--bare` is **not** usable, because it requires an API key and skips OAuth.

Shared `~/.claude`: A and B share settings, hooks, skills and auto-memory. That is good for consistent rules. The risk is two writers on the memory files, so only the CEO writes auto-memory. A separate `CLAUDE_CONFIG_DIR` for B is the fallback if that becomes a problem.

## B. Research findings, account-B manager run (was section 6)

The run: `claude -p --model opus --effort medium --disable-slash-commands --strict-mcp-config --allowedTools WebSearch WebFetch`, launched headless from the A desktop session on 2026-10-01.
- Result: rc=0, 17 turns, 107 s.
- Usage: input 14, cache_creation 95,827, cache_read 325,568, output 6,564.
- The first request was **27,647 tokens**, against 51–55 k for a default CLI start. The two trim flags halve B's base.

Findings are paraphrased. Each links its source; UNVERIFIED means the manager found no official source.

**Terms**
- The [consumer terms](https://www.anthropic.com/legal/consumer-terms) forbid scripted or automated access to the Services, except via an API key or where Anthropic explicitly permits it. Claude Code's [authentication docs](https://code.claude.com/docs/en/authentication) document `claude setup-token` for "CI pipelines, scripts" under a subscription. So headless `claude -p` for your own use is a documented path.
- The subscription login may not be embedded in third-party products ([Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)). Login credentials may not be shared.
- **One person holding two paid subscriptions: no clause found either way.** The [Usage Policy](https://www.anthropic.com/legal/aup) mentions a different account only as a way to *get around a ban*. It has no clause on usage limits. This was checked by CEO WebFetch on 2026-10-01. It is the user's call; re-read both pages before relying on it.

**How limits count** ([costs](https://code.claude.com/docs/en/costs), [support 11145838](https://support.claude.com/en/articles/11145838))
- Claude and Claude Code draw on one pool per account.
- Cache reads still count, at the cached-token rate. On a subscription the cache lives about 1 h. It drops to 5 min when you draw on usage credits.
- Thinking is billed as output. On Opus 5.5 thinking cannot be switched off, so effort is the knob.
- The 5 h and weekly limits are shared across models. Switching model only helps after an Opus-specific limit message.
- Exact cached/input/output weights for Pro: UNVERIFIED.

**Two accounts on one PC** ([authentication](https://code.claude.com/docs/en/authentication))
- The official way to separate CLI accounts is `CLAUDE_CONFIG_DIR` per account. On Windows the credentials sit in `.credentials.json` under that directory.
- The Desktop app keeps its own login and ignores those environment variables, so A (desktop) and B (CLI) do not collide.
- `CLAUDE_CODE_OAUTH_TOKEN` overrides `/login` in every new session. Never set it in the user profile.
- A stray `ANTHROPIC_API_KEY` beats the subscription.
- Pitfall ([claude-code#98526](https://github.com/anthropics/claude-code/issues/98526), macOS): the skills-sync bucket is keyed from the shared `~/.claude.json`, so two accounts overwrite each other's synced skills. This matches what we see here: the synced-plugin folder is named after B's org (`bbe3f1…`), yet those plugins load in A's desktop sessions.

**Orchestration patterns**
- Headless `-p` + `--resume <session_id>` + JSON output: the chosen transport (ceo-manager-architecture.md section 3).
- Subagents keep verbose output out of the parent's context, but they bill the same plan.
- Agent teams cost roughly 7× the tokens of a normal session. Wrong tool for stretching limits.
- claude-squad needs tmux, which on Windows means WSL. ccmanager also drives Gemini CLI and opencode, so it could suit the worker lanes; its per-account config is UNVERIFIED.
- Handoff format: no official guidance. Files on disk remain the recommendation: they survive `/clear`, and they cost the CEO only the report.

**Trimming** ([costs](https://code.claude.com/docs/en/costs), [skills](https://code.claude.com/docs/en/skills), [CLI reference](https://code.claude.com/docs/en/cli-reference))
- Run `/context` for the per-item split and `/usage` for per-plugin, per-skill and per-MCP spend.
- MCP tool definitions are deferred by default: only names ship until a tool is loaded. That lowers the cost of item 5 in section A to a name list, not 60 schemas.
- Keep CLAUDE.md under about 200 lines.
- The skill listing gets about 1 % of the context window. `disable-model-invocation: true` removes a skill from the listing.
- Compaction is itself a large request; `/clear` costs nothing. Prefer clearing at task boundaries, with state kept in repo files.
- Token cost of output styles: UNVERIFIED.

**Observed in the A session after compaction (2026-10-01)**
- 12 synced-plugin connectors were waiting for OAuth: figma, intercom, asana, atlassian, datadog, github, linear, notion, pagerduty, slack, clickup, monday.
- 6 servers failed to connect: browser-use, desktop-commander, academic-search, paper-parser, spanner, serena.
- The full `using-superpowers` skill was re-injected at SessionStart.
- All of this is further evidence for items 1–2 of section A.
