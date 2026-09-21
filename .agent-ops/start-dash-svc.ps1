# Launch the ground station service detached so it survives the win.sh job.
$dir = 'C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex'
$py  = 'C:\Users\Acer\AppData\Local\Programs\Python\Python311\python.exe'
$out = Join-Path $dir ".agent-ops\logs\svc-$(Get-Date -Format yyyyMMdd).out"
$err = Join-Path $dir ".agent-ops\logs\svc-$(Get-Date -Format yyyyMMdd).err"
$cmd = 'cmd /c cd /d "' + $dir + '" && "' + $py + '" -m ground_station.service --no-auto-subscribe > "' + $out + '" 2> "' + $err + '"'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId)"
