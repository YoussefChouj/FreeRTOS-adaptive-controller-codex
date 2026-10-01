import sys

with open("ground_station/comm/boot_default_layout.py", "r") as f:
    content = f.read()

# Remove 4 vars from DASHBOARD_FRAME_A_VARS
content = content.replace('    "s_ekf.x[5]",  # ekf.bias_accel_z   b_a_body[2] (m/s²)\n', '')
content = content.replace('    "s_ekf.x[6]",  # ekf.bias_gyro_x    b_g_body[0] (rad/s)\n', '')
content = content.replace('    "s_ekf.x[7]",  # ekf.bias_gyro_y    b_g_body[1] (rad/s)\n', '')
content = content.replace('    "s_ekf.x[8]",  # ekf.bias_gyro_z    b_g_body[2] (rad/s)\n', '')

# Insert 4 MRAC vars into DASHBOARD_FRAME_A_VARS (at the end before TODO)
insert_mrac_str = """    "mrac_inj.inj_alpha",
    "mrac_inj.learn_gate",
    "mrac_simplex.fade",
    "mrac_simplex.tripped",
    # TODO(firmware):"""
content = content.replace('    # TODO(firmware):', insert_mrac_str)

# Insert the 4 EKF vars into DASHBOARD_PANEL_EXTRA_VARS
insert_extra = """    # Estimator-mode readback flags — command-panel OF-bias section.
    "s_ekf.x[5]",
    "s_ekf.x[6]",
    "s_ekf.x[7]",
    "s_ekf.x[8]",
"""
content = content.replace('    # Estimator-mode readback flags — command-panel OF-bias section.\n', insert_extra)

with open("ground_station/comm/boot_default_layout.py", "w") as f:
    f.write(content)
