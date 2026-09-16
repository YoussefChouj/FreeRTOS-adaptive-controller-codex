"""Tests for the neutral telemetry protocol schema registry."""
from __future__ import annotations

from scripts.validate_protocol_schema import frame_b_lengths, load_schema, validate


def test_protocol_schema_matches_host_contracts():
    assert validate() == []


def test_schema_declares_all_parser_owned_frames():
    frames = load_schema()["frames"]
    for frame_type in ("1", "2", "3", "4", "5", "6"):
        assert frame_type in frames


def test_frame_b_formula_matches_basis_six_parser_size():
    # SerialBridge accepts N=6 payloads of 298 B and 302 B.
    assert frame_b_lengths(6) == {298, 302}


def test_schema_pins_crc_family_for_stream_data():
    frames = load_schema()["frames"]
    assert frames["6"]["crc"] == "crc16_ccitt_xmodem"
    for frame_type in ("9", "10", "11", "12"):
        assert frames[frame_type]["crc"] == "crc16_ccitt_xmodem"
