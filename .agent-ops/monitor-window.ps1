# monitor-window.ps1 - small always-on-top window showing worker health.
# Started automatically by 'agent-ops.ps1 spawn' (one instance at a time),
# or by hand: .agent-ops\agent-ops.ps1 monitor
# Reads state.log and logs\<id>.out directly; costs the supervisor nothing.
# Pops a tray notification on STALLED / NEEDS_INPUT / NET_DOWN / LOOP /
# BLOCKED / FAILED. Double-click a row to follow that worker's output live.
# Closes itself after 15 min with no live worker.

$ErrorActionPreference = 'SilentlyContinue'
$mutex = New-Object Threading.Mutex($false, 'Global\agent-ops-monitor')
if (-not $mutex.WaitOne(0)) { exit 0 }

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
# Launched with -WindowStyle Hidden (no console); Windows applies that to the
# first window shown, i.e. this form, so re-show it explicitly once created.
Add-Type -Namespace AgentOps -Name Win -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);'
$ops = $PSScriptRoot
$stateLog = Join-Path $ops 'state.log'
$alertKinds = 'STALLED|NEEDS_INPUT|NET_DOWN|LOOP|BLOCKED|FAILED'

$form = New-Object Windows.Forms.Form
$form.Text = 'agent-ops workers'
$form.TopMost = $true
$form.FormBorderStyle = 'Sizable'
$form.Size = New-Object Drawing.Size(700, 240)
$form.BackColor = [Drawing.Color]::FromArgb(40, 44, 52)
$wa = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.StartPosition = 'CenterScreen'

$list = New-Object Windows.Forms.ListView
$list.Dock = 'Fill'
$list.View = 'Details'
$list.FullRowSelect = $true
$list.GridLines = $true
$list.BorderStyle = 'None'
$list.Font = New-Object Drawing.Font('Segoe UI', 10)
$list.BackColor = [Drawing.Color]::FromArgb(30, 30, 30)
foreach ($c in @(@('task', 80), @('state', 105), @('status age', 85), @('output age', 85), @('last message', 320))) {
    [void]$list.Columns.Add($c[0], $c[1])
}
$list.ShowItemToolTips = $true
# Double-click: follow that task's output live in a terminal tab.
$list.Add_DoubleClick({
    $sel = $list.SelectedItems
    if ($sel.Count -eq 0) { return }
    $id = $sel[0].Tag
    $cmd = "tail -n 300 -F '/mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/.agent-ops/logs/$id.out'"
    $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($wt) { Start-Process wt.exe -ArgumentList '-w', 'agent-ops', 'new-tab', '--title', $id, 'wsl.exe', '-d', 'Ubuntu', '--', 'bash', '-c', "`"$cmd`"" }
    else { Start-Process wsl.exe -ArgumentList '-d', 'Ubuntu', '--', 'bash', '-c', "`"$cmd`"" }
})
# Full text of the selected (or first) row's message, wrapped, under the list.
$detail = New-Object Windows.Forms.TextBox
$detail.Multiline = $true; $detail.ReadOnly = $true; $detail.WordWrap = $true
$detail.Dock = 'Bottom'; $detail.Height = 50; $detail.BorderStyle = 'None'
$detail.Font = New-Object Drawing.Font('Segoe UI', 10)
$detail.BackColor = [Drawing.Color]::FromArgb(20, 20, 20); $detail.ForeColor = [Drawing.Color]::PaleGreen
function Update-Detail {
    $it = if ($list.SelectedItems.Count) { $list.SelectedItems[0] } elseif ($list.Items.Count) { $list.Items[0] } else { $null }
    $detail.Text = if ($it) { "$($it.Tag): $($it.ToolTipText)" } else { '' }
}
$list.Add_SelectedIndexChanged({ Update-Detail })
# Last column takes whatever width is left, so resizing reveals the message.
function Fit-Columns {
    $w = $list.ClientSize.Width
    for ($k = 0; $k -lt $list.Columns.Count - 1; $k++) { $w -= $list.Columns[$k].Width }
    $list.Columns[$list.Columns.Count - 1].Width = [Math]::Max(120, $w)
}
$list.Add_Resize({ Fit-Columns })
$form.Controls.Add($list)
$form.Controls.Add($detail)

$tray = New-Object Windows.Forms.NotifyIcon
$tray.Icon = [Drawing.SystemIcons]::Information
$tray.Visible = $true
$tray.Text = 'agent-ops monitor'

$script:alerted = @{}      # "<id>|<line>" -> seen, so each alert pops once
$script:startLines = @(Get-Content $stateLog).Count
$script:idleSince = Get-Date

function Format-Age([TimeSpan]$t) {
    if ($t.TotalSeconds -lt 90) { return '{0}s' -f [int]$t.TotalSeconds }
    if ($t.TotalMinutes -lt 90) { return '{0}m' -f [int]$t.TotalMinutes }
    return '{0}h' -f [int]$t.TotalHours
}

function Update-View {
    $lines = @(Get-Content $stateLog)
    $now = [DateTimeOffset]::Now
    $tasks = [ordered]@{}
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -notmatch '^(\S+) \[(\d{8}-\d{6})\] (\w+): ?(.*)$') { continue }
        $ts = [DateTimeOffset]::Parse($Matches[1]); $id = $Matches[2]; $kind = $Matches[3]; $msg = $Matches[4]
        if (-not $tasks.Contains($id)) { $tasks[$id] = @{ kind = ''; msg = ''; status = $ts; exited = $null; alert = '' } }
        $t = $tasks[$id]
        if ($kind -eq 'EXIT') { $t.exited = $ts; $t.kind = "EXIT $msg"; continue }
        $t.kind = $kind; $t.msg = $msg
        if ($kind -match "^($alertKinds)$") {
            $t.alert = $kind
            $key = "$id|$i"
            if ($i -ge $script:startLines -and -not $script:alerted.ContainsKey($key)) {
                $script:alerted[$key] = $true
                $tray.ShowBalloonTip(8000, "worker $id : $kind", $msg, 'Warning')
                [Media.SystemSounds]::Exclamation.Play()
            }
        } elseif ($kind -ne 'QUIET') {
            $t.status = $ts; $t.alert = ''
        }
    }

    $keepSel = if ($list.SelectedItems.Count) { $list.SelectedItems[0].Tag } else { $null }
    $list.BeginUpdate()
    $list.Items.Clear()
    $live = 0
    foreach ($id in $tasks.Keys) {
        $t = $tasks[$id]
        if ($t.exited -and ($now - $t.exited).TotalMinutes -gt 10) { continue }
        # No EXIT and silent for hours: killed window, not a live worker.
        if (-not $t.exited -and ($now - $t.status).TotalHours -gt 3) { continue }
        $out = Join-Path $ops "logs\$id.out"
        $outAge = if (Test-Path $out) { Format-Age ((Get-Date) - (Get-Item $out).LastWriteTime) } else { '-' }
        $row = New-Object Windows.Forms.ListViewItem($id.Substring(9))
        foreach ($v in @($t.kind, (Format-Age ($now - $t.status)), $outAge, $t.msg)) { [void]$row.SubItems.Add([string]$v) }
        $row.ForeColor = if ($t.exited) { [Drawing.Color]::Gray }
                         elseif ($t.alert) { [Drawing.Color]::OrangeRed }
                         elseif ($t.kind -eq 'QUIET') { [Drawing.Color]::Gold }
                         else { [Drawing.Color]::LightGreen }
        $row.ToolTipText = $t.msg
        $row.Tag = $id
        [void]$list.Items.Add($row)
        if (-not $t.exited) { $live++ }
    }
    $selId = $keepSel
    $list.EndUpdate()
    if ($selId) { foreach ($it in $list.Items) { if ($it.Tag -eq $selId) { $it.Selected = $true } } }
    Fit-Columns
    Update-Detail
    $form.Text = "agent-ops workers - $live live - $(Get-Date -Format HH:mm:ss)"
    if ($live -gt 0) { $script:idleSince = Get-Date }
    elseif (((Get-Date) - $script:idleSince).TotalMinutes -gt 15) { $form.Close() }
}

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ Update-View })
$timer.Start()
$form.Add_Shown({ [void][AgentOps.Win]::ShowWindow($form.Handle, 5); Update-View })
$form.Add_FormClosed({ $timer.Stop(); $tray.Visible = $false; $tray.Dispose() })
[Windows.Forms.Application]::Run($form)
$mutex.ReleaseMutex()
