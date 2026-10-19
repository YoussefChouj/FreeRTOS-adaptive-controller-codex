# win-agy.ps1 - launch a headless Windows agy worker in a git worktree (Claude/GPT quota pool).
param([Parameter(Mandatory=$true)][string]$Id, [string]$Model = "claude-sonnet-4-6", [string]$Effort = "")
$root = Split-Path -Parent $PSScriptRoot
$wt = Join-Path $root ".worktrees\win-$Id"
$brief = Join-Path $root ".agent-ops\tasks-src\win-$Id.md"
$common = Join-Path $root ".agent-ops\tasks-src\win-common.md"
$out = Join-Path $root ".agent-ops\out\win-$Id"
$prompt = "Your working directory is the git worktree $wt (branch win/$Id). Read the task brief $brief and the rules $common, then do the task fully and autonomously. Write your digest to $out.md in the MAIN tree path given (create it), then commit your work on branch win/$Id. When finished, print DONE."
$p = Start-Process -FilePath "D:\Tools\agy\bin\agy.exe" -WorkingDirectory $wt -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$out.out" -RedirectStandardError "$out.err" `
  -ArgumentList ((@("--dangerously-skip-permissions", "--model", $Model) + $(if ($Effort) { @("--effort", $Effort) } else { @() })) + @("--add-dir", "$root\.agent-ops", "-p", "`"$prompt`""))
"$Id pid $($p.Id) model $Model"
