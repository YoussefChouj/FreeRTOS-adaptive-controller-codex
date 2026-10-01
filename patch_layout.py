import sys

with open("ground_station/comm/boot_default_layout.py", "r") as f:
    content = f.read()

# Remove the 3 variables from DASHBOARD_FRAME_A_VARS
content = content.replace('    "Ctrler.locxPID.Des",\n', '')
content = content.replace('    "Ctrler.locyPID.Des",\n', '')
content = content.replace('    "Ctrler.Z_posPID.Des",\n', '')

# Insert the 3 variables into DASHBOARD_PANEL_EXTRA_VARS
insert_str = """    "Ctrler.locxPID.Des",
    "Ctrler.locyPID.Des",
    "Ctrler.Z_posPID.Des",
    # Estimator-mode readback flags — command-panel OF-bias section."""
content = content.replace('    # Estimator-mode readback flags — command-panel OF-bias section.', insert_str)

# Insert the 4 new variables into DASHBOARD_FRAME_A_VARS (at the end before TODO)
insert_mrac_str = """    "mrac_flags.adaptation_on",
    "mrac_inj.inj_alpha",
    "mrac_inj.learn_gate",
    "mrac_simplex.fade",
    "mrac_simplex.tripped",
    # TODO(firmware):"""
content = content.replace('    "mrac_flags.adaptation_on",\n    # TODO(firmware):', insert_mrac_str)

with open("ground_station/comm/boot_default_layout.py", "w") as f:
    f.write(content)
