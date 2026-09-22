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
