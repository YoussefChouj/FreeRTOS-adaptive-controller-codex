param([Parameter(Mandatory)][string]$Id, [switch]$Merge)
# Supervisor check of one worker task: compact digest, independent test run, optional merge.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path (Split-Path $PSScriptRoot))
$br = "worker/$Id"
"== result (key lines)"
Get-Content ".agent-ops\tasks\$Id.result.md" -ErrorAction SilentlyContinue |
  Select-String -Pattern 'ROOT CAUSE|passed|failed|NOT RUN|SUBSTITUT|could NOT|TODO|DONE' | Select-Object -First 12 | % { $_.Line }
"== commits on $br"
git log --oneline "main..$br" | Select-Object -First 8
"== diff stat"
git diff --stat "main...$br" | Select-Object -Last 25
"== uncommitted in worktree"
git -C ".worktrees\$Id" status --short | Select-Object -First 10
"== full tree in worktree (independent run)"
Push-Location ".worktrees\$Id"
python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120 2>&1 | Select-Object -Last 4
$rc = $LASTEXITCODE
Pop-Location
"pytest rc=$rc"
if ($Merge -and $rc -eq 0) {
  git merge --no-ff -q $br -m "merge $br`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  if ($LASTEXITCODE -eq 0) { git push -q 2>&1 | Select-Object -Last 2; "MERGED+PUSHED" } else { git merge --abort; "MERGE CONFLICT - aborted" }
}
