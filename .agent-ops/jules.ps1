# jules.ps1 - free worker lane (2026-10-17): Google Jules via its REST API (alpha). Ultra = 300 tasks per rolling 24 h,
# 60 at once, counted in tasks, not tokens, and it uses no Claude tokens. Google picks the model (no per-task choice).
# Jules works on Google's VM from a clean clone of the PUSHED branch and opens a PR: no Keil, no lab, no WSL tools, no
# untracked logs or videos. Good fit: Python ground_station, host C tests, docs. The CEO reviews every PR before merge.
# Key: only the USER makes it (jules.google -> Settings -> API keys, max 3) and saves it to $KeyFile or $env:JULES_API_KEY.
# Agents never type, print or commit the key. Guide: docs/agent/jules.md
# Start:   powershell -File .agent-ops/jules.ps1 -Id t-foo -Brief .agent-ops/briefs/t-foo.md [-Branch b] [-NoPr]
# Re-poll: powershell -File .agent-ops/jules.ps1 -Id t-foo -Session <session id>
# Sources: powershell -File .agent-ops/jules.ps1 -Sources        (is the GitHub repo connected?)
param([string]$Id, [string]$Brief, [string]$Session, [string]$Branch, [switch]$NoPr, [switch]$Sources,
      [string]$Repo = "YoussefChouj/FreeRTOS-adaptive-controller-codex", [int]$TimeoutMin = 90, [int]$PollSec = 60,
      [string]$KeyFile = "D:\tools\jules\api-key.txt")
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$key = $env:JULES_API_KEY
if (-not $key -and (Test-Path $KeyFile)) { $key = (Get-Content $KeyFile -Raw).Trim() }
if (-not $key) { "jules NO-KEY: the user makes a key at jules.google -> Settings -> API keys and saves it to $KeyFile"; exit 4 }
$api = "https://jules.googleapis.com/v1alpha"
$hdr = @{ "x-goog-api-key" = $key }
function Call($method, $path, $body) {
  $a = @{ Method = $method; Uri = "$api/$path"; Headers = $hdr; UseBasicParsing = $true }
  if ($body) { $a.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 6)); $a.ContentType = "application/json; charset=utf-8" }
  try { Invoke-RestMethod @a } catch {
    $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { "net" }
    # Console write: plain output would vanish into the caller's assignment when exit runs
    [Console]::WriteLine("jules API-ERROR $code on $method $path : $($_.Exception.Message)"); exit 3 }
}
function Cut($s, $n) { if (-not $s) { return "" }; $s = ($s -replace "\s+", " ").Trim(); if ($s.Length -gt $n) { $s.Substring(0, $n) + "..." } else { $s } }

if ($Sources) { (Call GET "sources?pageSize=50" $null).sources | ForEach-Object { $_.name }; exit 0 }
if (-not $Id) { "jules: -Id is required"; exit 1 }
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root ".agent-ops\out\jules-$Id"
$note = ""
if (-not $Session) {
  if (-not $Brief -or -not (Test-Path (Join-Path $root $Brief))) { "jules: -Brief <repo-relative path> is required"; exit 1 }
  if (-not $Branch) { $Branch = (git -C $root rev-parse --abbrev-ref HEAD).Trim() }
  $remote = ((git -C $root ls-remote origin "refs/heads/$Branch") -split "\s+")[0]
  if (-not $remote) { "jules NOT-PUSHED: origin has no branch $Branch (Jules clones the pushed branch)"; exit 5 }
  if ($remote -ne (git -C $root rev-parse HEAD).Trim()) { $note = "WARN: Jules sees origin/$Branch $($remote.Substring(0,8)), not local HEAD" }
  $prompt = (Get-Content (Join-Path $root $Brief) -Raw -Encoding utf8) + "`n`nRules: read AGENTS.md first. Keep the change " +
    "small and inside this brief. Never edit USER/ (Keil project), flashing or lab scripts. Run the tests for every file " +
    "you change. In the PR description list the files changed and each test command with its result."
  $body = @{ prompt = $prompt; title = $Id; requirePlanApproval = $false
             sourceContext = @{ source = "sources/github/$Repo"; githubRepoContext = @{ startingBranch = $Branch } } }
  if (-not $NoPr) { $body.automationMode = "AUTO_CREATE_PR" }
  $Session = (Call POST "sessions" $body).id
  $Session | Out-File "$out.session" -Encoding ascii
}
# Waits inside this one background run (the CEO starts it once and reads the digest, never polls by hand)
$t0 = Get-Date
$stop = @("COMPLETED", "FAILED", "PAUSED", "AWAITING_USER_FEEDBACK", "AWAITING_PLAN_APPROVAL")
do {
  $s = Call GET "sessions/$Session" $null
  $pr = @($s.outputs | Where-Object { $_.pullRequest } | ForEach-Object { $_.pullRequest })[0]
  if ($stop -contains $s.state -or $pr) { break }
  Start-Sleep -Seconds $PollSec
} while (((Get-Date) - $t0).TotalMinutes -lt $TimeoutMin)
$acts = @((Call GET "sessions/$Session/activities?pageSize=100" $null).activities)
@{ session = $s; activities = $acts } | ConvertTo-Json -Depth 10 | Out-File "$out.json" -Encoding utf8
$md = @("# Jules $Id", "", "- state: $($s.state)", "- session: $Session ($($s.url))", "- PR: $(if ($pr) { "$($pr.url) - $($pr.title)" } else { 'none yet' })")
if ($note) { $md += "- $note" }
if ($pr) { $md += @("", "## PR description (first 40 lines)", "") + @(($pr.description -split "`n") | Select-Object -First 40) }
$md += @("", "## Last activities", "")
foreach ($a in ($acts | Select-Object -Last 10)) {
  $txt = $a.description
  if ($a.progressUpdated) { $txt = "$($a.progressUpdated.title): $($a.progressUpdated.description)" }
  if ($a.agentMessaged) { $txt = "agent: $($a.agentMessaged.agentMessage)" }
  if ($a.planGenerated) { $txt = "plan: " + ((@($a.planGenerated.plan.steps) | ForEach-Object { $_.title }) -join " | ") }
  if ($a.sessionFailed) { $txt = "FAILED: $($a.sessionFailed.reason)" }
  $md += "- $($a.createTime) $($a.originator): $(Cut $txt 240)"
}
$md | Out-File "$out.md" -Encoding utf8
$mins = [int]((Get-Date) - $t0).TotalMinutes
$done = $pr -or $s.state -eq "COMPLETED"
"$Id $($s.state) pr=$(if ($pr) { $pr.url } else { 'none' }) session=$Session ${mins}min $note -> $out.md"
if ($s.state -eq "FAILED") { exit 1 } elseif ($done) { exit 0 } elseif ($stop -contains $s.state) { exit 6 } else { exit 2 }
