"""Static stack budget of every FreeRTOS task, from the Keil call graph.

Reads OBJ/JX_FLY.htm (written by every Keil link, "[Stack] Max Depth" per function), the task stack sizes
(Global_file/creat_task.h, USER/main.c xTaskCreate calls, FreeRTOSConfig.h idle/timer depth) and prints, per
task: allocated bytes, call-graph depth, depth + context frame, and the margin. Fails if a task's depth plus
context frame exceeds its allocation.

"+U" marks a depth that the linker could not bound (function pointers, recursion, assembler without stack
info). Those numbers are lower bounds: confirm on the bench with telemetry `hlth.stack_min_words`
(min `usStackHighWaterMark` over tasks) before shrinking any stack. See docs/firmware-stack-budget.md.

Second check, the interrupt stack (MSP): every NVIC_Init call in BSP/*.c gives a handler its preemption
priority. Handlers at the same priority do not nest, so the worst case is the deepest handler of each priority
level stacked on each other, plus one exception frame per nested level. Also fails if a handler above the
FreeRTOS syscall ceiling (priority number < configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY) reaches kernel code.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTM = ROOT / 'OBJ' / 'JX_FLY.htm'
TASK_H = ROOT / 'Global_file' / 'creat_task.h'
MAIN_C = ROOT / 'USER' / 'main.c'
CONFIG_H = ROOT / 'FreeRTOS' / 'include' / 'FreeRTOSConfig.h'
STARTUP = ROOT / 'stm32_lib' / 'startup_stm32f40_41xxx.s'
WORD = 4                       # FreeRTOS stack depth unit on Cortex-M [bytes]
# Context saved on the task stack by the ARM_CM4F port at a switch, worst case (task used the FPU):
# hardware frame 8 + 18 FP words, PendSV pushes r4-r11, r14 (9) + s16-s31 (16) = 51 words.
CONTEXT_FRAME = 51 * WORD      # [bytes]
# Exception entry frame with FP state (8 + 18 words) plus the 8-byte alignment word, pushed on the MSP by every
# nested exception (the first one from a task goes on that task's PSP).
EXC_FRAME = 27 * WORD          # [bytes]
KERNEL_OBJS = {'tasks', 'queue', 'list', 'timers', 'event_groups', 'port', 'heap_4', 'croutine', 'stream_buffer'}
# Known overruns (finding, fix PROPOSED on a branch): reported, not failed. Shrink only.
KNOWN_OVER = {
    'start_task': 'only on the malloc-failed path (vApplicationMallocFailedHook -> FaultCapture_Record)',
}


def read(p):
    return p.read_bytes().decode('latin-1')


def graph(htm):
    """Map function name -> (max depth bytes, unbounded flag, object file, callees)."""
    out = {}
    for m in re.finditer(r'<STRONG><a name="\[\w+\]"></a>(\w+)</STRONG>(.*?)(?=<P><STRONG>|\Z)', htm, re.S):
        body = m.group(2)
        obj = re.search(r'(\w+)\.o\(', body)
        calls = re.search(r'\[Calls\]<UL>(.*?)</UL>', body, re.S)
        calls = re.findall(r'&nbsp;&nbsp;&nbsp;(\w+)', calls.group(1)) if calls else []
        d = re.search(r'Max Depth = (\d+)([^<]*)', body)
        s = re.search(r'Stack size (\d+) bytes', body)
        if d:
            out[m.group(1)] = (int(d.group(1)), 'Unknown' in d.group(2), obj and obj.group(1), calls)
        elif s:
            out[m.group(1)] = (int(s.group(1)), False, obj and obj.group(1), calls)
    return out


def reach(g, fn):
    """Every function reachable from fn in the call graph."""
    seen, todo = set(), [fn]
    while todo:
        for c in g.get(todo.pop(), (0, 0, 0, []))[3]:
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return seen


def irq_priorities(cfg):
    """Yield (handler, preemption priority) from every NVIC_Init in BSP/*.c, plus the kernel's own handlers."""
    for f in sorted((ROOT / 'BSP').glob('*.c')):
        src = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', read(f), flags=re.S)
        chan = prio = None
        for m in re.finditer(r'NVIC_IRQChannel\s*=\s*(\w+)|NVIC_IRQChannelPreemptionPriority\s*=\s*(\d+)|NVIC_Init\s*\(', src):
            if m.group(1):
                chan = m.group(1)
            elif m.group(2):
                prio = int(m.group(2))
            elif chan and prio is not None:
                yield chan.replace('_IRQn', '_IRQHandler'), prio
    lowest = int(re.search(r'#define\s+configLIBRARY_LOWEST_INTERRUPT_PRIORITY\s+(\d+)', cfg).group(1))
    yield 'SysTick_Handler', lowest
    yield 'PendSV_Handler', lowest


def msp_check(g, cfg):
    """Print the MSP nesting bound; return the number of failures."""
    msp = int(re.search(r'Stack_Size\s+EQU\s+(0x[0-9A-Fa-f]+|\d+)', read(STARTUP)).group(1), 0)
    ceiling = int(re.search(r'#define\s+configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY\s+(\d+)', cfg).group(1))
    level, bad = {}, 0
    print('msp-budget: MSP %d B, syscall ceiling priority %d' % (msp, ceiling))
    for h, p in sorted(set(irq_priorities(cfg)), key=lambda x: (x[1], x[0])):
        if h not in g:
            print('  prio %2d %-24s not linked' % (p, h))
            continue
        d, unk = g[h][0], g[h][1]
        kern = sorted(f for f in reach(g, h) if g.get(f, (0, 0, None))[2] in KERNEL_OBJS)
        print('  prio %2d %-24s %4d%-2s%s' % (p, h, d, '+U' if unk else '', ' kernel: ' + ', '.join(kern[:3]) if kern else ''))
        if p < ceiling and kern:
            print('  FAIL: %s runs above the syscall ceiling and reaches the kernel' % h)
            bad += 1
        if d > level.get(p, (-1,))[0]:
            level[p] = (d, h)
    nest = sum(d for d, _ in level.values()) + (len(level) - 1) * EXC_FRAME
    fault = g['HardFault_Handler'][0] + EXC_FRAME
    print('  nested worst case: %d levels, %d B (%.0f %% of MSP); + HardFault on top: %d B'
          % (len(level), nest, 100.0 * nest / msp, nest + fault))
    if nest > msp:
        print('  FAIL: nested interrupts can overflow the MSP')
        bad += 1
    elif nest + fault > msp:
        print('  known: a fault inside the deepest nesting overflows (FaultCapture_Record 512 B local, fix PROPOSED)')
    return bad


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
    dep = graph(htm)
    bad = 0
    for fn, words in tasks():
        if fn not in dep:
            print('%-20s %8s %8s %10s %8s' % (fn, words * WORD if words else '?', '-', '-', 'not linked'))
            continue
        alloc = words * WORD
        d, unk = dep[fn][:2]
        need = d + CONTEXT_FRAME
        print('%-20s %8d %6d%-2s %10d %7.0f%%' % (fn, alloc, d, '+U' if unk else '', need, 100.0 * (alloc - need) / alloc))
        if need > alloc and fn in KNOWN_OVER:
            print('  known: %s' % KNOWN_OVER[fn])
        else:
            bad += need > alloc
    bad += msp_check(dep, read(CONFIG_H))
    print('stack-budget: %s (frame %d B per task; +U = lower bound, check hlth.stack_min_words)'
          % ('FAIL: %d check(s)' % bad if bad else 'OK', CONTEXT_FRAME))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
