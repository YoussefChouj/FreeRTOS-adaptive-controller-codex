# Handoff DRY RUN — do not change any code

This is a test of the Claude -> agy handoff. Do only this:
1. Read `.agent-ops/watchdog/agy-handoff-prompt.md` and `.agent_state/overnight-queue.md`.
2. Write `.agent_state/agy-dryrun.txt` containing: the word READY, the first queue item not marked DONE,
   and in one line each the three hard limits you must never break.
3. Stop. Do not edit any other file, run no build, flash, test, or drone command.
