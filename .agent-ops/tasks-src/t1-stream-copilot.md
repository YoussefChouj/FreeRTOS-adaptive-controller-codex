# Task T1: the dashboard gets no subscribed data after a service restart, and the co-pilot is silent

Repo root = your worktree. Read docs/research-platform/SPEC.md (context only).
Rules: do not contact the live service on 127.0.0.1:8081 (no GET, no POST). No firmware edits, no probe, no flashing.
Never print or cat API key values.

## Symptom A (measured live 2026-09-23 22:09 → 03:40)
The service was restarted at 22:09 at commit 86d7e42 while the drone was powered.
`/state` showed `connected=true` but `streams` was EMPTY for the whole session.
So the sidebar and panels had nothing to show. Before the restart, streams had data.
Hypothesis to verify or refute: in `ground_station/comm/wifi_bridge.py` the re-subscribe watchdog
`_check_resubscribe` only fires after a "boot subscribe" has happened
(see test `test_no_resend_without_a_boot_subscribe`). If the service starts while the FC is
already running, or the first subscribe is lost, nothing ever subscribes.
Also check how the service triggers the initial subscribe on start
(grep `_request_slot0_schema`, `subscribe`, `multi_slot`, `preset` in ground_station/comm and ground_station/service).
Fix: when frames arrive but no subscribed data has been received for `_RESUBSCRIBE_AFTER_S`,
send the subscribe even without a prior boot subscribe, rate-limited by `_RESUBSCRIBE_EVERY_S`.
Use the layout the service would use at start. Update and extend the tests in
ground_station/comm/tests/test_wifi_bridge_resubscribe.py (a fake clock; no network).

## Symptom B
The co-pilot never replies to operator notes. The key file `\\wsl.localhost\Ubuntu\home\youssef\.config\agent-keys.env`
is readable by the service and contains HETZNER_API_KEY and HETZNER_BASE_URL (names only).
Read ground_station/service/copilot.py and api.py (`build_copilot`, where operator notes trigger the co-pilot).
Find why no reply happens: the co-pilot not built, the trigger not wired, an exception swallowed, a thread not started, or a wrong model id.
Also make any failure VISIBLE: when the co-pilot is built, or fails to call the model, post one agent note
with the error class and message (no key material), rate-limited to one per minute.
Add a test using a fake HTTP opener proving that an operator note produces a model call and a reply note.

## Acceptance
- `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` is fully green (paste the last line).
- Commit on your branch with a message ending: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
- In your result file, give one ROOT CAUSE line per symptom with file:line evidence.
