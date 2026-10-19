# Research: Antigravity SDK compaction hooks (read-only: web + local CLI help)

A pasted AI answer makes these claims. For EACH one answer CONFIRMED / NOT FOUND / CONTRADICTED, with a URL and a
short quote (under 15 words) as evidence.

1. Google Antigravity auto-compacts context at about 135k tokens, and then pauses to ask the user for confirmation.
2. A Python "Google Antigravity SDK" exists, with `from google.antigravity.hooks import on_context_compaction, pre_turn`,
   `from google.antigravity import AntigravityAgent`, `AntigravityAgent(project_path=...)`, `agent.run_task(...)`.
3. The compaction event has `event.inject_compaction_prompt(...)` and `event.auto_approve()`.
4. The pre-turn context has `context.total_tokens` and `context.set_tool_output_policy("summarized")`.

Sources the answer cites. Fetch these first:
- https://antigravity.google/blog/introducing-google-antigravity-sdk/
- https://ai.google.dev/gemini-api/docs/antigravity-agent
- https://discuss.google.dev/t/context-compaction-in-antigravity-the-delicate-balance-between-token-optimization-and-determinism-in-low-level-workflows/400021
- https://tokenade.net/en/articles/reduce-antigravity-token-usage

Also check PyPI for an official Antigravity SDK package. Report its name and version, and whether the symbols above
exist in its docs.

Local: `D:\Tools\agy\bin\agy.exe` is agy 1.3.5. Run `agy --help` (plus `agy help <cmd>` for at most 2 relevant
subcommands). Report any flag or settings.json key for each of these:
- a compaction threshold;
- a max-token or budget limit;
- resuming or continuing a headless `-p` run by its conversation_id.

Do not scan the binary.

Answer in at most 30 lines, then give a one-line verdict.
