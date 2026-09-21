# Test harness only (not shipped pipeline code): feeds recorded real failure
# outputs through the EXACT classifier fragment from Test-WorkerBackend in
# agent-ops.ps1, since the live backend cannot be made to 401/429 safely.
function Classify([string[]]$probe) {
    $err = @($probe | Where-Object { $_ -match 'UNAUTHENTICATED|\(code 401\)|RESOURCE_EXHAUSTED|\(code 429\)|Individual quota|sign in to view' } | Select-Object -First 1)
    if ($err.Count -eq 0) { return 'PASS (no failure signature)' }
    if ($err[0] -match 'UNAUTHENTICATED|\(code 401\)|sign in to view') {
        return "THROW-AUTH-GONE: agy auth is gone - re-login in WSL required; supervisor cannot fix. Cause: $($err[0])"
    }
    return "THROW-QUOTA: agy quota exhausted - use -Worker ark (separate pool). Cause: $($err[0])"
}
# Case 1: live no-auth probe output (captured 2026-09-21 07:1x via HOME=/tmp/agy-noauth agy models)
Write-Output ("no-auth  : " + (Classify @('Error: Please sign in to view available models. Launch the CLI without arguments to sign in.')))
# Case 2: recorded 401 from .agent-ops/logs/20260921-063609.out (verbatim line)
Write-Output ("401      : " + (Classify @('error: UNAUTHENTICATED (code 401): Request had invalid authentication credentials. Expected OAuth 2 access token, login cookie or other valid authentication credential. See https://developers.google.com/identity/sign-in/web/devconsole-project.')))
# Case 3: recorded 429 from .agent-ops/tasks/20260920-033650.result.md (verbatim line)
Write-Output ("429      : " + (Classify @('error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 48m32s.')))
# Case 4: healthy models output (recorded live 2026-09-21)
Write-Output ("healthy  : " + (Classify @('Fetching available models...', 'gemini-3.8-flash-low	Gemini 3.8 Flash (Low)')))
# Case 5: transient eligibility signature must NOT be classified as auth/quota
Write-Output ("eligibil : " + (Classify @('error: Eligibility check failed: failed to get profile picture: Get "https://lh3.googleusercontent.com/a/ACg8ocKSHcj5WJkjue1Fb7z56xmneqL_uzM-chDd93bKt8CSg4S3pw=s96-c": EOF')))
