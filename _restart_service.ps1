$ErrorActionPreference = 'Stop'
$logPath = "C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\ground_station\service_output_v2.txt"
$env:PYTHONUNBUFFERED = '1'
Set-Location "C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex"
$proc = Start-Process -FilePath 'python' -ArgumentList @(
    '-m', 'ground_station.service',
    '--wifi-host', '192.168.4.1',
    '--wifi-port', '14550',
    '--port', '8081',
    '--rtos-bridge',
    '--rtos-interval', '5'
) -RedirectStandardOutput $logPath -RedirectStandardError "$logPath.err" -PassThru -WindowStyle Hidden
Write-Host "Started PID:" $proc.Id
Start-Sleep -Seconds 2
Get-Process -Id $proc.Id -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, @{Name='CmdLine';Expression={(Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine}} | Format-List
