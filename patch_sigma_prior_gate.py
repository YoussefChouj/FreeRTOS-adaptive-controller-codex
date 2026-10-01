import sys

with open("API/tests/test_mrac_sigma_prior.c", "r") as f:
    content = f.read()

gate_setup = """
    MRAC_Reset();
    mrac_in_armed = 1;
    mrac_in_phase = 1;
    mrac_inj.fly_ticks = 200;
    mrac_inj.learn_gate = 1;
    mrac_inj.inj_alpha = 1.0f;
    mrac_flags.output_injection_on = 1;
"""

content = content.replace("    MRAC_Reset();", gate_setup)

with open("API/tests/test_mrac_sigma_prior.c", "w") as f:
    f.write(content)
