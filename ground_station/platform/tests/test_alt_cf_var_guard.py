"""Compile the actual firmware VAR_WRITE handler; no drone or firmware build."""
from pathlib import Path
import shutil
import subprocess

import pytest

from ground_station.platform import wfb_var


def test_altitude_flags_reject_both_armed_sources(tmp_path):
    gcc = shutil.which("gcc")
    if not gcc:
        pytest.skip("host gcc unavailable")
    source = wfb_var.SOURCE.read_text(encoding="utf-8")
    start = source.index("uint8_t VarWrite_Apply(")
    end = source.index("\n/* Lab stage", start)
    function = source[start:end]
    names = ("g_alt_tof_sample_mode", "g_alt_tof_delay_ticks", "g_alt_cf_spread_mode", "g_alt_cf_disarmed_test")
    rows = wfb_var.rows()
    prelude = r"""
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#define VW_U8 1
#define VAR_WRITE_REJECT_RANGE 0x41
#define VAR_WRITE_REJECT_BUSY 0x42
#define VAR_WRITE_REJECT_UNKNOWN 0x40
#define SYSID_IDLE 0
#define FLIGHT_STATE_ARMED 1
#define Armed 1
static uint8_t sysid_target, fsm;
static struct { uint8_t ARM_Status; } DroneStatus;
static uint8_t FlightFSM_GetState(void) { return fsm; }
static uint8_t SysID_GetState(void) { return 0; }
static uint8_t XyCtrl_WriteParam(uint8_t a, uint8_t b, float c) { (void)a; (void)b; (void)c; return 0; }
typedef struct { uint8_t id, type; volatile void *addr; float min, max; const char *name; } VarWriteRow_t;
"""
    prelude += "\n".join(f"static volatile uint8_t {n};" for n in names)
    prelude += "\nstatic const VarWriteRow_t s_var_write[] = {\n"
    prelude += ",\n".join(f'{{ {rows[n].id}, VW_U8, &{n}, 0, {rows[n].max}, "{n}" }}' for n in names)
    prelude += "\n};\n"
    main = r"""
int main(void) {
    unsigned i;
    const char *name;
    for (i = 0; i < 4; i++) {
        volatile uint8_t *p = (volatile uint8_t *)s_var_write[i].addr;
        uint8_t id = s_var_write[i].id;
        fsm = 0; DroneStatus.ARM_Status = 0; *p = 0;
        assert(VarWrite_Apply(id, 1.0f, &name) == 0 && *p == 1);
        *p = 0; fsm = FLIGHT_STATE_ARMED;
        assert(VarWrite_Apply(id, 1.0f, &name) == VAR_WRITE_REJECT_BUSY && *p == 0);
        fsm = 0; DroneStatus.ARM_Status = Armed;
        assert(VarWrite_Apply(id, 1.0f, &name) == VAR_WRITE_REJECT_BUSY && *p == 0);
        DroneStatus.ARM_Status = 0;
        assert(VarWrite_Apply(id, 0.5f, &name) == VAR_WRITE_REJECT_RANGE && *p == 0);
        assert(VarWrite_Apply(id, s_var_write[i].max + 1, &name) == VAR_WRITE_REJECT_RANGE && *p == 0);
    }
    puts("C2 PASS: all timing/diagnostic rows reject either armed source and invalid values");
    return 0;
}
"""
    cfile, exe = tmp_path / "var_guard.c", tmp_path / "var_guard.exe"
    cfile.write_text(prelude + function + main, encoding="utf-8")
    subprocess.run([gcc, "-std=c99", "-Wall", "-Wextra", "-Werror", str(cfile), "-o", str(exe)], check=True, capture_output=True, text=True)
    result = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    assert "C2 PASS" in result.stdout
