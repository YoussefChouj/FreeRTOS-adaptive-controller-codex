$r = Invoke-RestMethod http://localhost:8081/state -TimeoutSec 5
Write-Host "=== FULL SLOT 0 VALUES ==="
$slot0 = $r.streams['0']
Write-Host "tag: $($slot0.tag)"
Write-Host "sequence: $($slot0.sequence)"
Write-Host "received: $($slot0.received)"
Write-Host "dropped: $($slot0.dropped)"
Write-Host "loss_pct: $($slot0.loss_pct)"
Write-Host "`nAll values in slot 0:"
$slot0.values.PSObject.Properties | ForEach-Object {
    Write-Host "  $($_.Name) = $($_.Value)"
}
Write-Host "`n=== ALL STREAMS ==="
$r.streams.PSObject.Properties | ForEach-Object {
    Write-Host "Slot: $($_.Name) - $($_.Value.values.Count) values"
}
