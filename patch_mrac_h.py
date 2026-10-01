import sys

with open("API/mrac.h", "r") as f:
    content = f.read()

injection_code = """
// ------------------------------------------------------------------------------
// Injection Ramp & Ground Safety
// ------------------------------------------------------------------------------
#define MRAC_INJ_T_UP      2.5f   /* WHY: >5x the measured 0.45 s unwind, matches 2-3 s settling */
#define MRAC_INJ_T_DN      0.5f   /* WHY: fast enough to remove injection promptly on disable, slow enough to avoid a sharp kick */
#define MRAC_LEARN_HOLD_S  1.0f   /* WHY: skip takeoff ground effect after phase turns FLYING (h > 0.2 m) */
#define MRAC_FLY_HYST_TICKS 100   /* WHY: 0.5s hysteresis so a short phase flicker does not reset learning */

typedef struct {
    float inj_alpha;
    uint8_t learn_gate;
    uint16_t fly_ticks;
    uint8_t prev_injection_on;
    uint16_t not_flying_ticks;
} MRAC_Inj_t;

extern MRAC_Inj_t mrac_inj;
extern volatile uint8_t mrac_in_armed;
extern volatile uint8_t mrac_in_phase;

void MRAC_ResetWeights(void);
void MRAC_GateStep(void);

"""

content = content.replace("} MRAC_FeatureFlags_t;\n\n\n// ------------------------------------------------------------------------------\n// 6. Simplex fallback (mode-based freeze + fade of u_ad injection)\n// ------------------------------------------------------------------------------", "} MRAC_FeatureFlags_t;\n\n" + injection_code + "// ------------------------------------------------------------------------------\n// 6. Simplex fallback (mode-based freeze + fade of u_ad injection)\n// ------------------------------------------------------------------------------")

with open("API/mrac.h", "w") as f:
    f.write(content)
