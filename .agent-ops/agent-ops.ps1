<#
agent-ops.ps1 - Windows-side front end for the Antigravity worker pool.
Claude Code (supervisor) calls this; it forwards to the WSL scripts.
Task text travels through a file, so no PowerShell -> WSL quoting issues.

  .agent-ops\agent-ops.ps1 preflight [-Worker agy|ark] [-Model flash]  # cheap auth/model check (spawn runs it too)
  .agent-ops\agent-ops.ps1 spawn "task text"      # or: spawn -File task.md  [-TimeoutMin 120] [-Model flash] [-Effort low] [-Worktree] [-Force]
  .agent-ops\agent-ops.ps1 ask "question"         # one-shot read-only lookup; prints only the answer
  .agent-ops\agent-ops.ps1 wait <task_id>         # block until DONE/FAILED/EXIT/BLOCKED (not STALLED), then print a digest
  .agent-ops\agent-ops.ps1 verify <task_id>       # deterministic acceptance checks; one PASS/WARN/FAIL verdict
  .agent-ops\agent-ops.ps1 status                 # last 15 state.log lines + live workers
  .agent-ops\agent-ops.ps1 output [task_id] [-Lines 40]   # tail a worker's stdout (default: newest)
  .agent-ops\agent-ops.ps1 kill <window_id|all>   # stop worker window(s)
  .agent-ops\agent-ops.ps1 prune [-RetentionDays 14]  # close dead worker windows; drop old task/log files
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('preflight', 'spawn', 'ask', 'wait', 'verify', 'status', 'output', 'kill', 'monitor', 'attach', 'prune')]
    [string]$Action,
    [Parameter(Position = 1)]
    [string]$Arg,
    [string]$File,
    [int]$Lines = 40,
    [int]$TimeoutMin = 120,
    [ValidateSet('agy', 'ark', 'oc')]
    [string]$Worker = 'agy',
    # Alias from $modelMap below, or a raw id from `agy models`.
    [string]$Model = 'flash',
    [ValidateSet('low', 'medium', 'high')]
    [string]$Effort,
    [switch]$Worktree,
    # spawn: skip the laptop power guard (battery / CPU / RAM / one local worker).
    [switch]$Force,
    # prune: delete tasks/ and logs/ files older than this; live tasks kept.
    [int]$RetentionDays = 14
)

# Gemini bakes the effort level into the model id; Claude and GPT draw on a
# separate quota pool, so they are the fallback when the Gemini week runs low.
$modelMap = @{
    'flash'     = 'gemini-3.8-flash-low'
    'flash-med' = 'gemini-3.8-flash-medium'
    'flash-hi'  = 'gemini-3.8-flash-high'
    'pro'       = 'gemini-3.1-pro-low'
    'sonnet'    = 'claude-sonnet-4-6'
    'opus'      = 'claude-opus-4-6-thinking'
    'gpt'       = 'gpt-oss-120b-medium'
}
# Volcengine Ark Agent Plan (Medium): its own AFP budget, separate from both agy
# pools and the supervisor's Claude plan. AFP per 1M tokens = coefficient x 100,
# input and output alike, and cached input gets no discount (Ark docs 2516283,
# via the Ark console assistant 2026-09-21). So the model is the main cost lever:
# 'cheap' (0.5) is the default; 'glm' (4.5) and 'deepseek' (5.5) are for
# firmware and review work only. 'auto' (ark-code-latest) is whatever model is
# enabled in the Ark console, so its cost is not known from here.
$arkModelMap = @{
    'cheap'    = 'deepseek-v4-flash[1m]'
    'mini'     = 'doubao-seed-2.0-mini'
    'auto'     = 'ark-code-latest'
    'glm'      = 'glm-5.3[1m]'
    'kimi'     = 'kimi-k3[1m]'
    'deepseek' = 'deepseek-v4-pro[1m]'
    'flash'    = 'deepseek-v4.1-flash[1m]'
}
# opencode (-Worker oc): free models. Hetzner Inference (experimental, free,
# 10 req/min per key) and OpenRouter :free ids (20 req/min, 1000 req/day for
# the whole account). oc-worker.sh refuses any OpenRouter id without :free,
# so the paid credit is never touched. All six passed a read/write tool test
# on 2026-09-23 (north-mini-code did not: it copied line-number prefixes).
# 'free' is OpenRouter's router over its free models only (openrouter/free);
# it is the one OpenRouter id allowed without the :free suffix.
$ocModelMap = @{
    'qwen'   = 'hetzner/Qwen/Qwen3.6-35B-A3B-FP8'
    'free'   = 'openrouter/openrouter/free'
    'qwen27' = 'hetzner/Qwen3.8-27B'
    'nemo'   = 'openrouter/nvidia/nemotron-3-super-120b-a12b:free'
    'ultra'  = 'openrouter/nvidia/nemotron-3-ultra-550b-a55b:free'
    'nex'    = 'openrouter/nex-agi/nex-n2.5-pro:free'
    'laguna' = 'openrouter/poolside/laguna-s-2.1:free'
}
if ($Worker -eq 'ark') {
    if (-not $PSBoundParameters.ContainsKey('Model')) { $Model = 'cheap' }
    $modelId = if ($arkModelMap.ContainsKey($Model)) { $arkModelMap[$Model] } else { $Model }
} elseif ($Worker -eq 'oc') {
    if (-not $PSBoundParameters.ContainsKey('Model')) { $Model = 'qwen' }
    $modelId = if ($ocModelMap.ContainsKey($Model)) { $ocModelMap[$Model] } else { $Model }
} else {
    $modelId = if ($modelMap.ContainsKey($Model)) { $modelMap[$Model] } else { $Model }
}

$ErrorActionPreference = 'Stop'
$opsWin = $PSScriptRoot
$opsWsl = (wsl -d Ubuntu -- wslpath -a ($opsWin -replace '\\', '/')).Trim()

# The charger drops out under sustained load and the laptop dies (2026-09-26 00:00),
# so a local worker starts only on AC power, with headroom, and one at a time.
# Server-side workers (.agent-ops/vps-worker.sh) cost the laptop nothing: use them first.
function Assert-LocalHeadroom {
    $why = @()
    $bat = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($bat -and $bat.BatteryStatus -ne 2) { $why += "on battery ($($bat.EstimatedChargeRemaining)%)" }
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
    if ($cpu -gt 70) { $why += "CPU at $cpu%" }
    $freeGb = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
    if ($freeGb -lt 2) { $why += "only $freeGb GB RAM free" }
    $running = [int]((Invoke-Wsl 'pgrep -fc "[r]un-worker.sh" || true') | Select-Object -Last 1)
    if ($running -ge 1) { $why += "$running local worker(s) already running" }
    if ($why) {
        throw "power guard: $($why -join '; '). Run it on the VPS instead (bash .agent-ops/vps-worker.sh spawn ...), or pass -Force."
    }
}

function Invoke-Wsl([string]$script) {
    # Pass the script on stdin so no argument is re-parsed by a shell;
    # strip the BOM and CRLF that PowerShell may add when piping to native exes.
    $script | wsl -d Ubuntu -e sh -c "sed '1s/^\xEF\xBB\xBF//; s/\r$//' | bash -l"
}

# One cheap authenticated call before spawning, so a dead login or an unknown
# model fails HERE with a named cause instead of a worker that dies minutes
# later (2026-09-21: @63 ran 12 min against a dead 401 login; @64 died rc=1
# in 10 s on a model id the backend no longer offered). Throws on failure.
function Test-WorkerBackend([string]$WorkerKind, [string]$ModelId) {
    if ($WorkerKind -eq 'oc') {
        if ($ModelId -like 'openrouter/*' -and $ModelId -notlike '*:free' -and $ModelId -ne 'openrouter/openrouter/free') {
            throw "preflight: $ModelId is a paid OpenRouter model; use a :free id or an alias (qwen, free, qwen27, nemo, ultra, nex, laguna)"
        }
        Write-Output "preflight: oc model $ModelId (no quota probe; 429s show in the worker log)"
        return
    }
    if ($WorkerKind -ne 'agy') {
        # ark has no cheap probe for *quota*: 'claude --version' is local, and
        # any real turn costs quota. Its burst 429s are handled by the spawn gap
        # and cap in spawn-worker.sh.
        #
        # What did kill two ark waves on 2026-09-21 was not quota at all:
        # Claude Code refreshes the git index at startup, and in this repo from
        # WSL that re-reads 111 MB of dirty OBJ/ over the 9p mount and never
        # returns (rc=124 at 301 s, zero bytes), which reads exactly like a
        # quota death. spawn-worker.sh now starts ark outside the repo and
        # attaches the tree with --add-dir. That is the fix; core.checkStat and
        # a warmed index were measured and do NOT help. Assert the workaround
        # is still in place rather than paying 25 s to re-measure the symptom.
        $spawnSh = Join-Path $opsWin 'spawn-worker.sh'
        if (-not (Select-String -Path $spawnSh -SimpleMatch -Quiet -- '--add-dir')) {
            throw ("preflight: spawn-worker.sh no longer starts ark outside the repo with --add-dir, " +
                   "so an ark worker will hang on the WSL git index refresh and return zero bytes. " +
                   "See .claude_state.md 'Wave 6' and AGENT_GUIDE.md trap 6.")
        }
        Write-Output "preflight: ark quota not verified (no cheap probe); non-repo cwd + --add-dir workaround present"
        return
    }
    $probe = @(Invoke-Wsl "timeout 90 agy models 2>&1")
    $err = @($probe | Where-Object { $_ -match 'UNAUTHENTICATED|\(code 401\)|RESOURCE_EXHAUSTED|\(code 429\)|Individual quota|sign in to view' } | Select-Object -First 1)
    if ($err.Count -gt 0) {
        if ($err[0] -match 'UNAUTHENTICATED|\(code 401\)|sign in to view') {
            throw "preflight: agy auth is gone - re-login in WSL is required (launch 'agy' with no arguments and complete the sign-in); the supervisor cannot fix this from Windows. Cause: $($err[0])"
        }
        throw "preflight: agy quota exhausted - spawn with -Worker ark (separate pool) or wait for the reset. Cause: $($err[0])"
    }
    # A transient network EOF in the probe must not block the spawn: the
    # worker itself retries exactly that signature (run-worker.sh).
    $transient = @($probe | Where-Object { $_ -match 'Eligibility check failed' } | Select-Object -First 1)
    if ($transient.Count -gt 0) {
        Write-Output "preflight: transient probe failure (not blocking; worker retries it): $($transient[0])"
        return
    }
    # Only validate the model when the probe actually returned a list
    # (list lines are '<id><tab><name>'); an empty or hung probe passes.
    if (@($probe | Where-Object { $_ -match "`t" }).Count -gt 0) {
        $known = @($probe | Where-Object { $_ -like "$ModelId`t*" })
        if ($known.Count -eq 0) {
            throw "preflight: model '$ModelId' is not offered by the agy backend; use an alias (flash, flash-med, flash-hi, pro, sonnet, opus, gpt) or an id from 'agy models'"
        }
    }
}

switch ($Action) {
    # Standalone check for the operator: same preflight the spawn path runs.
    'preflight' { Test-WorkerBackend $Worker $modelId; if ($? -and $Worker -eq 'agy') { Write-Output "preflight: $Worker backend usable (model $modelId)" } }
    'spawn' {
        if (-not $Force) { Assert-LocalHeadroom }
        New-Item -ItemType Directory -Force (Join-Path $opsWin 'inbox') | Out-Null
        $inbox = Join-Path $opsWin ('inbox\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.md')
        if ($File) { Copy-Item $File $inbox }
        elseif ($Arg) { [IO.File]::WriteAllText($inbox, $Arg) }
        else { throw 'spawn needs "task text" or -File path' }
        $inboxWsl = "$opsWsl/inbox/" + (Split-Path $inbox -Leaf)
        # Fail the spawn before any worker starts if the backend is known-bad.
        Test-WorkerBackend $Worker $modelId
        # Headless, autonomous command per worker; the prompt is appended as the
        # last argument, so every flag goes before -p.
        $flags = "--dangerously-skip-permissions --model $modelId"
        if ($Effort) { $flags += " --effort $Effort" }
        # Both workers run headless and unattended (user authorization 2026-09-20):
        # scope is set by the task prompt, and .agent-ops/win.sh still blocks
        # flash/reset/halt/poke with exit 126 no matter what the worker asks for.
        $cmd = @{
            agy      = "agy $flags -p"
            ark      = "claude --dangerously-skip-permissions --model $modelId -p"
            oc       = "$opsWsl/oc-worker.sh $modelId"
        }[$Worker]
        Write-Output "model: $modelId$(if ($Effort) { " effort: $Effort" })"
        # oc is the free pool, so it runs the most and is trusted the least;
        # and it is the only worker whose Serena is writable, which it can only
        # be safely inside a worktree (spawn-worker.sh:231). Default it into
        # .worktrees/<id> so a bad edit never lands in the main checkout.
        # -Worktree:$false opts out; the other workers are unchanged.
        $useWorktree = if ($PSBoundParameters.ContainsKey('Worktree')) {
            [bool]$Worktree
        } else { $Worker -eq 'oc' }
        $wtFlag = if ($useWorktree) { ' -w' } else { '' }
        if ($useWorktree) { Write-Output "worktree: .worktrees/<task-id> (isolated)" }
        # Whole-tree snapshot (untracked included, OBJ excluded) built in a
        # throwaway index before the worker starts. 'verify' diffs against it,
        # so edits to files that were already dirty at spawn still show. ~1 s.
        $repo = Split-Path $opsWin
        $tmpIdx = Join-Path $env:TEMP ('agent-ops-' + [guid]::NewGuid() + '.index')
        Copy-Item (Join-Path $repo '.git\index') $tmpIdx
        $env:GIT_INDEX_FILE = $tmpIdx
        git -C $repo -c core.safecrlf=false add -A -- . ':(exclude)OBJ'
        $preTree = git -C $repo write-tree
        Remove-Item Env:GIT_INDEX_FILE
        Remove-Item $tmpIdx
        $out = Invoke-Wsl "AGY_CMD='$cmd' AGY_TIMEOUT=$($TimeoutMin * 60) '$opsWsl/spawn-worker.sh' -f '$inboxWsl'$wtFlag"
        $out
        # Baseline so 'wait' can show only what the worker changed.
        if ("$out" -match 'spawned (\d{8}-\d{6})') {
            git -C (Split-Path $opsWin) status --short -- . ':(exclude)OBJ' |
                Set-Content -Encoding utf8 (Join-Path $opsWin "tasks\$($Matches[1]).pre-status")
            [IO.File]::WriteAllText((Join-Path $opsWin "tasks\$($Matches[1]).pre-tree"), $preTree)
        }
        & $PSCommandPath monitor
    }
    # One-shot read-only lookup. The supervisor pays for the answer only, not
    # for reading the files; plan mode keeps the worker from editing anything.
    'ask' {
        if (-not $Arg -and -not $File) { throw 'ask needs "question" or -File path' }
        New-Item -ItemType Directory -Force (Join-Path $opsWin 'inbox') | Out-Null
        $q = Join-Path $opsWin ('inbox\ask-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.md')
        $text = if ($File) { Get-Content $File -Raw } else { $Arg }
        [IO.File]::WriteAllText($q, $text + @"


---
Answer in at most $Lines lines. Findings only, each with a file:line reference;
quote at most 3 lines per file. Read only what the question needs. Change nothing.
"@)
        $qWsl = "$opsWsl/inbox/" + (Split-Path $q -Leaf)
        $projWsl = ($opsWsl -replace '/[^/]+$', '')
        # 'ask' hardcoded agy while $modelId was already rewritten by -Worker, so
        # 'ask -Worker ark' handed agy an ark model id and died on "invalid model
        # selection" - a routing bug that reads exactly like a dead backend.
        # Dispatch on $Worker the way 'spawn' does. Read-only on both sides comes
        # from plan mode: --mode plan for agy, --permission-mode plan for ark.
        $askCmd = @{
            agy = "agy --dangerously-skip-permissions --mode plan --model $modelId"
            ark = "claude --permission-mode plan --model $modelId"
        }[$Worker]
        # ark cannot start inside this repo - the git index refresh hangs it.
        # Same non-repo cwd + --add-dir workaround as spawn (Test-WorkerBackend).
        $askCwd = $projWsl
        if ($Worker -eq 'ark') { $askCwd = '/tmp/arkwork/ask'; $askCmd += " --add-dir $projWsl" }
        Write-Output "ask: worker=$Worker model=$modelId cwd=$askCwd"
        Invoke-Wsl "mkdir -p '$askCwd' && cd '$askCwd' && timeout $($TimeoutMin * 60) $askCmd -p `"`$(cat '$qWsl')`""
    }
    # Quake drop-down (Win+` toggles it once open) attached to the tmux session.
    'attach' { Start-Process wt.exe -ArgumentList '-w', '_quake', 'wsl.exe', '-d', 'Ubuntu', '--', 'tmux', 'new', '-A', '-s', 'agent-ops' }
    'monitor' {
        # Single instance (mutex inside); a second start exits at once.
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $opsWin 'monitor-window.ps1')
    }
    'wait' {
        if ($Arg -notmatch '^\d{8}-\d{6}$') { throw 'wait needs a task id like 20260919-132708' }
        # Silent wait in WSL; the digest below is all the supervisor reads.
        Invoke-Wsl "'$opsWsl/wait-task.sh' '$Arg' $(($TimeoutMin + 10) * 60)"
        $waitRc = $LASTEXITCODE
        # First line of the digest is the verdict: wait-task.sh returns
        # 0 success / 2 worker failed / 3 gave up / 4 needs attention.
        $taskLog = @(Get-Content (Join-Path $opsWin 'state.log') | Where-Object { $_.Contains("[$Arg]") })
        $exitLine = @($taskLog | Where-Object { $_ -match 'EXIT: rc=(\d+)' } | Select-Object -Last 1)
        $attnLine = @($taskLog | Where-Object { $_ -match '\s(BLOCKED|STALLED|NEEDS_INPUT|NET_DOWN|LOOP):' } | Select-Object -Last 1)
        if ($waitRc -eq 0) {
            $outcome = 'OUTCOME: success (rc=0)'
        } elseif ($waitRc -eq 5) {
            $outcome = 'OUTCOME: idle death (rc=0 but no result file) - worker was likely terminated while idle, NOT a clean finish; it may still have made real edits, check the working tree'
        } elseif ($waitRc -eq 2) {
            $failedRc = if ($exitLine.Count -gt 0 -and $exitLine[0] -match 'EXIT: rc=(\d+)') { $Matches[1] } else { '?' }
            $outcome = "OUTCOME: worker failed (rc=$failedRc)"
        } elseif ($waitRc -eq 3) {
            $outcome = 'OUTCOME: wait gave up'
        } elseif ($waitRc -eq 4) {
            $attnKind = if ($attnLine.Count -gt 0 -and $attnLine[0] -match '\s(BLOCKED|STALLED|NEEDS_INPUT|NET_DOWN|LOOP):') { $Matches[1] } else { 'ATTENTION' }
            $outcome = "OUTCOME: needs attention ($attnKind)"
        } else {
            $outcome = "OUTCOME: unknown wait-task exit code $waitRc"
        }
        Write-Output $outcome
        Write-Output "== state [$Arg]"
        Get-Content (Join-Path $opsWin 'state.log') | Where-Object { $_.Contains("[$Arg]") } | Select-Object -Last 8
        Write-Output '== result'
        $result = Join-Path $opsWin "tasks\$Arg.result.md"
        if (Test-Path $result) { Get-Content $result -TotalCount 30 } else { 'no result file yet' }
        # A -Worktree spawn works in .worktrees/<id>, which .gitignore hides
        # from the main checkout entirely: status here reported nothing for
        # such a task no matter how much it rewrote. Follow the worker.
        $wtDir = Join-Path (Split-Path $opsWin) ".worktrees\$Arg"
        if (Test-Path $wtDir) {
            Write-Output "== files changed in .worktrees\$Arg (worktree spawn)"
            git -C $wtDir status --short -- . ':(exclude)OBJ' | Select-Object -First 20
        } else {
            Write-Output '== files changed since spawn (OBJ/ excluded; files already dirty then are not shown)'
            $pre = Join-Path $opsWin "tasks\$Arg.pre-status"
            $before = if (Test-Path $pre) { @(Get-Content $pre) } else { @() }
            git -C (Split-Path $opsWin) status --short -- . ':(exclude)OBJ' |
                Where-Object { $before -notcontains $_ } | Select-Object -First 20
        }
        $global:LASTEXITCODE = $waitRc
    }
    'verify' {
        if ($Arg -notmatch '^\d{8}-\d{6}$') { throw 'verify needs a task id like 20260919-132708' }
        # All checks live in verify_task.py; exit 0 PASS, 1 WARN, 2 FAIL.
        python (Join-Path $opsWin 'verify_task.py') $Arg
    }
    'status' {
        # A tmux window stays open after its worker exits (scrollback is kept on
        # purpose), so the window list is NOT a liveness signal. Report both:
        # the windows, for attach; and the tasks state.log says are still going.
        Invoke-Wsl ("'$opsWsl/monitor.sh'" +
            "; echo '--- tmux windows (open does not mean running)'" +
            "; tmux list-windows -t agent-ops -F '#{window_id} #{window_name}' 2>/dev/null | grep agy-worker || echo none" +
            "; echo '--- running tasks (live processes)'" +
            "; pgrep -af run-worker.sh 2>/dev/null | grep -o '[0-9]\{8\}-[0-9]\{6\}' | sort -u | grep . || echo none")
    }
    'output' {
        $log = if ($Arg) { "'$opsWsl/logs/$Arg.out'" } else { "`$(ls -t '$opsWsl'/logs/*.out | head -1)" }
        Invoke-Wsl "f=$log; echo `"== `$f`"; tail -n $Lines `"`$f`""
    }
    'kill' {
        if ($Arg -eq 'all') { Invoke-Wsl "tmux list-windows -t agent-ops -F '#{window_id} #{window_name}' | awk '`$2==`"agy-worker`"{print `$1}' | xargs -r -n1 tmux kill-window -t" }
        elseif ($Arg) { Invoke-Wsl "tmux kill-window -t '$Arg'" }
        else { throw 'kill needs a window id (from status) or all' }
    }
    # Reap windows spawns leave behind (never @0, never a live worker) and
    # age out tasks/ and logs/. Kept manual on purpose: scrollback is useful.
    'prune' {
        $bash = @'
OPS='__OPS__'
RETENTION_DAYS='__DAYS__'
closed=0
kept=0
is_descendant() {
    child=$1
    while [ "$child" != "1" ] && [ -n "$child" ]; do
        [ "$child" = "$2" ] && return 0
        child="$(ps -o ppid= -p "$child" 2>/dev/null | tr -d ' ')"
    done
    return 1
}
if tmux has-session -t agent-ops 2>/dev/null; then
    rw_pids="$(pgrep -f 'run-worker\.sh [0-9]{8}-[0-9]{6}' || true)"
    targets=""
    while IFS=' ' read -r widx wid wname pane_pid; do
        [ "$widx" = "0" ] && continue
        [ "$wname" = "agy-worker" ] || continue
        live_here=0
        for rwp in $rw_pids; do
            if is_descendant "$rwp" "$pane_pid"; then live_here=1; break; fi
        done
        if [ "$live_here" -eq 0 ]; then
            targets="$targets $wid"
        else
            kept=$((kept+1))
        fi
    done < <(tmux list-windows -t agent-ops -F '#{window_index} #{window_id} #{window_name} #{pane_pid}')
    for wid in $targets; do
        tmux kill-window -t "$wid" && closed=$((closed+1))
    done
fi
echo "prune: closed $closed dead agy-worker window(s); kept $kept live; @0 never touched"
live_ids="$(pgrep -af 'run-worker\.sh [0-9]{8}-[0-9]{6}' | grep -oE '[0-9]{8}-[0-9]{6}' | sort -u || true)"
cutoff_mins=$((RETENTION_DAYS * 1440))
deleted=0
kept_live=0
while IFS= read -r f; do
    id="$(basename "$f")"
    id="${id:0:15}"
    if printf '%s\n' "$live_ids" | grep -Fxq "$id"; then
        kept_live=$((kept_live+1))
    else
        rm -f -- "$f" && deleted=$((deleted+1))
    fi
done < <(find "$OPS/tasks" "$OPS/logs" -maxdepth 1 -type f -mmin "+$cutoff_mins" 2>/dev/null)
echo "prune: deleted $deleted task/log file(s) older than $RETENTION_DAYS day(s); kept $kept_live for live tasks"
'@
        Invoke-Wsl $bash.Replace('__OPS__', $opsWsl).Replace('__DAYS__', [string]$RetentionDays)
    }
}
exit $LASTEXITCODE
