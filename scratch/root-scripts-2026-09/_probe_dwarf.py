"""Probe DWARF symbols actually resolve against OBJ/JX_FLY.axf."""
import sys
from ground_station.livewatch.symbols import SymbolResolver

r = SymbolResolver("OBJ/JX_FLY.axf")
wanted = [
    "platform_obs_send_ticks", "platform_obs_queue_depth", "platform_obs_dma_busy",
    "platform_obs_usart3_tx_drops", "platform_obs_cmd_queue_depth",
    "platform_obs_cmd_queue_max", "platform_obs_heap_free_bytes",
    "imu_data.rol", "imu_data.pit", "imu_data.yaw",
    "DroneStatus.ARM_Status", "DroneStatus.FlyMode", "real_voltage",
    "mrac_state.pitch.e", "mrac_state.pitch.u_ad",
    "mrac_state.pitch.Theta[0]", "mrac_state.pitch.Theta[1]",
    "mrac_state.pitch.Theta[2]", "mrac_state.pitch.Theta[3]",
    "mrac_state.pitch.Theta[4]", "mrac_state.pitch.Theta[5]",
    "mrac_state.roll.Theta[5]",
    "mrac_state.yaw.Theta[5]",
    "mrac_state.z_rate.Theta[5]",
    "s_ekf.x[0]", "s_ekf.x[1]", "s_ekf.x[8]",
    "s_ekf.P[0]", "s_ekf.P[8]",
    "TWC.execute", "TWC_arrived", "sbus_lost", "s_authority",
    "g_of_hold_active", "g_estimator_ready",
    "ano_of.earth_x", "ano_of.earth_y", "ano_of.of_quality",
    "Ctrler.gyroxPID.Des", "Ctrler.gyroyPID.Des", "Ctrler.gyrozPID.Des",
    "UA3RxFrameCnt", "UA3TxFrames", "UA3TxDrops",
    "xTickCount",
]
resolved, missing = 0, []
for n in wanted:
    try:
        sym = r.resolve(n)
        resolved += 1
    except Exception as e:
        missing.append((n, type(e).__name__))
print(f"Resolved {resolved}/{len(wanted)}")
print("Missing:")
for n, e in missing:
    print(f"  {n}: {e}")
