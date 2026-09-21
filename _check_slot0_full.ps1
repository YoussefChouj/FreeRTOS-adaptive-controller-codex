$r = Invoke-RestMethod http://localhost:8081/state -TimeoutSec 5
$slot0 = $r.streams.'0'
Write-Host "=== SLOT 0 FULL DUMP ==="
Write-Host "tag: $($slot0.tag)"
Write-Host "sequence: $($slot0.sequence)"
Write-Host "received: $($slot0.received)"
Write-Host "dropped: $($slot0.dropped)"
Write-Host "loss_pct: $($slot0.loss_pct)"
Write-Host "last_update_ns: $($slot0.last_update_ns)"
Write-Host ""
Write-Host "All values:"
$slot0.values.PSObject.Properties | ForEach-Object {
    Write-Host "  $($_.Name) = $($_.Value)"
}

Write-Host ""
Write-Host "=== ALL STREAM KEYS ==="
$r.streams.PSObject.Properties | ForEach-Object {
    Write-Host "Slot: $($_.Name) - received=$($_.Value.received) dropped=$($_.Value.dropped)"
}
