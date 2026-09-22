$taskName = "AgentOpsVPNMonitor"
$scriptPath = "C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\watchdog\vpn_monitor.py"
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    # fallback if pythonw is not on path
    $pythonw = (Get-Command python.exe).Source -replace "python.exe", "pythonw.exe"
}

# Create a scheduled task action to run pythonw
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Run with lowest privileges, hidden
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId (Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty UserName) -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force

# Start it immediately
Start-ScheduledTask -TaskName $taskName
Write-Output "VPN Monitor scheduled task installed and started."
