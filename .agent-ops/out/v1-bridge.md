STATUS: done

FILES CHANGED:
- .agent-ops/vps-bridge.sh: polling bridge script (<80 lines) mirroring VPS worker logs and transitions to state.log
- .agent-ops/monitor-window.ps1: accept [\w-]+ worker ids, launch vps-bridge.sh hidden via Git Bash, kill on exit
- tests/agent_ops/test_vps_bridge.sh: integration test for vps-bridge.sh covering all transitions, failure modes, idempotency, and pure-function parsing
- tests/agent_ops/test_vps_bridge.py: pytest test runner invoking test_vps_bridge.sh

VERIFICATION:
- ./tests/agent_ops/test_vps_bridge.sh: 26 passed, 0 failed
- pytest tests/agent_ops/test_vps_bridge.py: 1 passed in 0.18s
- bash .agent-ops/tests/test_netdown_regex.sh: passed (all cases OK)
- wc -l .agent-ops/vps-bridge.sh: 72 lines (spec requirement: <80 lines)
- git status check: only target files touched

OPEN QUESTIONS / RISKS:
- Laptop environment must have C:\Program Files\Git\bin\bash.exe for automatic bridge launch (handled silently if absent)
- SSH host oc-agent must be accessible in ~/.ssh/config for remote polling
- Windows monitor UI not directly verified on headless Linux VPS

SUBSTITUTIONS: none
