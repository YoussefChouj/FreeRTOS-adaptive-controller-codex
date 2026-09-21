$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:8081'
function Probe {
    param($Name, [scriptblock]$Fn)
    Write-Host ("=== " + $Name + " ===")
    try {
        & $Fn
    } catch {
        Write-Host ("error: " + $_.Exception.Message)
    }
    Write-Host ''
}

Probe '/state' {
    $r = Invoke-WebRequest -Uri "$base/state" -UseBasicParsing -TimeoutSec 5
    $j = $r.Content | ConvertFrom-Json
    Write-Host ("status=" + $r.StatusCode + " connected=" + $j.connected)
    Write-Host ("streams=" + ($j.streams.PSObject.Properties.Name -join ','))
    foreach ($k in $j.streams.PSObject.Properties.Name) {
        $v = $j.streams.$k
        $valCount = ($v.values.PSObject.Properties | Measure-Object).Count
        Write-Host ("  slot '" + $k + "' tag=" + $v.tag + " seq=" + $v.sequence + " rx=" + $v.received + " keys=" + $valCount)
    }
    Write-Host ("last_tx=" + ($j.last_transaction_result | ConvertTo-Json -Compress))
}

Probe 'POST /commands (PID gain commandId=1 idx=0 val=0.5)' {
    $body = @{ command_id = 1; index = 0; value = 0.5 } | ConvertTo-Json -Compress
    $r = Invoke-WebRequest -Uri "$base/commands" -Method POST -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 10
    Write-Host ("status=" + $r.StatusCode + " body=" + $r.Content)
}

Probe 'POST /commands (gs_max_horizontal_speed, cmdId=9 idx=0 val=5.0)' {
    $body = @{ command_id = 9; index = 0; value = 5.0 } | ConvertTo-Json -Compress
    $r = Invoke-WebRequest -Uri "$base/commands" -Method POST -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 10
    Write-Host ("status=" + $r.StatusCode + " body=" + $r.Content)
}

Probe 'POST /subscribe (slot 1, divider=10, 3 vars)' {
    $body = @{ slot = 1; divider = 10; ranges = @(
        @{ address = '0x200002F8'; size = 4; count = 1 },  # imu_data.rol
        @{ address = '0x200002FC'; size = 4; count = 1 },  # imu_data.pit
        @{ address = '0x20000300'; size = 4; count = 1 }   # imu_data.yaw
    ) } | ConvertTo-Json -Compress
    $r = Invoke-WebRequest -Uri "$base/subscribe" -Method POST -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 10
    Write-Host ("status=" + $r.StatusCode + " body=" + $r.Content)
}
