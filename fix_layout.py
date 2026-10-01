import sys

with open("ground_station/comm/boot_default_layout.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_frame_a = False
in_panel_extra = False

for i, line in enumerate(lines):
    if "DASHBOARD_FRAME_A_VARS: tuple" in line:
        in_frame_a = True
    elif in_frame_a and line.strip() == ")":
        # End of DASHBOARD_FRAME_A_VARS, insert MRAC vars
        new_lines.extend([
            '    "mrac_inj.inj_alpha",\n',
            '    "mrac_inj.learn_gate",\n',
            '    "mrac_simplex.fade",\n',
            '    "mrac_simplex.tripped",\n'
        ])
        in_frame_a = False
    
    if "DASHBOARD_PANEL_EXTRA_VARS: tuple" in line:
        in_panel_extra = True
    elif in_panel_extra and line.strip() == ")":
        # End of DASHBOARD_PANEL_EXTRA_VARS, insert removed EKF vars
        new_lines.extend([
            '    "s_ekf.x[5]",\n',
            '    "s_ekf.x[6]",\n',
            '    "s_ekf.x[7]",\n',
            '    "s_ekf.x[8]",\n'
        ])
        in_panel_extra = False

    # Skip lines we are removing
    if line.strip().startswith('"s_ekf.x[5]"') and in_frame_a: continue
    if line.strip().startswith('"s_ekf.x[6]"') and in_frame_a: continue
    if line.strip().startswith('"s_ekf.x[7]"') and in_frame_a: continue
    if line.strip().startswith('"s_ekf.x[8]"') and in_frame_a: continue

    new_lines.append(line)

with open("ground_station/comm/boot_default_layout.py", "w") as f:
    f.writelines(new_lines)

