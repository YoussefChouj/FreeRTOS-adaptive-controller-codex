# argon.ps1 - the only manager lane since 2026-10-17: Windows agy, Gemini 4 Argon (High), plan mode (settings.json defaults).
# Research and second opinions ONLY: no worktree, no commits, no edits. The CEO builds everything inline.
# Ceiling: CapK thousand tokens for the run and every subagent it spawns (GEMINI.md rule 10). agy has no settable
# token cap, so the wrapper enforces it twice: the prompt states the budget, and the timeout kills a runaway run.
# usage.* in the JSON covers the main thread only (subagent tokens are not reported). Smoke 10-17: one grep question
# = 63k input + 55k cache-read in 52 s, so keep each brief to one narrow question.
# Example: powershell -File .agent-ops/argon.ps1 -Id res-lqr -Brief .agent-ops/briefs/res-lqr.md
param([Parameter(Mandatory=$true)][string]$Id, [Parameter(Mandatory=$true)][string]$Brief,
      [int]$CapK = 160, [int]$TimeoutMin = 20)
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root ".agent-ops\out\argon-$Id"
$prompt = "Read the brief $Brief and answer it. Read-only: do not edit files or commit. Token ceiling: $CapK K tokens in " +
  "total for you and every subagent you spawn; before you reach it, stop and answer with what you have. Answer in at most " +
  "40 lines: findings with file:line or quoted sources (URL + short quote), then a one-line verdict."
$p = Start-Process -FilePath "D:\Tools\agy\bin\agy.exe" -WorkingDirectory $root -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$out.json" -RedirectStandardError "$out.err" `
  -ArgumentList @("-p", "`"$prompt`"", "--output-format", "json")
if (-not $p.WaitForExit($TimeoutMin * 60000)) { $p.Kill(); "$Id KILLED after $TimeoutMin min (over time = over budget)"; exit 2 }
try { $j = Get-Content "$out.json" -Raw -Encoding utf8 | ConvertFrom-Json } catch { "$Id no JSON, see $out.err"; exit 3 }
$u = $j.usage
# the cap counts fresh tokens; cache reads (the re-sent prefix) are shown but not counted
$used = [int]$u.input_tokens + [int]$u.output_tokens + [int]$u.thinking_tokens
$flag = if ($used -gt $CapK * 1000) { "OVER-CAP" } else { "ok" }
$j.response | Out-File "$out.md" -Encoding utf8
"$Id $($j.status) $flag used=$used (in $($u.input_tokens) cache $($u.cache_read_tokens) out $($u.output_tokens)) " +
  "turns=$($j.num_turns) $([int]$j.duration_seconds)s conv=$($j.conversation_id) -> $out.md"
