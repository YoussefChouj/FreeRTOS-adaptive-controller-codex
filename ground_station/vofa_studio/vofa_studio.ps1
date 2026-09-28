# VOFA Studio launcher: opens http://127.0.0.1:8090 in the browser.
# Connect the laptop to the drone WiFi AP (192.168.4.1) before pressing Start.
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $root
try { python -m ground_station.vofa_studio @args }
finally { Pop-Location }
