# net-wait.ps1 - dot-sourced by argon.ps1 and jules.ps1: ride out a VPN node timeout or proxy drop instead of failing.
# Probe = one tiny request to a Google endpoint through the Clash mixed port, i.e. the same AI-exit route that Gemini
# (Argon) and the Jules API use. It never touches Clash itself (no restart, no node switch): the tray and url-test
# groups do that, these scripts only wait for them.
$NetProxy = if ($env:NET_WAIT_PROXY) { $env:NET_WAIT_PROXY } else { "http://127.0.0.1:7897" }
$NetProbeUrl = "https://www.gstatic.com/generate_204"

function Test-Net([int]$TimeoutSec = 10) {
  try {
    $r = Invoke-WebRequest -Uri $NetProbeUrl -Proxy $NetProxy -UseBasicParsing -TimeoutSec $TimeoutSec -Method Get
    return [int]$r.StatusCode -eq 204
  } catch { return $false }
}

# Wait until the probe answers, at most $MaxMin minutes. Backoff 15 s, 30 s, then every 60 s. Returns $true when the
# network is back (at once if it never left), $false on giving up. $Tag prefixes the two log lines it may write.
function Wait-Net([double]$MaxMin = 30, [string]$Tag = "net") {
  if (Test-Net) { return $true }
  $t0 = Get-Date
  [Console]::WriteLine("$Tag NET-DOWN: no answer from $NetProbeUrl via $NetProxy; waiting up to $MaxMin min")
  for ($n = 1; ($leftS = $MaxMin * 60 - ((Get-Date) - $t0).TotalSeconds) -gt 0; $n++) {
    Start-Sleep -Seconds ([Math]::Max(1, [Math]::Ceiling([Math]::Min([Math]::Min(60, 15 * $n), $leftS))))
    if (Test-Net) {
      [Console]::WriteLine("$Tag NET-BACK after $([int]((Get-Date) - $t0).TotalSeconds) s")
      return $true
    }
  }
  [Console]::WriteLine("$Tag NET-GAVE-UP after $MaxMin min")
  return $false
}
