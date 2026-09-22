"""Firmware scanner tests over a synthetic C fixture (no ELF required)."""
import sys
from pathlib import Path

import pytest

from ground_station.agent_map import firmware as F

FIXTURE = r'''
#include "ctl.h"

/* a block comment
   spanning lines */
#define NOT_A_SYM 42

float Throttle_out, u_gyrox, u_gyroy, u_gyroz;   // globals on one line
short count = 0;

typedef struct {
    PIDTypeDef gyroxPID;
    PIDTypeDef rollPID;
    float e;                // scalar member -> should be dropped
    unsigned char ARM_Status;
} CtrlerTypeDef;

CtrlerTypeDef Ctrler = { 0, 0 };

static void helper(int x) { int local = x; local += 1; }
void Reset_World_Origin(void) { float des_pitch = 0; des_pitch = 1; }
int Compute(int a) { return a * 2; }
'''


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    p = tmp_path / "fixture.c"
    p.write_text(FIXTURE, encoding="utf-8")
    return p


def _names(recs, kind):
    return {r["name"] for r in recs if r["kind"] == kind}


def test_functions(src):
    recs = F.scan_file(src)
    fns = _names(recs, "function")
    assert {"Reset_World_Origin", "Compute", "helper"} <= fns
    # locals / control flow are never functions
    assert "local" not in fns and "if" not in fns


def test_globals(src):
    recs = F.scan_file(src)
    g = _names(recs, "global")
    assert {"Throttle_out", "u_gyrox", "u_gyroy", "u_gyroz", "count", "Ctrler"} <= g
    assert "local" not in g and "des_pitch" not in g


def test_compound_members_only(src):
    recs = F.scan_file(src)
    members = {r["name"]: r for r in recs if r["kind"] == "member"}
    assert "gyroxPID" in members      # PIDTypeDef member kept
    assert "rollPID" in members
    assert members["gyroxPID"]["line"] >= 1
    assert "e" not in members         # scalar float member dropped
    assert "ARM_Status" not in members


def test_line_numbers_after_comments(src):
    # count is on physical line 9 of the fixture (counting from 1):
    #  1: blank     2: #include  3: blank  4-5: block comment
    #  6: #define   7: blank     8: float  9: short count = 0;
    # Ensure comment/`#define` masking preserves the real line number.
    recs = F.scan_file(src)
    count = next(r for r in recs if r["name"] == "count" and r["kind"] == "global")
    assert count["line"] == 9


def test_global_initializer_not_leaked(src):
    # `Ctrler = { ... }` must not leak member names into the global set
    recs = F.scan_file(src)
    g = _names(recs, "global")
    assert "gyroxPID" not in g
    assert "rollPID" not in g