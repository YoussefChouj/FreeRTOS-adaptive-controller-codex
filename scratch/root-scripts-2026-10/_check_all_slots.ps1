$ErrorActionPreference = 'SilentlyContinue'
$r = Invoke-RestMethod http://localhost:8081/state -TimeoutSec 5

Write-Host "=== ALL SLOTS ==="
$r.streams.PSObject.Properties | ForEach-Object {
    $slotName = $_.Name
    $stream = $_.Value
    Write-Host "`n--- Slot: $slotName ---"
    Write-Host "  tag:      $($stream.tag)"
    Write-Host "  sequence: $($stream.sequence)"
    Write-Host "  received: $($stream.received)"
    Write-Host "  dropped:  $($stream.dropped)"
    Write-Host "  loss_pct: $($stream.loss_pct)"
    Write-Host "  last_update_ns: $($stream.last_update_ns)"
    $keys = @($stream.values.PSObject.Properties.Name)
    Write-Host "  Total values: $($keys.Count)"
    Write-Host "  Keys:"
    foreach ($k in $keys) {
        $v = $stream.values.$k
        Write-Host "    $k = $v"
    }
}

Write-Host "`n=== SAMPLES: $($r.samples) ==="
Write-Host "=== LAST UPDATE: $([DateTimeOffset]::FromUnixTimeMilliseconds([long]($r.last_update_ns / 1000000)).LocalDateTime) ==="
