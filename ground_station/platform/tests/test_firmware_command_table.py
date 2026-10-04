"""The firmware's ground-station command table vs the ground-station contract (WP-37).

TASK/send_data.c dispatches commands through one table, k_gs_cmds (GS_CMD_ENTRY(first, last, handler) rows).
COMMAND_TABLE in firmware_contract.py describes the commands to the GS. These tests fail when one side gains or
loses an id the other does not know about, except for the documented exceptions below, and when two firmware
rows claim the same id (the dispatcher takes the first match, so a duplicate would silently shadow a handler).
"""
from __future__ import annotations

import re
from pathlib import Path

from ground_station.platform.firmware_contract import COMMAND_TABLE

REPO = Path(__file__).resolve().parents[3]
SEND_DATA = REPO / "TASK" / "send_data.c"
WFB_GLUE_H = REPO / "API" / "wfb_glue.h"

# Handled by the firmware, not described in COMMAND_TABLE.
FIRMWARE_ONLY = {
    0x19: "SIMPLEX (run-time assurance knobs). PROPOSED: add to COMMAND_TABLE and regenerate the manifest",
    0x1A: "WFB_CMD_PRIM, described in ground_station/platform/wfb_commands.py",
    0x1B: "WFB_CMD_TRAJ, described in ground_station/platform/wfb_commands.py",
    **{i: "MRAC element update, 8-bit index (0x20 + field*4 + axis)" for i in range(0x20, 0x2C)},
}
# Described in COMMAND_TABLE, refused by the firmware (CommandSafetyReject: unknown above 0x1E).
GS_ONLY = {
    0x1F: "CTRL_SELECT: no handler; g_ctrl_select_req / g_ctrl_axis_mask are written over the probe. "
          "PROPOSED: a handler, and lift the 0x1E bound in CommandSafetyReject",
}


def _hex_define(text: str, name: str) -> int:
    m = re.search(r"#define\s+%s\s+\(?\s*(0x[0-9A-Fa-f]+|\d+)[uU]?" % name, text)
    assert m, f"#define {name} not found"
    return int(m.group(1), 0)


def firmware_entries() -> list[tuple[int, int, str]]:
    """(first, last, handler) per GS_CMD_ENTRY row of k_gs_cmds."""
    text = SEND_DATA.read_text(encoding="utf-8", errors="replace")
    wfb = WFB_GLUE_H.read_text(encoding="utf-8", errors="replace")
    base = _hex_define(text, "MRAC_ELEM_CMD_BASE")
    tol = _hex_define(text, "MRAC_ELEM_FIELD_TOL")
    assert re.search(r"#define\s+MRAC_ELEM_CMD_LAST\s+\(MRAC_ELEM_CMD_BASE \+ \(MRAC_ELEM_FIELD_TOL << 2\) \+ 3U\)", text)
    consts = {
        "WFB_CMD_PRIM": _hex_define(wfb, "WFB_CMD_PRIM"),
        "WFB_CMD_TRAJ": _hex_define(wfb, "WFB_CMD_TRAJ"),
        "MRAC_ELEM_CMD_BASE": base,
        "MRAC_ELEM_CMD_LAST": base + (tol << 2) + 3,
    }
    table = text[text.index("k_gs_cmds[] = {"):]
    table = table[: table.index("};")]
    rows = re.findall(r"GS_CMD_ENTRY\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)", table)
    return [(consts.get(a) if a in consts else int(a, 16), consts.get(b) if b in consts else int(b, 16), h)
            for a, b, h in rows]


def firmware_ids() -> set[int]:
    return {i for first, last, _ in firmware_entries() for i in range(first, last + 1)}


def test_table_parses():
    entries = firmware_entries()
    assert len(entries) >= 25
    assert (0x01, 0x01, "Cmd_PidGain") in entries
    assert (0x20, 0x2B, "Cmd_MracElem8") in entries


def test_no_id_is_claimed_twice():
    seen: dict[int, str] = {}
    for first, last, handler in firmware_entries():
        assert first <= last, f"{handler}: range {first:#04x}..{last:#04x} is empty"
        for i in range(first, last + 1):
            assert i not in seen, f"id {i:#04x} claimed by {seen[i]} and {handler}"
            seen[i] = handler


def test_every_firmware_id_is_described():
    undocumented = firmware_ids() - set(COMMAND_TABLE)
    assert undocumented == set(FIRMWARE_ONLY), (
        "firmware ids missing from COMMAND_TABLE: " + ", ".join(f"{i:#04x}" for i in sorted(undocumented)))


def test_every_contract_id_has_a_handler():
    unhandled = set(COMMAND_TABLE) - firmware_ids()
    assert unhandled == set(GS_ONLY), (
        "COMMAND_TABLE ids without a firmware handler: " + ", ".join(f"{i:#04x}" for i in sorted(unhandled)))
