"""Symbol image: the Keil file list is read right, armcc-only C is rewritten without touching data, and the built
image names the variables the lab asks for at the addresses the resolver reads."""
from __future__ import annotations

import shutil
import textwrap

import pytest

from ground_station.lab import symbol_image as si
from ground_station.livewatch import symbols


def test_project_sources_reads_keil_file_list(tmp_path):
    user = tmp_path / "USER"
    user.mkdir()
    (user / "p.uvprojx").write_text(textwrap.dedent("""\
        <Project><Targets><Target><TargetOption><TargetArmAds><Cads><VariousControls>
          <Define>USE_STDPERIPH_DRIVER, STM32F40_41xxx</Define>
          <IncludePath>..\\API;..\\TASK</IncludePath>
        </VariousControls></Cads></TargetArmAds></TargetOption><Groups>
          <Group><Files>
            <File><FilePath>..\\API\\a.c</FilePath></File>
            <File><FilePath>..\\API\\a.h</FilePath></File>
            <File><FilePath>..\\TASK\\b.C</FilePath></File>
            <File><FilePath>..\\API\\a.c</FilePath></File>
            <File><FilePath>..\\CORE\\startup.s</FilePath></File>
          </Files></Group>
        </Groups></Target></Targets></Project>
        """))
    sources, includes, defines = si.project_sources(user / "p.uvprojx")
    assert [p.relative_to(tmp_path).as_posix() for p in sources] == ["API/a.c", "TASK/b.C"]
    assert [p.relative_to(tmp_path).as_posix() for p in includes] == ["API", "TASK"]
    assert defines == ["USE_STDPERIPH_DRIVER", "STM32F40_41xxx"]


def test_desugar_empties_asm_and_keeps_data():
    src = textwrap.dedent("""\
        float Acc_X_Real;
        static int counter = 3;
        __asm void WFI_SET(void)
        {
            WFI;
        }
        __asm void MSR_MSP(u32 addr)
        {
            MSR MSP, r0
            BX r14
        }
        void f(void) { counter++; __asm{NOP;}; __asm volatile ("nop"); }
        """)
    out = si.desugar(src)
    assert "float Acc_X_Real;" in out and "static int counter = 3;" in out
    assert "void WFI_SET(void) {}" in out and "void MSR_MSP(u32 addr) {}" in out
    assert "WFI;" not in out and "MSR MSP" not in out and "NOP" not in out
    assert '__asm volatile ("nop");' in out          # GNU asm, which clang takes, is left alone


def test_drop_static_only_on_reported_line():
    text = "extern unsigned char fault_backup[8];\nstatic unsigned char fault_backup[8];\nstatic int keep;\n"
    err = "x.i:2:22: error: static declaration of 'fault_backup' follows non-static declaration"
    assert si._drop_static(text, err) == "extern unsigned char fault_backup[8];\nunsigned char fault_backup[8];\n" \
                                         "static int keep;\n"
    assert si._drop_static(text, "x.i:3:1: error: something else") is None


def test_keep_ccm_tags_only_the_ccm_macro():
    src = textwrap.dedent("""\
        #ifdef __CC_ARM
            #define MRAC_CCM __attribute__((section("MRAC_CCM"), zero_init))
        #else
            #define MRAC_CCM
        #endif
        #define OTHER
        MRAC_State_t mrac_state MRAC_CCM;
        """)
    out = si.keep_ccm(src)
    assert '    #define MRAC_CCM __attribute__((section("MRAC_CCM")))\n' in out
    assert "#define OTHER\n" in out and out.count("\n") == src.count("\n")
    assert si.keep_ccm("int x;\n#define A\n") == "int x;\n#define A\n"


def test_firmware_elf_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv(symbols.FIRMWARE_ELF_ENV, raising=False)
    assert symbols.firmware_elf() == si.ROOT / "OBJ" / "JX_FLY.axf"
    monkeypatch.setenv(symbols.FIRMWARE_ELF_ENV, str(tmp_path / "x.elf"))
    assert symbols.firmware_elf() == tmp_path / "x.elf"


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_built_image_resolves_lab_names(tmp_path):
    b = si.build(tmp_path / "img")
    assert b.compiled and set(b.skipped) <= {"FreeRTOS/portable/RVDS/ARM_CM4F/port.c"}
    assert {"API/bmi088_driver.c", "API/sys.c", "USER/fault_capture.c"} <= set(b.desugared)
    r = symbols.SymbolResolver(b.elf)
    # One name from each desugared file plus streams the preflight checks.
    for name in ("Acc_X_Real", "g_wfb_status", "DroneStatus", "xTickCount", "mrac_state"):
        sym = r.resolve(name)
        assert sym.address and sym.size > 0, name
    # USER/JX_FLY.sct: MRAC_CCM objects in the 64 KB CCM, everything else in the 128 KB SRAM.
    for name, lo, hi in (("mrac_state", 0x10000000, 0x10010000), ("g_wfb_status", 0x10000000, 0x10010000),
                         ("DroneStatus", 0x20000000, 0x20020000)):
        sym = r.resolve(name)
        assert lo <= sym.address and sym.address + sym.size <= hi, (name, hex(sym.address))
    # clang puts float P[R][C] on one array DIE with two subranges: [i] is a row, [i][j] a float.
    p, row, cell = r.resolve("s_ekf_of.P"), r.resolve("s_ekf_of.P[1]"), r.resolve("s_ekf_of.P[1][5]")
    assert row.size * 2 <= p.size and row.address == p.address + row.size
    assert cell.size == 4 and cell.address == row.address + 5 * 4
    assert si.build(tmp_path / "img").cached                  # unchanged inputs reuse the image
