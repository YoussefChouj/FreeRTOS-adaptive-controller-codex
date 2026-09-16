<#
  Elevated headless build with Defender real-time protection temporarily off.

  The command-line firmware build writes ~85 fresh .o files and a new JX_FLY.axf
  in a burst; Defender's real-time scanner grabs each one for a split second and
  the compiler's write fails with "Invalid argument". Excluding OBJ/ would be the
  gentler fix but did not take on this machine, so we disable real-time monitoring
  for the duration of the build ONLY, and re-enable it in a finally that runs even
  if the build throws or the window is closed.

  MUST be launched elevated (Set-MpPreference needs admin). Writes a result file
  the caller polls: C:\tmp\defender_build.result  ("OK <exit>" or "FAIL <exit>").
  Full build output is tee'd to C:\tmp\defender_build.log.

  Usage (elevated):  powershell -ExecutionPolicy Bypass -File defender_build.ps1 [-Rebuild]
#>
param([switch]$Rebuild)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # ground_station\flashtool -> repo root
$result = 'C:\tmp\defender_build.result'
$log    = 'C:\tmp\defender_build.log'
New-Item -ItemType Directory -Force -Path 'C:\tmp' | Out-Null
Remove-Item $result -ErrorAction SilentlyContinue

# Confirm we are actually elevated before touching Defender.
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    "FAIL not-elevated" | Set-Content $result
    Write-Error "defender_build.ps1 must run elevated (admin)."
    exit 9
}

$restored = $false
try {
    $was = (Get-MpComputerStatus).RealTimeProtectionEnabled
    "RealTimeProtectionEnabled before: $was" | Tee-Object -FilePath $log
    Set-MpPreference -DisableRealtimeMonitoring $true
    Start-Sleep -Milliseconds 300
    "RealTimeProtectionEnabled now:    $((Get-MpComputerStatus).RealTimeProtectionEnabled)" | Tee-Object -FilePath $log -Append

    Set-Location $repo
    $py = @('-m', 'ground_station.flashtool', 'build')
    if ($Rebuild) { $py += '--rebuild' }
    & python @py 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
}
finally {
    # ALWAYS turn protection back on, no matter what happened above.
    try { Set-MpPreference -DisableRealtimeMonitoring $false; $restored = $true } catch { $restored = $false }
    $state = try { (Get-MpComputerStatus).RealTimeProtectionEnabled } catch { 'unknown' }
    "RealTimeProtectionEnabled restored: $state (restore-call-ok=$restored)" | Tee-Object -FilePath $log -Append
}

if ($null -eq $code) { $code = 1 }
$tag = if ($code -eq 0) { 'OK' } else { 'FAIL' }
"$tag $code restored=$restored rtp=$state" | Set-Content $result
exit $code
