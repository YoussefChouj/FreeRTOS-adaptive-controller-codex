$r = Invoke-RestMethod http://localhost:8081/state -TimeoutSec 5
Write-Host "schema_id: $($r.schema_id)"
Write-Host "connected: $($r.connected)"
Write-Host "samples: $($r.samples)"
Write-Host "last_update_ns: $($r.last_update_ns)"
$streams = $r.streams
Write-Host "Streams count: $($streams.Count)"
$streams.PSObject.Properties | ForEach-Object {
    $slot = $_.Name
    $val = $_.Value
    $keys = $val.values.PSObject.Properties.Name
    Write-Host "Slot $slot : $($keys.Count) keys, first 15: $($keys[0..14] -join ', ')"
}
