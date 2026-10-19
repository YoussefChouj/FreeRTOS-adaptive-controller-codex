# Offline harness for jules.ps1 network handling: Invoke-RestMethod, Invoke-WebRequest and Start-Sleep are mocked
# Run: powershell -NoProfile -File .agent-ops/tests/net-jules.ps1  (offline, ~1 min; every line must say PASS)
# (functions shadow cmdlets in the child script), so no request leaves the machine.
$repo = "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex"
$jules = "$repo\.agent-ops\jules.ps1"
$env:JULES_API_KEY = "dummy-offline"
$replyRel = ".agent-ops\out\nettest-reply.md"
"Reply for the offline net test.`nSecond line." | Out-File "$repo\$replyRel" -Encoding utf8
$global:log = @()
function Start-Sleep { param($Seconds) $global:slept += [int]$Seconds; [Threading.Thread]::Sleep(40) }
function Invoke-WebRequest { param($Uri, $Proxy, [switch]$UseBasicParsing, $TimeoutSec, $Method)
  $global:probes++
  if ($global:probes -le $global:probeFails) { throw "probe refused" }
  [pscustomobject]@{ StatusCode = 204 } }
function Invoke-RestMethod { param($Method, $Uri, $Headers, [switch]$UseBasicParsing, $TimeoutSec, $Body, $ContentType)
  $path = $Uri -replace '^.*/v1alpha/', ''
  $global:log += "$Method $path"
  if ($Method -eq "POST") {
    $global:posts++
    if ($global:posts -le $global:postFails) { throw [Net.WebException]::new("timed out", [Net.WebExceptionStatus]::Timeout) }
    $global:delivered = $true; return @{} }
  if ($path -like "*activities*") {
    if ($global:getFails -gt 0) { $global:getFails--; throw [Net.WebException]::new("refused", [Net.WebExceptionStatus]::ConnectFailure) }
    $acts = @()
    if ($global:delivered -or ($global:posts -ge 1 -and $global:landsOnTimeout)) {
      $acts += [pscustomobject]@{ userMessaged = [pscustomobject]@{ userMessage = "Reply for the offline net test.`r`nSecond line." } } }
    return [pscustomobject]@{ activities = $acts } }
  return [pscustomobject]@{ state = "COMPLETED"; outputs = @(); url = "x" } }

function Run($name, [int]$postFails, [bool]$landsOnTimeout, [int]$getFails, [int]$probeFails, [int]$wantPosts, [int]$wantExit) {
  $global:posts = 0; $global:postFails = $postFails; $global:landsOnTimeout = $landsOnTimeout; $global:delivered = $false
  $global:getFails = $getFails; $global:probes = 0; $global:probeFails = $probeFails; $global:slept = 0; $global:log = @()
  $o = & $jules -Id nettest -Session S1 -Reply $replyRel -PollSec 1 -TimeoutMin 0 -NetMaxMin 0.05 *>&1 | Out-String
  $rc = $LASTEXITCODE
  $ok = ($wantPosts -lt 0 -or $global:posts -eq $wantPosts) -and ($rc -eq $wantExit)
  "{0} {1}: posts={2} exit={3} slept={4}s probes={5}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $name, $global:posts, $rc, $global:slept, $global:probes
  if (-not $ok) { $o.Trim() }
}
Run "clean send" 0 $false 0 0 1 0
Run "timeout but landed -> not re-sent" 1 $true 0 0 1 0
Run "timeout, not landed -> re-sent once" 1 $false 0 0 2 0
Run "GET refused + proxy down 2 probes -> waits, recovers" 0 $false 2 2 1 0
Run "POST times out forever -> AMBIGUOUS, exit 3" 9999 $false 0 0 -1 3
Remove-Item "$repo\$replyRel", "$repo\.agent-ops\out\jules-nettest.*" -ErrorAction SilentlyContinue
