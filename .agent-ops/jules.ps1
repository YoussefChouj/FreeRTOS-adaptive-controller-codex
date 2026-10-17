# jules.ps1 - free worker lane (2026-10-17): Google Jules via its REST API (alpha). Ultra = 300 tasks per rolling 24 h,
# 60 at once, counted in tasks, not tokens, and it uses no Claude tokens. Google picks the model (no per-task choice).
# Jules works on Google's VM from a clean clone of the PUSHED branch and opens a PR: no Keil, no lab, no WSL tools, no
# untracked logs or videos. Good fit: Python ground_station, host C tests, docs. The CEO reviews every PR before merge.
# Key: only the USER makes it (jules.google -> Settings -> API keys, max 3) and saves it to $KeyFile or $env:JULES_API_KEY.
# Agents never type, print or commit the key. Guide: docs/agent/jules.md
# Start:   powershell -File .agent-ops/jules.ps1 -Id t-foo -Brief .agent-ops/briefs/t-foo.md [-Branch b] [-NoPr]
# Re-poll: powershell -File .agent-ops/jules.ps1 -Id t-foo -Session <session id>
# Sources: powershell -File .agent-ops/jules.ps1 -Sources        (is the GitHub repo connected?)
# Reply:   powershell -File .agent-ops/jules.ps1 -Id t-foo -Session <id> -Reply <repo-relative .md>  (answers a question, then waits)
#          add -Approve to approve a pending plan instead of (or before) the reply
# Wait:    powershell -File .agent-ops/jules.ps1 -Id t-foo -Session <id> -Wait   (the reply already went out: wait, never re-send)
# Network: every call waits out a VPN or proxy drop (net-wait.ps1) for up to NetMaxMin minutes, then exits 7.
# Exit: 0 done or PR, 1 FAILED, 2 timeout, 3 API error, 4 no key, 5 branch not pushed, 6 waits on you, 7 no network.
param([string]$Id, [string]$Brief, [string]$Session, [string]$Branch, [switch]$NoPr, [switch]$Sources, [string]$Reply, [switch]$Approve, [switch]$Wait,
      [string]$Repo = "YoussefChouj/FreeRTOS-adaptive-controller-codex", [int]$TimeoutMin = 90, [int]$PollSec = 60, [double]$NetMaxMin = 30,
      [string]$KeyFile = "D:\tools\jules\api-key.txt")
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$key = $env:JULES_API_KEY
if (-not $key -and (Test-Path $KeyFile)) { $key = (Get-Content $KeyFile -Raw).Trim() }
if (-not $key) { "jules NO-KEY: the user makes a key at jules.google -> Settings -> API keys and saves it to $KeyFile"; exit 4 }
$api = "https://jules.googleapis.com/v1alpha"
$hdr = @{ "x-goog-api-key" = $key }
. (Join-Path $PSScriptRoot "net-wait.ps1")
# $landed: for a POST, a scriptblock that tells whether a request that failed after it was sent took effect anyway
# (a timeout or a cut reply leaves that unknown). It returns the result to use, or $null when the POST must be re-sent.
function Call($method, $path, $body, $landed = $null) {
  $a = @{ Method = $method; Uri = "$api/$path"; Headers = $hdr; UseBasicParsing = $true; TimeoutSec = 120 }
  if ($body) {
    $json = $body | ConvertTo-Json -Depth 6
    # Get-Content strings carry PSPath/PSDrive/PSProvider notes that ConvertTo-Json expands (a 1 KB reply became 107 MB
    # and got 400, 2026-10-17): read files with Text, and refuse any body that big before it is sent
    if ($json.Length -gt 1MB) { [Console]::WriteLine("jules BODY-TOO-BIG $($json.Length) chars on $method $path"); exit 3 }
    $a.Body = [Text.Encoding]::UTF8.GetBytes($json); $a.ContentType = "application/json; charset=utf-8" }
  # A VPN node timeout or proxy drop must not kill a 90 min wait (2026-10-17: one drop failed 4 runs at once). On a
  # network error, 429 or 5xx: wait for the network (Wait-Net), then retry, for up to $NetMaxMin minutes in total.
  # A GET is always safe to repeat; so is a POST that never left (connect, send or DNS failure). A POST that may have
  # arrived is repeated only after $landed says it did not, so a reply is never delivered twice.
  $c0 = Get-Date
  for ($try = 1; ; $try++) {
    try { return Invoke-RestMethod @a } catch {
      $ex = $_.Exception
      $code = if ($ex.Response) { [int]$ex.Response.StatusCode } else { "net" }
      $unsent = ($ex -is [Net.WebException]) -and
        (@("ConnectFailure", "SendFailure", "NameResolutionFailure", "ProxyNameResolutionFailure") -contains "$($ex.Status)")
      $transient = ($code -eq "net") -or ($code -is [int] -and ($code -eq 429 -or $code -ge 500))
      $left = $NetMaxMin - ((Get-Date) - $c0).TotalMinutes
      $repeatable = $unsent -or ($method -eq "GET") -or $landed
      if ($transient -and $repeatable -and $left -gt 0) {
        [Console]::WriteLine("jules retry $try on $method $path ($code $($ex.Status))")
        Start-Sleep -Seconds ([Math]::Min(60, 15 * $try))
        if (-not (Wait-Net -MaxMin $left -Tag "jules")) { break }
        if ($method -eq "POST" -and -not $unsent) {
          $r = & $landed
          if ($null -ne $r) { [Console]::WriteLine("jules $method $path had landed before the drop: not re-sent"); return $r }
        }
        continue
      }
      $why = if ($transient -and $method -eq "POST" -and -not $unsent) {
        " (AMBIGUOUS: it may have arrived; check the Jules sessions before re-running)" } else { "" }
      # Console write: plain output would vanish into the caller's assignment when exit runs
      [Console]::WriteLine("jules API-ERROR $code on $method $path : $($ex.Message)$why"); exit 3 }
  }
  [Console]::WriteLine("jules NET-ERROR on $method $path : no network for $NetMaxMin min"); exit 7
}
# Every activity of a session, all pages: one 100-item page shows only the oldest ones, so a long session looked stale
# (2026-10-17, j-ladder-b) and a reply could go out twice
function Activities($sid) {
  $acts = @(); $tok = ""
  do {
    $pg = Call GET ("sessions/$sid/activities?pageSize=100" + $(if ($tok) { "&pageToken=$tok" } else { "" })) $null
    if ($pg.activities) { $acts += @($pg.activities) }
    $tok = $pg.nextPageToken
  } while ($tok)
  return $acts
}
# first $n characters with whitespace runs folded, so a stored message matches the file it came from
function Flat($s, $n) { $s = ("$s" -replace "\s+", " ").Trim(); if ($s.Length -gt $n) { $s.Substring(0, $n) } else { $s } }
# how many user messages of the session start with $head
function Sent($sid, $head) { @(Activities $sid | Where-Object { $_.userMessaged -and (Flat $_.userMessaged.userMessage 200).StartsWith($head) }).Count }
function Text($rel) { [IO.File]::ReadAllText((Join-Path $root $rel), [Text.Encoding]::UTF8) }
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
  $prompt = (Text $Brief) + "`n`nRules: read AGENTS.md first. Keep the change " +
    "small and inside this brief. Never edit USER/ (Keil project), flashing or lab scripts. Run the tests for every file " +
    "you change. In the PR description list the files changed and each test command with its result."
  $body = @{ prompt = $prompt; title = $Id; requirePlanApproval = $false
             sourceContext = @{ source = "sources/github/$Repo"; githubRepoContext = @{ startingBranch = $Branch } } }
  if (-not $NoPr) { $body.automationMode = "AUTO_CREATE_PR" }
  $Session = (Call POST "sessions" $body).id
  $Session | Out-File "$out.session" -Encoding ascii
}
# A reply or approval: the state stays AWAITING_* for a moment after it, so the loop ignores that state for a few polls
$grace = 0
# A POST cut off mid-flight is re-sent only when the session shows it did not land: the plan still waits, or no new
# user message starts with this reply
if ($Session -and $Approve) {
  Call POST "sessions/${Session}:approvePlan" @{} {
    if ((Call GET "sessions/$Session" $null).state -ne "AWAITING_PLAN_APPROVAL") { @{} } } | Out-Null
  $grace = 5 }
if ($Session -and $Reply) {
  if (-not (Test-Path (Join-Path $root $Reply))) { "jules: -Reply <repo-relative path> not found"; exit 1 }
  $msg = Text $Reply
  $head = Flat $msg 200
  $before = Sent $Session $head
  Call POST "sessions/${Session}:sendMessage" @{ prompt = $msg } {
    if ((Sent $Session $head) -gt $before) { @{} } } | Out-Null
  $grace = 5; $Wait = $true
}
# After a reply (or with -Wait, when the reply already went out) the session keeps its old state (COMPLETED, or a PR
# that already exists) until Jules picks the message up, so wait for it to move first (2026-10-17, j-att-fit: 0 min)
$moved = -not $Wait
if ($Wait -and -not $grace) { $grace = 5 }
# Waits inside this one background run (the CEO starts it once and reads the digest, never polls by hand)
$t0 = Get-Date
$stop = @("COMPLETED", "FAILED", "PAUSED", "AWAITING_USER_FEEDBACK", "AWAITING_PLAN_APPROVAL")
do {
  $s = Call GET "sessions/$Session" $null
  $pr = @($s.outputs | Where-Object { $_.pullRequest } | ForEach-Object { $_.pullRequest })[0]
  if ($stop -notcontains $s.state) { $moved = $true }
  if (-not $moved -and $grace -gt 0) { $grace--; Start-Sleep -Seconds $PollSec; continue }
  if ($grace -gt 0 -and $s.state -like "AWAITING*" -and -not $pr) { $grace--; Start-Sleep -Seconds $PollSec; continue }
  if ($stop -contains $s.state -or ($pr -and -not $Wait)) { break }
  Start-Sleep -Seconds $PollSec
} while (((Get-Date) - $t0).TotalMinutes -lt $TimeoutMin)
$acts = Activities $Session
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
