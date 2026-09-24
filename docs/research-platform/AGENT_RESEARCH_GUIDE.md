# Agent Research Guide: the dashboard agent doing research here

## Autonomy levels

| Level | What the agent may do                          | Approval needed |
|-------|------------------------------------------------|-----------------|
| L0    | Observe: read files, grep, run tests           | none            |
| L1    | Bench: run simulations, dry-runs, analysis     | none            |
| L2    | Approved workflow: one YAML workflow in library| none (pre-approved) |
| L3    | Campaign: pick points inside operator envelope | per-envelope    |

## Workflows

Workflows are YAML files in `research/workflows/`. Each has typed steps:

| Step type      | What it does                         |
|----------------|--------------------------------------|
| `set_params`   | write firmware parameters            |
| `fly_trajectory`| fly a trajectory preset or family   |
| `capture`       | start telemetry recording            |
| `wait_until`    | wait for telemetry predicate         |
| `analyze`       | run core metrics plugin              |
| `revert`        | restore parameters (try/finally)     |
| `note`          | leave an operator note               |
| `call`          | invoke another workflow              |

Agent-written workflows must pass:
1. Schema validation (ground_station.research.workflow)
2. Sim dry-run (sim/plant, sim/replay)
3. Operator approval into the library

## Dry-run first

Never run a workflow on hardware without a passing sim dry-run.
`python -m ground_station.research workflow dryrun <path>` executes the full
workflow through the sim harness and stores a validation Run tagged `sim`.

## Envelopes

L3 campaigns operate inside operator-approved parameter envelopes. The agent
proposes points inside the envelope via `python -m ground_station.research
campaign plan <envelope.json>`. Envelope membership is checked by
`campaign_is_within_envelope()` before any point is submitted.

## Findings

Durable findings go in `docs/research-platform/findings/*.md`. Each file has
frontmatter (id, date, severity, status, runs, summary) and a Markdown body
with Evidence and Suggested Action.

CLI:
```
python -m ground_station.research finding new F-001 --severity high --summary "..."
python -m ground_station.research finding list
```

## Safety: never flash while armed

- Check `status.arm` before any firmware change. If `armed`, abort.
- The opencode.json permission policy blocks `flash*`, `rebuild_and_flash`,
  and `livewatch*` commands by default (ask=intercept).
- The supervisor owns the hardware; only the operator flashes.

## Workflow library

Regenerate `CATALOG.md` from the workflow library:
```
python -m ground_station.research catalog
```

The catalog lists all steps, their signatures, and the action registry.

## Thesis link

Every Run carries `phase` and `hypothesis`. Results tables are queries over
the index. Run `python -m ground_station.research query 'phase=?' 'phase0'`
to list Phase-0 runs.

## Parameters

Physical constants live in `sim/plant.py` and `docs/sysid_results.md`.
Never copy numbers from memory -- reference the originals.

## Shell

The terminal panel (`Terminal` tab) runs `opencode` by default in a PTY.
Set `TERMINAL_AGENT=claude` to switch to Claude Code.
Token is required; set it in the panel or via `sessionStorage`.
