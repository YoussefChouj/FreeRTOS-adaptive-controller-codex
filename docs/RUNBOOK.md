# Runbook — starting things

Run everything from the repo root (`FreeRTOS-adaptive-controller-codex`), Windows PowerShell.

## Dashboard service (port 8081)

```powershell
# Is it running, and since when?
Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" } |
  Select-Object ProcessId, CreationDate

# Start (foreground; Ctrl+C to stop)
python -m ground_station.service

# Stop a running one (only when the drone is disarmed / not flying)
Stop-Process -Id <ProcessId>
```

Open http://127.0.0.1:8081 in a browser.

Restart the service after pulling or committing service code; a process started earlier keeps
running the old code. Quick check that it has the agent layer: `GET /api/agent/control` must
return JSON, not `{"error": "file not found"}`.

Useful options (`python -m ground_station.service --help` for all):

| Option | Default | Use |
| --- | --- | --- |
| `--port` / `-p` | 8081 | listen port |
| `--wifi-host` | 192.168.4.1 | MicoAir AP address |
| `--wifi-port` | 14550 | UDP port |
| `--no-auto-subscribe` | off | do not send a subscribe on connect |
| `--preset NAME` | none | subscribe preset from `multi_slot_presets.yaml` |
| `--rtos-bridge` | off | poll RTOS stats over the probe |

## Tier-0 access switch (Approvals panel)

1. Approvals panel → set agent mode to **autonomous**.
2. **Grant full access** → confirm the dialog. Agents may now write tier-0 params without asking.
3. **Set partial access** to take it back. The setting is memory-only: every service restart
   resets it to partial.
4. Full access releases everything without prompts: tier-0 params, tier-1 → tier-0 flows
   (EKF-OF bias select, cmd 0x1E idx 0 value ≥ 2) and arming (`allow_agent_arm` turns on).
   Partial access turns `allow_agent_arm` back off; those flows then wait for you again.

## MCP servers (Claude Code only)

Defined in `.mcp.json`: `dashboard` (`python -m ground_station.service.agent_mcp`) and
`serena`. Claude Code asks once per project to approve them. The `dashboard` server needs the
service running on 8081 for live tools.

Other agents do not use `.mcp.json`:

- WSL workers (agy, opencode, Ark Claude): no MCP. They use the CLI instead,
  `python -m ground_station.agent_map explain <name>`, and must not POST to the service.
- The `serena` entry points at a Windows `.exe`; it cannot run from WSL.

## Co-pilot

The co-pilot is an LLM-powered assistant in the dashboard chat drawer.  When
enabled, every operator message posted to `/api/session/note` triggers a
background thread that asks an LLM for a reply, which is then posted as an
agent message.  The LLM sees a trimmed state snapshot (mode, running plan,
pending approvals) and the last ~20 conversation turns.

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `COPILOT_API_KEY` | _(none)_ | API key for the LLM provider. **Required** -- without it the co-pilot is off. |
| `COPILOT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | Model name passed to the API. |
| `COPILOT_BASE_URL` | `https://inference.hetzner.com/api/v1` | Endpoint base URL (must end with `/api/v1` for Hetzner, `/v1` for OpenRouter). |
| `COPILOT_OFF` | _(unset)_ | Set to any non-empty value to disable the co-pilot even when a key is present. |

### Turning it off

- **Environment**: set `COPILOT_OFF=1` before starting the service.
- **At runtime**: unset `COPILOT_API_KEY` or set `COPILOT_OFF` (requires a restart).

### Rate limiting

The co-pilot is rate-limited to **1 request per 6 seconds** (Hetzner Inference
allows 10 req/min).  If the operator sends messages faster than this, the
co-pilot replies "busy, try again" for subsequent messages in the same
6-second window.  At most 3 operator messages are queued; extras are dropped.

### Safety

- The co-pilot must **never** arm the drone, call probe/flash tools, or bypass
  operator approvals.
- It can only **propose** plans through the existing plan-creation path.
- The API key is never logged, echoed, or returned in any message.
