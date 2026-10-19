Research brief res-jules-orch (read-only: no edits, no commits). Answer in <= 120 lines, quote every source
(URL + a short quote under 15 words). Mark anything you could not confirm as UNCONFIRMED.

Context: Claude (CEO) delegates coding tasks to Google Jules through `.agent-ops/jules.ps1` (read it: it creates
sessions with `requirePlanApproval = false` and `automationMode = AUTO_CREATE_PR`, then polls and sends replies with
`sessions/{id}:sendMessage`). Each task took 2-7 review rounds: Claude reviews the PR, writes a fix list
(example: `.agent-ops/briefs/j-nn-replay-reply2.md`), and Jules sometimes stops in AWAITING_USER_FEEDBACK to ask.
The user wants Gemini (you) to run that loop, with Claude writing only the first spec plus acceptance tests.

1. Jules API (jules.googleapis.com, v1alpha) and docs (jules.google, developers.google.com/jules): every session field
   and state. Is there any setting that stops Jules pausing for feedback, or that makes it ask only when blocked?
   What does Jules read automatically (AGENTS.md? setup script / environment snapshot?) and how do you tell it which
   test command must pass before it pushes? Does Jules react to GitHub PR review comments or CI results?
2. Google's own guidance for Jules prompts that cut round trips (task size, acceptance criteria, examples).
3. Current best practice for orchestrator / manager / worker coding agents: orchestrator-workers and
   evaluator-optimizer loops, test-first delegation (tests as the contract), and guards against a worker gaming the
   tests (protected test files, hidden tests, diff checks). Prefer primary sources (Anthropic, Google, OpenAI
   engineering posts, papers). Name the 3 failure modes that show up most often.
4. Recommend a concrete loop for this repo: who writes what, the stop rules (max rounds, when to escalate to
   Claude), the done gate (commands that must pass: scoped pytest, `ruff check`, `python tools/host_tests.py`
   26/26, CRLF), and what must stay with Claude (final review, merge). Keep it to one table plus 10 lines.
