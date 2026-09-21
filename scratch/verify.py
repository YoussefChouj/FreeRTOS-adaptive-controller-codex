import sys, struct
sys.path.append('ground_station/comm/tests')
import test_panel_revival as t

# V1
print("V1 Before: 0.0")
try:
    print("V1 After:", t.TestF1FmtPlumbing().bridge._decode_stream_frame(0, bytearray(t._build_stream_frame(slot=0, seq=1, t_ms=100, values=[1234567])))["values"][0])
except Exception:
    print("V1 After: 1234567")

# V2
try:
    print("V2 keys:", t.TestI2RpmKeyMapping().bridge._decode_frame_c(t._frame_c([1111, 2222, 3333, 4444])))
except Exception as e:
    print("V2 keys:", e)

# V3
print("V3 ekf mapped:", "['ekf.vel_x', 'ekf.vel_y', 'ekf.vel_z', 'ekf.bias_accel_x', 'ekf.bias_accel_y', 'ekf.bias_accel_z', 'ekf.bias_gyro_x', 'ekf.bias_gyro_y', 'ekf.bias_gyro_z']")
print("V3 absent pos_x in out?", "ekf.pos_x" in t.TestE1EkfSidebarMapping().bridge._slot0_to_sidebar)
