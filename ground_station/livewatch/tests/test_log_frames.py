"""The default telemetry frame lives in a Markdown table, not in argv.

Two things are worth pinning here. First, that the table parser ignores
everything that makes the file readable as documentation -- prose, a second
table, header rows -- and still finds the frame. Second, that the frame we
actually ship fits inside UART5's share of the link, because a default that the
firmware rejects with 0x7F is worse than no default at all.
"""
import pytest

from ground_station.livewatch.stream import (
    BUDGET_PCT, MAX_SLOTS, SEND_TASK_MEASURED_HZ, TRANSPORT_UART5, stream_bps,
)
from ground_station.livewatch.stream_log import (
    DEFAULT_FRAMES, load_frames, parse_frames_markdown,
)
from ground_station.livewatch.transport import LiveTransportError

TABLE = """
# Some heading

Prose that mentions | a pipe | but is not a table row.

| Slot | Rate (Hz) | Variables |
| ---- | --------- | --------- |
| 0 | 40 | mrac_state.roll.Theta:6 |
| 1 | 10 | imu_data.rol:3, s_ekf.x:9 |
"""


def test_parses_slots_rates_and_symbols():
    assert parse_frames_markdown(TABLE) == [
        (40.0, ["mrac_state.roll.Theta:6"]),
        (10.0, ["imu_data.rol:3", "s_ekf.x:9"]),
    ]


def test_ignores_prose_headers_and_separator_rows():
    """Only rows whose first cell is an integer count as frame rows."""
    assert len(parse_frames_markdown(TABLE)) == 2


def test_a_second_table_does_not_leak_into_the_frame():
    """The budget table in the real file must not be read as slots."""
    text = TABLE + """
| Transport | Share | Budget |
| --- | --- | --- |
| UART5 | 20 % | 2304 B/s |
"""
    assert len(parse_frames_markdown(text)) == 2


def test_rows_are_ordered_by_slot_not_by_file_order():
    text = """
| Slot | Rate | Variables |
| 1 | 10 | b |
| 0 | 40 | a |
"""
    assert [syms for _, syms in parse_frames_markdown(text)] == [["a"], ["b"]]


def test_non_contiguous_slots_are_refused():
    """Slot N maps to firmware slot N; a gap would silently renumber it."""
    text = """
| Slot | Rate | Variables |
| 0 | 40 | a |
| 2 | 10 | b |
"""
    with pytest.raises(LiveTransportError, match="contiguous"):
        parse_frames_markdown(text)


def test_a_file_with_no_table_is_an_error_not_an_empty_frame():
    with pytest.raises(LiveTransportError, match="no frame rows"):
        parse_frames_markdown("# Nothing here\n\njust prose\n")


def test_non_positive_rate_is_refused():
    with pytest.raises(LiveTransportError, match="non-positive"):
        parse_frames_markdown("| Slot | Rate | Variables |\n| 0 | 0 | a |\n")


def test_empty_variable_list_is_refused():
    with pytest.raises(LiveTransportError, match="no variables"):
        parse_frames_markdown("| Slot | Rate | Variables |\n| 0 | 40 |  |\n")


# ---- the shipped default -------------------------------------------------

def test_shipped_default_frame_parses():
    frames = load_frames(DEFAULT_FRAMES)
    assert 1 <= len(frames) <= MAX_SLOTS


def _default_frame_bps():
    """Cost of the shipped frame, using the firmware's own arithmetic.

    4 B per value is the widest scalar the resolver accepts, so this is an upper
    bound on the payload and needs no ELF.
    """
    total = 0
    for rate, specs in load_frames(DEFAULT_FRAMES):
        payload = sum(int(s.partition(":")[2] or 1) * 4 for s in specs)
        divider = max(1, min(255, round(SEND_TASK_MEASURED_HZ / rate)))
        total += stream_bps(payload, divider)
    return total


def test_shipped_default_frame_fits_the_uart5_budget():
    """Below this, the firmware answers the subscription with 0x7F."""
    allowed = (115200 // 10) * BUDGET_PCT[TRANSPORT_UART5] // 100
    total = _default_frame_bps()
    assert total <= allowed, (
        "default frame costs %d B/s but UART5 allows %d B/s" % (total, allowed))


def test_shipped_default_frame_stays_under_the_MEASURED_drop_free_ceiling():
    """Passing the firmware's guard is not the same as arriving intact.

    The guard is baud arithmetic; it does not know UART5 is already ~100%
    saturated by the existing telemetry. Measured on the drone 2026-07-29:
    2055 B/s was accepted but dropped 14% of frames on every slot, while
    1580 B/s ran clean. Keep the shipped default on the clean side of that --
    a default that loses frames teaches everyone who copies it to lose frames.
    """
    assert _default_frame_bps() <= 1600
