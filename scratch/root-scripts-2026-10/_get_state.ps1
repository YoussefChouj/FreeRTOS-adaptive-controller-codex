$ErrorActionPreference = 'Continue'
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8081/state' -UseBasicParsing -TimeoutSec 5
    Write-Host "STATUS:" $r.StatusCode
    Write-Host $r.Content
} catch {
    Write-Host "HTTP error:" $_.Exception.Message
}
