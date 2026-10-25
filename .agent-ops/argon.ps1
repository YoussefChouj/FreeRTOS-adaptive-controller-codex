# argon.ps1 - the only manager lane since 2026-10-17: Windows agy, Gemini 4 Argon (High), plan mode (settings.json defaults).
# Research and second opinions ONLY: no worktree, no commits, no edits. The CEO builds everything inline.
# Ceiling: CapK thousand tokens for the run and every subagent it spawns (GEMINI.md rule 10). agy has no settable
# token cap, so the wrapper enforces it twice: the prompt states the budget, and the timeout kills a runaway run.
# usage.* in the JSON covers the main thread only (subagent tokens are not reported). Smoke 10-17: one grep question
# = 63k input + 55k cache-read in 52 s, so keep each brief to one narrow question.
# Network: a VPN node timeout ends an agy run with status ERROR "There was a network issue connecting to the server"
# after ~640 s, and the work was lost (2026-10-17: 3 runs). Now the wrapper waits for the proxy first (net-wait.ps1),
# and after a network ERROR it waits again and resumes the SAME conversation (--conversation), up to NetRetries times.
# TimeoutMin counts agy run time only, not network waits; the cap counts the tokens of every attempt.
# TimeoutMin must exceed agy's own subagent wait: an idle root agent prints "waiting up to 30m0s for 1 background
# task(s)" (argon-n25-sim.err, 2026-10-25), and the old 20 min killed those runs mid-wait. 60 is a chosen value (PROPOSED).
# Google answers "User location is not supported" when the VPN exit is in a region it rejects (3 runs, 2026-10-25
# 01:23-01:43); that is not a network drop, so no retry: exit 8, fix the Clash AI-exit node.
# Exit: 0 answered, 1 agy ERROR, 2 killed (over time), 3 no JSON, 7 no network, 8 VPN exit region rejected.
# Example: powershell -File .agent-ops/argon.ps1 -Id res-lqr -Brief .agent-ops/briefs/res-lqr.md
param([Parameter(Mandatory=$true)][string]$Id, [Parameter(Mandatory=$true)][string]$Brief,
      [int]$CapK = 160, [int]$TimeoutMin = 60, [int]$NetRetries = 3, [double]$NetMaxMin = 30)
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "net-wait.ps1")
# Exit-node log (2026-10-25): the Clash AI-exit node each run used, read from the last "AI:" line of the vpn_monitor
# log (one line a minute). exit=8 rows name nodes Google rejected, exit=0 rows nodes it accepted.
$monLog = Join-Path $env:USERPROFILE ".vpn_monitor.log"
function Get-AiNode {
  $m = Get-Content $monLog -Tail 5 -Encoding utf8 -ErrorAction SilentlyContinue | Select-String "AI: .*?-> (.+?) \|" |
    Select-Object -Last 1
  if ($m) { $m.Matches[0].Groups[1].Value } else { "unknown" } }
$node0 = Get-AiNode
function Fin([int]$code) {
  "$(Get-Date -Format 'yyyy-MM-dd HH:mm') $Id exit=$code start=$node0 end=$(Get-AiNode)" |
    Out-File (Join-Path $root ".agent-ops\outrgon-nodes.log") -Append -Encoding utf8
  exit $code }
$out = Join-Path $root ".agent-ops\out\argon-$Id"
$prompt = "Read the brief $Brief and answer it. Read-only: do not edit files or commit. Token ceiling: $CapK K tokens in " +
  "total for you and every subagent you spawn; before you reach it, stop and answer with what you have. Answer in at most " +
  "40 lines: findings with file:line or quoted sources (URL + short quote), then a one-line verdict."
$resume = "The network dropped during your last turn. Continue the same brief ($Brief) from where you stopped, under " +
  "the same rules and token ceiling, and give the final answer."
# agy's own wording, plus the Node and gRPC codes a dropped proxy surfaces as
$netErr = "network issue|ECONN|ETIMEDOUT|EAI_AGAIN|fetch failed|socket hang up|UNAVAILABLE|deadline exceeded"
if (-not (Wait-Net -MaxMin $NetMaxMin -Tag $Id)) { "$Id NO-NET: no network for $NetMaxMin min, agy not started"; Fin 7 }
$agyArgs = @("-p", "`"$prompt`"", "--output-format", "json")
$runMs = 0; $in = 0; $cache = 0; $outTok = 0; $think = 0; $turns = 0; $secs = 0
for ($n = 0; ; $n++) {
  $f = if ($n) { "$out.r$n" } else { $out }     # every attempt keeps its own .json/.err
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $p = Start-Process -FilePath "D:\Tools\agy\bin\agy.exe" -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$f.json" -RedirectStandardError "$f.err" -ArgumentList $agyArgs
  if (-not $p.WaitForExit([Math]::Max(0, $TimeoutMin * 60000 - $runMs))) {
    $p.Kill(); "$Id KILLED after $TimeoutMin min of agy run time (over time = over budget)"; Fin 2 }
  $runMs += $sw.ElapsedMilliseconds
  $j = $null
  try { $j = Get-Content "$f.json" -Raw -Encoding utf8 | ConvertFrom-Json } catch { }
  $errText = if ($j) { "$($j.error)" } else { (Get-Content "$f.err" -Raw -ErrorAction SilentlyContinue) + "" }
  $isNet = ((-not $j) -or $j.status -eq "ERROR") -and $errText -match $netErr
  if ($errText -match "location is not supported") {
    "$Id GEO-BLOCK: Google rejected the VPN exit region, pin Clash AI-exit to a tested node, see $f.err"; Fin 8 }
  if (-not $j -and -not $isNet) { "$Id no JSON, see $f.err"; Fin 3 }
  if ($j) {
    $u = $j.usage
    $in += [int]$u.input_tokens; $cache += [int]$u.cache_read_tokens; $outTok += [int]$u.output_tokens
    $think += [int]$u.thinking_tokens; $turns += [int]$j.num_turns; $secs += [int]$j.duration_seconds
  }
  if (-not $isNet -or $n -ge $NetRetries) { break }
  [Console]::WriteLine("$Id NET-ERROR in attempt $($n + 1): $(($errText -replace '\s+', ' ').Trim())")
  if (-not (Wait-Net -MaxMin $NetMaxMin -Tag $Id)) { break }
  # resume the same conversation when agy reported one, else start the brief again
  $agyArgs = if ($j -and $j.conversation_id) {
    @("-p", "`"$resume`"", "--conversation", $j.conversation_id, "--output-format", "json") } else { $agyArgs }
}
if (-not $j) { "$Id NO-NET: agy lost the network $($n + 1) times without a reply, see $f.err"; Fin 7 }
# the cap counts fresh tokens; cache reads (the re-sent prefix) are shown but not counted
$used = $in + $outTok + $think
$flag = if ($used -gt $CapK * 1000) { "OVER-CAP" } else { "ok" }
$j.response | Out-File "$out.md" -Encoding utf8
"$Id $($j.status) $flag used=$used (in $in cache $cache out $outTok) turns=$turns ${secs}s net-retries=$n " +
  "conv=$($j.conversation_id) -> $out.md"
if ($isNet) { Fin 7 } elseif ($j.status -ne "SUCCESS") { Fin 1 } else { Fin 0 }
