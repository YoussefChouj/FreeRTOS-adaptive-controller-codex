# win-run.ps1 - run one command for win.sh inside a Windows Job Object.
# WSL interop does not kill the Windows side when a worker's tool call times
# out, so orphaned python/pytest processes used to pile up and exhaust RAM.
# Here the whole process tree is bounded: killed after -TimeoutSec, and
# allocations fail once the tree commits more than -MemMB.
param(
    [Parameter(Mandatory = $true)][string]$EncodedCmd,
    [int]$TimeoutSec = 600,
    [int]$MemMB = 1536
)

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AgentOpsJob {
    [StructLayout(LayoutKind.Sequential)]
    struct BASIC {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IO { public ulong a, b, c, d, e, f; }
    [StructLayout(LayoutKind.Sequential)]
    struct EXTENDED {
        public BASIC Basic;
        public IO Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int cls, ref EXTENDED info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] public static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] static extern bool QueryInformationJobObject(IntPtr job, int cls, out EXTENDED info, uint size, IntPtr ret);

    public static ulong PeakBytes(IntPtr job) {
        EXTENDED info;
        if (!QueryInformationJobObject(job, 9, out info, (uint)Marshal.SizeOf(typeof(EXTENDED)), IntPtr.Zero)) return 0;
        return info.PeakJobMemoryUsed.ToUInt64();
    }

    // Put this process in a new job; every child it starts inherits it.
    public static IntPtr Enter(ulong memBytes) {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        EXTENDED info = new EXTENDED();
        info.Basic.LimitFlags = 0x200 | 0x2000;   // JOB_MEMORY | KILL_ON_JOB_CLOSE
        info.JobMemoryLimit = new UIntPtr(memBytes);
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(typeof(EXTENDED))))
            throw new System.ComponentModel.Win32Exception();
        if (!AssignProcessToJobObject(job, GetCurrentProcess()))
            throw new System.ComponentModel.Win32Exception();
        return job;
    }
}
'@

$job = [AgentOpsJob]::Enter([uint64]$MemMB * 1MB)
$p = Start-Process powershell.exe -ArgumentList '-NoProfile', '-EncodedCommand', $EncodedCmd -NoNewWindow -PassThru
[void]$p.Handle   # cache the handle so ExitCode is readable after exit
$done = $p.WaitForExit($TimeoutSec * 1000)
# One line per command: peak commit of the whole tree, for sizing the limits.
$cmd = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($EncodedCmd)) -replace '^\$ProgressPreference = "SilentlyContinue"; ', ''
$line = '{0} peak={1}MB cap={2}MB rc={3} {4}' -f (Get-Date -Format s), [int]([AgentOpsJob]::PeakBytes($job) / 1MB), $MemMB,
    $(if ($done) { $p.ExitCode } else { 124 }), $cmd.Substring(0, [Math]::Min(120, $cmd.Length))
try { Add-Content -Path (Join-Path $PSScriptRoot 'logs\win-mem.log') -Value $line -Encoding utf8 } catch {}
if (-not $done) {
    [Console]::Error.WriteLine("win.sh: killed after ${TimeoutSec}s (WIN_TIMEOUT); whole process tree terminated")
    [void][AgentOpsJob]::TerminateJobObject($job, 124)
}
exit $p.ExitCode
