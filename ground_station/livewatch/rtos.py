from __future__ import annotations
import time
import sys

def cmd_rtos(args):
    from .reader import LiveReader
    from .transport import SwdCmsisDap
    
    t = getattr(args, "transport", "swd")
    limit_packets = not getattr(args, "swd_no_limit_packets", False)
    
    with LiveReader(args.elf, transport=SwdCmsisDap(limit_packets=limit_packets)) as lr:
        try:
            vars_to_read = [
                "g_task_snapshot", "g_task_snapshot_count", "g_task_snapshot_total_time",
                "g_heap_free", "g_heap_min_free", "g_reset_csr",
                "g_loop_period_cyc_last", "g_loop_period_cyc_max",
                "g_loop_period_cyc_min", "g_loop_overrun_count"
            ]
            
            # Read first time
            res1 = lr.read_many(vars_to_read)
            
            # Wait a bit
            time.sleep(1.0)
            
            # Read second time
            res2 = lr.read_many(vars_to_read)
            
        except Exception as e:
            print(f"Failed to read RTOS state: {e}")
            return 1
            
        # Compute CPU% per task
        t1_total = res1.get("g_task_snapshot_total_time", 0)
        t2_total = res2.get("g_task_snapshot_total_time", 0)
        dt = t2_total - t1_total
        
        t1_tasks = res1.get("g_task_snapshot", [])
        t2_tasks = res2.get("g_task_snapshot", [])
        t1_count = res1.get("g_task_snapshot_count", 0)
        t2_count = res2.get("g_task_snapshot_count", 0)
        
        t1_map = {t.get("pcTaskName", b"").decode("ascii", "ignore").strip("\x00"): t for t in t1_tasks[:t1_count]}
        t2_map = {t.get("pcTaskName", b"").decode("ascii", "ignore").strip("\x00"): t for t in t2_tasks[:t2_count]}
        
        print(f"{'Task':<20} | {'Prio':<4} | {'State':<5} | {'CPU%':<6} | {'Stack HWM':<9}")
        print("-" * 55)
        for name, t2 in sorted(t2_map.items()):
            t1 = t1_map.get(name, t2)
            run1 = t1.get("ulRunTimeCounter", 0)
            run2 = t2.get("ulRunTimeCounter", 0)
            
            cpu = 0.0
            if dt > 0:
                cpu = (run2 - run1) / dt * 100.0
                
            prio = t2.get("uxCurrentPriority", 0)
            state = t2.get("eCurrentState", 0)
            hwm = t2.get("usStackHighWaterMark", 0)
            
            # states: 0: Running, 1: Ready, 2: Blocked, 3: Suspended, 4: Deleted
            state_str = ["Run", "Rdy", "Blk", "Sus", "Del"][state] if state < 5 else str(state)
            
            print(f"{name:<20} | {prio:<4} | {state_str:<5} | {cpu:>5.1f}% | {hwm:>9}")
            
        print("\n--- Loop / Heap / Reset Summary ---")
        heap_free = res2.get("g_heap_free", 0)
        heap_min = res2.get("g_heap_min_free", 0)
        loop_last = res2.get("g_loop_period_cyc_last", 0)
        loop_max = res2.get("g_loop_period_cyc_max", 0)
        loop_min = res2.get("g_loop_period_cyc_min", 0)
        loop_over = res2.get("g_loop_overrun_count", 0)
        csr = res2.get("g_reset_csr", 0)
        
        print(f"Heap Free: {heap_free} bytes (Min ever: {heap_min})")
        print(f"Loop Period Cycles: Last={loop_last}, Min={loop_min}, Max={loop_max}, Overruns={loop_over}")
        
        # Decode CSR
        causes = []
        if csr & (1 << 31): causes.append("LPWR")
        if csr & (1 << 30): causes.append("WWDG")
        if csr & (1 << 29): causes.append("IWDG")
        if csr & (1 << 28): causes.append("SFT")
        if csr & (1 << 27): causes.append("POR/PDR")
        if csr & (1 << 26): causes.append("PIN")
        if csr & (1 << 25): causes.append("BOR")
        
        print(f"Reset Cause (RCC_CSR=0x{csr:08X}): {', '.join(causes) if causes else 'None'}")
        return 0
