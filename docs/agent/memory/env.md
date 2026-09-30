# Environment facts (this laptop + workers)

Migrated 2026-09-30 from Claude-private memory. Re-verify before relying on anything time-bound.

## Machine
- Windows 10, Acer A315-53G, i5-8250U, 12 GB RAM. Battery ~3.5% health, AC flaps under load (CPU turbo off on AC,
  `.wslconfig processors=4`, worker cap 2).
- WSL2 Ubuntu at `D:\WSL\Ubuntu`, user `youssef`. No mirrored networking.
- Behind Clash Verge proxy `127.0.0.1:7897`. WSL reaches Google via `/etc/profile.d/winproxy.sh`. Chinese sites are direct.
  Local tools that call 127.0.0.1 must bypass the proxy (`NO_PROXY=127.0.0.1` or an empty ProxyHandler).
- Windows tools (Python, pyOCD, UV4) are only reachable from WSL via `.agent-ops/win.sh "<cmd>"`.

## Hardware access
- pyOCD/SWD: use `--transport swd` when the dashboard holds UDP 14550.
- VOFA Studio shares UDP 14550 and the FC subscribe slots with the 8081 service: do not start 8081 while the operator is
  streaming or flying.

## Workers (full table: `.agent-ops/WORKERS.md`)
| Kind | Backend | Pool | Note |
|---|---|---|---|
| agy | Antigravity CLI (local, WSL, VPS) | Google accounts; Gemini pool and Claude+GPT pool are separate | Gemini first |
| ark | Volcengine Agent Plan via `claude` binary | Metered; weekly quota | `deepseek-v4-flash`, not glm (9x cost) |
| oc | opencode | Free Hetzner Qwen / OpenRouter :free | 10 and 20 req/min limits |
| Claude subagents | Agent tool in Claude Code | SAME plan quota as the main session | 429s arrive together with the main limit |

- Paid pools were exhausted 2026-09-23; ark reset 2026-09-28. Re-measure before assuming.
- VPS: `ssh openclaw` (root), Hetzner 2 vCPU / 3.7 GB. agy runs there under a secondary Google account (default lane).
- Keys live in the WSL `agent-keys.env`, never in the repo.
