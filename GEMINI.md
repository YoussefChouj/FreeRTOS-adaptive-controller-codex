# Gemini / Antigravity entry point

The project rules are in `AGENTS.md` (single source, shared by all harnesses). Read it, then `docs/agent/HANDOFF.md`
(skip HANDOFF.md when the task names its files and needs no project state).
You are a worker unless the handoff prompt says supervisor.

## Reading discipline (every model, every task)

Every turn re-sends the whole conversation, so each file you open is paid for again on every later turn.

1. Search before you open. Use grep or find_by_name to locate the symbol, then view only that line range
   (about 80 lines around the hit). Open a whole file only when it is under 200 lines.
2. Never re-open a range you already read in this conversation. Use your notes.
3. Budget: at most 8 file views and 10 shell commands before you report back. If you reach the budget without an
   answer, stop and report what you know, what you ruled out, and which file or test you would read next.
4. Never read generated or bulk files: `OBJ/`, `Listings/`, `*.axf`, `*.hex`, `*.map`, `*.uvguix.*`, `docs/flights/`
   logs and plots, `*.csv`, `.agent-ops/logs/`, `node_modules/`.
5. Do not search the web unless the task asks for it.
6. Hardware bugs (motors, arming, radio, sensors) cannot be checked from code alone. Do not loop on them. Read the
   named path once, list the 2-3 most likely causes with file:line, and name the one bench check that tells them
   apart. Then stop.
7. Plan before you edit: state the files and the change in at most 5 lines, then make targeted edits
   (replace the lines, never rewrite whole files). Run the verify command the task gives you.
8. Answers are short: file:line references, no pasted file contents.
9. Exploration that spans more than 3 files goes to the `codebase-scout` subagent (invoke_subagent). It works in its
   own context and returns paths, line numbers and a short summary. Work from that summary; do not re-read those
   files in the main thread except the 1-2 ranges you will edit.
