$r = Invoke-RestMethod http://localhost:8081/state -TimeoutSec 5
$slot0 = $r.streams.'0'
$keys = @($slot0.values.PSObject.Properties.Name)
Write-Host "Slot 0 has $($keys.Count) keys:"
foreach ($k in $keys) {
    Write-Host "  $k = $($slot0.values.$k)"
}
