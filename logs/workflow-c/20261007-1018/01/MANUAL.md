# Step A truth roam: one command (PowerShell or Git Bash, offline)

From the repo folder, phone filming LANDSCAPE first:

```
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepa-roam_20261007-1021.yaml --pack P4000-1 --run logs/workflow-c/20261007-1018
```

It starts the service in its own window if 8081 is down, prints the preflight (FAIL = fix and press Enter), waits
for you to arm by RC and type `go`, sends GO (the campaign log plan records everything at 100 Hz), prints each phase,
then runs the debrief after landing.

| stop | how |
|---|---|
| land | Ctrl+C once |
| abort | Ctrl+C twice |
| kill | RC ch10 (always first choice) |

Do not use `curl` in PowerShell: it is Invoke-WebRequest there.
