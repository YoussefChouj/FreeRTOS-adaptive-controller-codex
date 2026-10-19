You are a RESEARCH worker. Today is 2026-10-01. No code changes. Do not touch firmware, the 8081 dashboard or any secrets.

## Context (measured by the supervisor; do not re-check)
- Project: STM32F4 + FreeRTOS quadrotor firmware in C (Keil build, compile_commands.json present), plus a Python ground station and pytest suite. The user is a researcher, so experiments matter more than plumbing.
- Seats:
  - CEO: Claude Opus, desktop, Pro plan A. Plans and decides.
  - Manager: Claude Opus CLI, Pro plan B. Dispatches, reviews, commits.
  - Workers: Gemini via Antigravity CLI (agy), Qwen3.6-35B-A3B via opencode, and deepseek-v4-flash.
- Measured pain:
  - Worker diffs often get rejected, and the expensive Opus seat then rewrites them inline. Example: G7 was rejected and rewritten inline, and the G5+G6 worker run was rejected too.
  - One manager work package (41 turns) used a whole 5-hour Pro window.
  - Plan A's weekly limit is at 77%.

## Questions (answer each with sources)
1. **Hierarchical multi-agent orchestration for coding: the state of the art in 2025–2026.**
   - Which roles and hierarchy work, and when does multi-agent hurt?
   - What are the token-cost multipliers?
   - Primary sources first: Anthropic's engineering posts ("Building effective agents", "multi-agent research system", the Claude Code best-practices and subagent docs) and Cognition's "Don't build multi-agents".
   - Then OpenAI Codex / agents guidance, and papers: MetaGPT, ChatDev, AgentCoder, MapCoder, SWE-agent, Agentless, OpenHands, and recent surveys.
2. **Raising first-pass quality from cheap worker models.** Give evidence for each technique:
   - spec-driven development (GitHub Spec Kit, Kiro);
   - acceptance tests written first by the strong model;
   - the architect/editor split (Aider's published benchmark numbers);
   - small diffs;
   - lint, type and static-analysis gates;
   - mutation testing;
   - LLM reviewer vs deterministic checks;
   - best-of-N.

   Which roles deserve a separate agent, and which are better as a script?
3. **Routing by capability and difficulty.**
   - Evidence from RouteLLM, FrugalGPT and cascades.
   - Published benchmark standings (SWE-bench Verified, Aider polyglot, etc.) for gemini-3.1-pro, gemini-3.8-flash, Qwen3.6-35B-A3B, deepseek-v4-flash and Claude Opus. Only numbers you can source; otherwise write UNVERIFIED.
4. **LLM code quality in embedded or RTOS C.** Evidence and practices:
   - MISRA, cppcheck, clang-tidy;
   - host-side unit tests (Unity/CMock/Ceedling);
   - hardware-in-the-loop (HIL) testing.
5. **Code-navigation tools for agents.** Compare serena (oraios) with:
   - Claude Code's native LSP plugins (clangd-lsp, pyright-lsp);
   - opencode's built-in LSP;
   - ast-grep;
   - Aider's repo-map and ctags;
   - plain grep.

   Cover accuracy and token savings, Windows issues, startup time and dashboard/tray settings. Say which tools work from Gemini CLI/Antigravity and from opencode.
6. **Long-term maintainability of AI code.**
   - GitClear's churn and duplication reports, the DORA 2025 AI report, METR's 2025 developer study.
   - Proven mitigations.

## Rules
- Each claim gets a quote under 15 words plus its URL. If you cannot find a source, write UNVERIFIED. Never invent numbers.
- Prefer primary sources (vendor docs and blogs, papers, repos) over SEO blogs.
- **The previous attempt was REJECTED.** 10 of its 11 URLs were invented, for example `arxiv.org/llm-misra-c` and `aider.chat/architect-editor`.
  - Open every URL with your web fetch/read tool BEFORE citing it, and copy the exact URL from the fetched page.
  - The supervisor will curl every URL. Any 404 means the whole report is rejected.
  - Aim for at least 25 distinct fetched sources.
- Add an appendix "Fetched sources": one line per URL, with the page title as fetched.

## Deliverables (commit both on your branch)
1. `docs/agent/research/orchestration-2026-10.md`, at most 250 lines:
   - one section per question;
   - then "Recommended setup for this user": a roles table (seat, model, does, never does), the work flow as an arrow diagram, quality gates, routing rules, and the code-navigation pick.
2. `.agent-ops/out/res-orch.md`, at most 40 lines: the digest. Put the recommendation and the 8 strongest sourced findings in it.
