import sys

with open("API/mrac.h", "r") as f:
    content = f.read()

old_struct = """typedef struct {
    float inj_alpha;
    uint8_t learn_gate;
    uint16_t fly_ticks;
    uint8_t prev_injection_on;
    uint16_t not_flying_ticks;
    float ramp_p;
    uint8_t prev_armed;
} MRAC_Inj_t;"""

new_struct = """typedef struct {
    float inj_alpha;
    uint8_t learn_gate;
    uint16_t fly_ticks;
    uint8_t prev_injection_on;
    uint16_t not_flying_ticks;
    float ramp_p;
    uint8_t prev_armed;
    uint8_t freeze_shadow;
} MRAC_Inj_t;"""

content = content.replace(old_struct, new_struct)

with open("API/mrac.h", "w") as f:
    f.write(content)
