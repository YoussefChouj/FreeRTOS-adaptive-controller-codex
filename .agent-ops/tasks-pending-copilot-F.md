# Task F: co-pilot reply backend (2026-09-22)

Problem: the dashboard co-pilot drawer never answers. Operator messages only reach an
agent that loops on the MCP tools `wait_for_operator`/`say`
(`ground_station/service/agent_mcp.py`), and usually no agent is running.

Build a deep module `ground_station/service/copilot.py`:
- Interface: `Copilot(llm, agent_state_fn, say_fn)` plus `handle(message: str) -> str`.
  Everything else stays internal: prompt building, state snapshot trimming, history
  (last ~20 turns), token cap, errors.
- LLM seam with two adapters: `OpenAILLM` (stdlib urllib, OpenAI-compatible
  `POST {base}/chat/completions`, `Authorization: Bearer <key>`, 30 s timeout) and `FakeLLM`
  for tests. Target: Hetzner Inference (base `https://inference.hetzner.com/api/v1`,
  model `Qwen/Qwen3.6-35B-A3B-FP8`, 10 req/min, so rate-limit to at most 1 request per
  6 s and answer "busy, try again" rather than queueing). The same adapter must also work
  with OpenRouter. Honour HTTPS_PROXY (urllib does by default).
  Read the key from env `COPILOT_API_KEY`, the model from `COPILOT_MODEL` (default above)
  and the base URL from `COPILOT_BASE_URL` (default above). Never log, echo
  or return the key. If no key is set, the co-pilot is off and says so once in the chat.
- Wiring: when an operator chat message arrives in the service, call `handle` on a
  background thread (never block the HTTP handler) and post the reply as an agent
  message through the existing say/message path. A minimal hook in `agent.py` is
  allowed; keep it to a few lines. Do not answer your own agent messages (no loops).
  Rate limit to one in-flight request, and queue at most 3 messages.
- Scope of the model's power: it answers questions from the state snapshot
  (`get_state`-equivalent data, plan list, mode). It may PROPOSE a plan only through the
  existing plan-creation code path, so the mode/tier/approval rules apply unchanged. It
  must not bypass approvals, must not arm, and must not call probe/flash.
- Add a CLI flag or env to disable it: `--no-copilot`.

Tests (FakeLLM, no network): reply is posted; operator echo is not re-answered; no key
means off; an exception in the LLM gives a short error message in chat; the key never
appears in any message or log. Doc: add a "Co-pilot" section to `docs/RUNBOOK.md`
(env vars, off switch).

Do NOT edit firmware. Do NOT POST to the live 8081 service. Do NOT make real network
calls in tests. Keep other uncommitted edits. Verification: `python -m pytest
ground_station/service -q` green except the known flake
(test_mode_off_cancels_running_plan_and_returns_423); paste the final line verbatim.
Exit cleanly when done.
