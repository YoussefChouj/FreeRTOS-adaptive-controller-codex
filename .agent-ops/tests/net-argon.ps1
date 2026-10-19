# Offline harness for argon.ps1: Start-Process (agy), Invoke-WebRequest (probe) and Start-Sleep are mocked.
# Run: powershell -NoProfile -File .agent-ops/tests/net-argon.ps1  (offline, seconds; every line must say PASS)
$argon = "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\argon.ps1"
function Start-Sleep { param($Seconds) }
function Invoke-WebRequest { param($Uri, $Proxy, [switch]$UseBasicParsing, $TimeoutSec, $Method) [pscustomobject]@{ StatusCode = 204 } }
function Start-Process { param($FilePath, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError, $ArgumentList)
  $global:calls += ,($ArgumentList -join " ")
  $k = $global:script[$global:calls.Count - 1]
  $net = '{"conversation_id":"C1","status":"ERROR","response":"","error":"There was a network issue connecting to the server, please try again.","duration_seconds":640,"num_turns":3,"usage":{"input_tokens":1000,"output_tokens":10,"thinking_tokens":5,"cache_read_tokens":0}}'
  $ok = '{"conversation_id":"C1","status":"SUCCESS","response":"ANSWER","error":"","duration_seconds":20,"num_turns":2,"usage":{"input_tokens":500,"output_tokens":20,"thinking_tokens":5,"cache_read_tokens":0}}'
  $other = '{"conversation_id":"C1","status":"ERROR","response":"","error":"quota exhausted","duration_seconds":1,"num_turns":0,"usage":{}}'
  $body = @{ net = $net; ok = $ok; other = $other }[$k]
  [IO.File]::WriteAllText($RedirectStandardOutput, $body); [IO.File]::WriteAllText($RedirectStandardError, "")
  $o = [pscustomobject]@{}; $o | Add-Member ScriptMethod WaitForExit { param($ms) $true }; $o | Add-Member ScriptMethod Kill { }; $o }
function Run($name, $seq, [int]$wantCalls, [int]$wantExit, $wantText) {
  $global:calls = @(); $global:script = $seq
  $o = & $argon -Id hx -Brief b.md -NetRetries 2 -NetMaxMin 0.05 *>&1 | Out-String
  $rc = $LASTEXITCODE
  $ok = $global:calls.Count -eq $wantCalls -and $rc -eq $wantExit -and $o -match $wantText
  "{0} {1}: agy calls={2} exit={3}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $name, $global:calls.Count, $rc
  if ($global:calls.Count -gt 1) { "  2nd call resumes: " + ($global:calls[1] -match "--conversation C1") }
  if (-not $ok) { $o.Trim() }
}
Run "clean answer" @("ok") 1 0 "SUCCESS ok used=525"
Run "net drop then resume" @("net", "ok") 2 0 "SUCCESS ok used=1540 .*net-retries=1"
Run "net drop past retries" @("net", "net", "net") 3 7 "net-retries=2"
Run "non-network ERROR, no retry" @("other") 1 1 "ERROR ok"
Remove-Item "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\out\argon-hx*" -ErrorAction SilentlyContinue
