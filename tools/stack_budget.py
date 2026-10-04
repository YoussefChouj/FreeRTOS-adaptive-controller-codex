"""Static stack budget of every FreeRTOS task, from the Keil call graph.

Reads OBJ/JX_FLY.htm (written by every Keil link, "[Stack] Max Depth" per function), the task stack sizes
(Global_file/creat_task.h, USER/main.c xTaskCreate calls, FreeRTOSConfig.h idle/timer depth) and prints, per
task: allocated bytes, call-graph depth, depth + context frame, and the margin. Fails if a task's depth plus
context frame exceeds its allocation.

"+U" marks a depth that the linker could not bound (function pointers, recursion, assembler without stack
info). Those numbers are lower bounds: confirm on the bench with telemetry `hlth.stack_min_words`
(min `usStackHighWaterMark` over tasks) before shrinking any stack. See docs/firmware-stack-budget.md.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTM = ROOT / 'OBJ' / 'JX_FLY.htm'
TASK_H = ROOT / 'Global_file' / 'creat_task.h'
MAIN_C = ROOT / 'USER' / 'main.c'
CONFIG_H = ROOT / 'FreeRTOS' / 'include' / 'FreeRTOSConfig.h'
WORD = 4                       # FreeRTOS stack depth unit on Cortex-M [bytes]
# Context saved on the task stack by the ARM_CM4F port at a switch, worst case (task used the FPU):
# hardware frame 8 + 18 FP words, PendSV pushes r4-r11, r14 (9) + s16-s31 (16) = 51 words.
CONTEXT_FRAME = 51 * WORD      # [bytes]
# Known overruns (finding, fix PROPOSED on a branch): reported, not failed. Shrink only.
KNOWN_OVER = {
    'start_task': 'only on the malloc-failed path (vApplicationMallocFailedHook -> FaultCapture_Record)',
}


def read(p):
    return p.read_bytes().decode('latin-1')


def depths(htm):
    """Map function name -> (max depth bytes, unbounded flag)."""
    out = {}
    for m in re.finditer(r'<STRONG><a name="\[\w+\]"></a>(\w+)</STRONG>(.*?)(?=<P><STRONG>|\Z)', htm, re.S):
        body = m.group(2)
        d = re.search(r'Max Depth = (\d+)([^<]*)', body)
        if d:
            out[m.group(1)] = (int(d.group(1)), 'Unknown' in d.group(2))
            continue
        s = re.search(r'Stack size (\d+) bytes', body)
        if s:
            out[m.group(1)] = (int(s.group(1)), False)
    return out


def tasks():
    """Yield (task function, stack words) for every task the firmware creates."""
    macros = {k: int(v) for k, v in re.findall(r'#define\s+(\w+_STK_SIZE)\s+(\d+)', read(TASK_H))}
    main = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', read(MAIN_C), flags=re.S)
    for call in main.split('xTaskCreate(')[1:]:
        fn = re.search(r'\(TaskFunction_t\s*\)\s*(\w+)', call)
        sz = re.search(r'\(uint16_t\s*\)\s*(\w+)', call)
        if fn and sz:
            words = int(sz.group(1)) if sz.group(1).isdigit() else macros.get(sz.group(1))
            yield fn.group(1), words
    cfg = read(CONFIG_H)
    minimal = int(re.search(r'#define\s+configMINIMAL_STACK_SIZE\s+\(\(unsigned short\)(\d+)\)', cfg).group(1))
    yield 'prvIdleTask', minimal
    if re.search(r'#define\s+configUSE_TIMERS\s+1', cfg):
        yield 'prvTimerTask', minimal * 2   # configTIMER_TASK_STACK_DEPTH = configMINIMAL_STACK_SIZE*2


def main():
    htm = read(HTM)
    stamp = re.search(r'Last Updated: ([^\n<]+)', htm)
    print('stack-budget: call graph %s (%s)' % (HTM.relative_to(ROOT).as_posix(), stamp.group(1) if stamp else '?'))
    print('%-20s %8s %8s %10s %8s' % ('task', 'alloc B', 'depth B', '+frame B', 'margin'))
    dep = depths(htm)
    bad = 0
    for fn, words in tasks():
        if fn not in dep:
            print('%-20s %8s %8s %10s %8s' % (fn, words * WORD if words else '?', '-', '-', 'not linked'))
            continue
        alloc = words * WORD
        d, unk = dep[fn]
        need = d + CONTEXT_FRAME
        print('%-20s %8d %6d%-2s %10d %7.0f%%' % (fn, alloc, d, '+U' if unk else '', need, 100.0 * (alloc - need) / alloc))
        if need > alloc and fn in KNOWN_OVER:
            print('  known: %s' % KNOWN_OVER[fn])
        else:
            bad += need > alloc
    print('stack-budget: %s (frame %d B per task; +U = lower bound, check hlth.stack_min_words)'
          % ('FAIL: %d task(s) over' % bad if bad else 'OK', CONTEXT_FRAME))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
