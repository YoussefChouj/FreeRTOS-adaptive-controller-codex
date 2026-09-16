<#
.SYNOPSIS
  Diagnose and recover the ATK / DeveBox CH340 FC dongle COM port (ADR-0007).

.DESCRIPTION
  Walks through three states in order:

   1) "not present"  -> the dongle is physically disconnected or the CH340
      driver did not bind. Prompts the user to plug it in, then rescans.
   2) "Unknown / CM_PROB_PHANTOM" -> the device is half-enumerated. The
      driver cannot disable / restart it. Recommends the only working recovery
      on Windows: Device Manager -> Uninstall device + tick "Attempt to remove
      the driver for this device" -> physical unplug-replug. Until that is
      done, prints the new candidate port and exits so the user can override
      serial_port_fallback.
   3) "OK" -> emits a one-line summary compatible with shell `FOR /F`.

  No destructive action is taken. PowerShell is read-only against PnP.

.PARAMETER Class
  Device class to scan. Default is 'Ports' (serial + COM). Use 'USBDevice' or
  'USB' for the raw USB tree if 'Ports' misses it.

.PARAMETER DescriptionHint
  Substring used to identify the dongle in PnP / pyserial output. Default
  matches the ATK / DeveBox CH340: 'CH340', 'USB-SERIAL', '1A86:7523'.

.PARAMETER AsSummary
  Emit a single-line summary suitable for capture. Default emits the full
  human-readable report.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Recover-AtkComPort.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Recover-AtkComPort.ps1 -AsSummary
  COM3:OK

.NOTES
  Author: UAV Lab, 2026-07-23. ADR-0007.
#>

[CmdletBinding()]
param(
    [string]$Class = 'Ports',
    [string[]]$DescriptionHint = @('CH340', 'USB-SERIAL', '1A86:7523'),
    [switch]$AsSummary
)

$ErrorActionPreference = 'Continue'

function Get-CandidatePorts {
    param([string]$Class, [string[]]$Hint)
    try {
        $ports = Get-PnpDevice -Class $Class -ErrorAction Stop
    } catch {
        Write-Warning "Get-PnpDevice failed: $_"
        return @()
    }
    $candidates = foreach ($p in $ports) {
        $matches = $false
        foreach ($h in $Hint) {
            if ($p.FriendlyName -and $p.FriendlyName.IndexOf($h, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $matches = $true; break
            }
            if ($p.InstanceId -and $p.InstanceId.IndexOf($h, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $matches = $true; break
            }
        }
        if ($matches) {
            [pscustomobject]@{
                FriendlyName = $p.FriendlyName
                InstanceId   = $p.InstanceId
                Status       = $p.Status
                Problem      = $p.Problem
                Class        = $p.Class
            }
        }
    }
    return $candidates
}

function Try-PyserialList {
    # Cross-check via pyserial for COM numbers Windows assigned.
    try {
        $out = & python -c "import serial.tools.list_ports as lp; [print(p.device + '|' + (p.description or '')) for p in sorted(lp.comports(), key=lambda p:p.device)]" 2>$null
        return $out
    } catch {
        return @()
    }
}

# --- 1. discover ----------------------------------------------------------
if (-not $AsSummary) {
    Write-Host ""
    Write-Host "=== Recover-AtkComPort.ps1 (ADR-0007) ===" -ForegroundColor Cyan
    Write-Host ("Class: {0}" -f $Class)
    Write-Host ("Hint : {0}" -f ($DescriptionHint -join ', '))
    Write-Host ""
}

$cands = Get-CandidatePorts -Class $Class -Hint $DescriptionHint

if (-not $AsSummary) {
    Write-Host "=== PnP candidate devices ==="
    if (-not $cands) {
        Write-Host "  (no devices matching hint in class '$Class')"
    } else {
        foreach ($c in $cands) {
            $state = if ($c.Status -eq 'OK') { 'OK' } else { $c.Status }
            Write-Host ("  [{0,-7}] {1}" -f $state, $c.FriendlyName)
            if ($c.Problem) {
                Write-Host ("            problem={0}" -f $c.Problem) -ForegroundColor Yellow
            }
            Write-Host ("            instance={0}" -f $c.InstanceId)
        }
    }
}

$pyLines = Try-PyserialList
if (-not $AsSummary) {
    Write-Host ""
    Write-Host "=== pyserial.comports() ==="
    if (-not $pyLines) {
        Write-Host "  (none / pyserial not installed)"
    } else {
        foreach ($l in $pyLines) { Write-Host ("  {0}" -f $l) }
    }
}

# --- 2. classify + recommend ---------------------------------------------
$ok      = @($cands | Where-Object { $_.Status -eq 'OK' })
$phantom = @($cands | Where-Object { $_.Status -ne 'OK' })
$pyMap   = @{}
foreach ($l in $pyLines) {
    $parts = $l -split '\|', 2
    if ($parts.Count -eq 2) { $pyMap[$parts[0]] = $parts[1] }
}

if (-not $AsSummary) { Write-Host ""; Write-Host "=== Recommendation ===" -ForegroundColor Cyan }

if ($ok -and $ok.Count -gt 0) {
    $port = ($pyLines | Where-Object { $_ -match $ok[0].FriendlyName -or $_ -match 'CH340|1A86:7523' } | Select-Object -First 1)
    $portName = if ($port) { ($port -split '\|')[0] } else { '<unknown>' }
    $msg = "Dongle healthy. pyserial reports {0}. Set serial_port_fallback in config.yaml accordingly." -f $portName
    if (-not $AsSummary) { Write-Host ("  [OK] " + $msg) -ForegroundColor Green }
    if ($AsSummary) { Write-Output ("{0}:OK" -f $portName) }
    exit 0
}

if ($phantom -and $phantom.Count -gt 0) {
    $msg = @"
Dongle is half-enumerated (Status=$($phantom[0].Status), Problem=$($phantom[0].Problem)).
Driver reset (pnputil /disable, /enable, /restart-device) returns ACCESS DENIED on this Windows install.

Only working recovery:
  1. Device Manager -> right-click the phantom entry -> Uninstall device
  2. Tick "Attempt to remove the driver for this device"
  3. OK, then physical unplug-replug of the dongle
"@
    if (-not $AsSummary) {
        Write-Host ("  [PHANTOM] " + $msg) -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Meanwhile, update ground_station/config.yaml:" -ForegroundColor Yellow
        Write-Host "    serial_port_fallback: <new COM after recovery>" -ForegroundColor Yellow
    }
    if ($AsSummary) { Write-Output "PHANTOM:RECOVER_VIA_DEV_MGR" }
    exit 2
}

# No candidate at all - assume not plugged in.
$msg = "No matching dongle found in class '$Class'. Plug it in and rerun."
if (-not $AsSummary) { Write-Host ("  [MISSING] " + $msg) -ForegroundColor Red }
if ($AsSummary) { Write-Output "MISSING:PLUG_IN" }
exit 3
