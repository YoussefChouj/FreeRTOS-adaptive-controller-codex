"""Tests for MultiOutput: CSV, JSON manifest, VOFA+, collision handling."""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from ground_station.comm.output import MultiOutput
from ground_station.livewatch.stream import StreamRange, StreamSchema


def _make_schema(total_bytes: int = 8, slot: int = 0) -> StreamSchema:
    return StreamSchema(
        divider=1,
        transport=1,
        total_bytes=total_bytes,
        ranges=(StreamRange(address=0x20000000, size=4, count=2),),
        slot=slot,
    )


class TestCSV(unittest.TestCase):
    def test_header_written_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "roll.Theta[0]", 1: "roll.Theta[1]"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)

            out.write_frame(0, {0: 1.0, 1: 2.0}, {0: 1, 1: 1})
            out.write_frame(5, {0: 1.1, 1: 2.1}, {0: 1, 1: 1})
            out.close()

            with open(out._csv_path) as f:
                lines = f.readlines()
            self.assertEqual(lines[0].strip(), "timestamp_ms,var,value,encoding,rle_count")
            self.assertEqual(lines[1].strip(), "0,roll.Theta[0],1.0,float32,1")
            self.assertEqual(lines[2].strip(), "0,roll.Theta[1],2.0,float32,1")
            self.assertEqual(lines[3].strip(), "5,roll.Theta[0],1.1,float32,1")
            self.assertEqual(lines[4].strip(), "5,roll.Theta[1],2.1,float32,1")

    def test_one_row_per_variable_per_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x", 1: "y", 2: "z"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)

            out.write_frame(0, {0: 1.0, 1: 2.0, 2: 3.0}, {0: 1, 1: 1, 2: 1})
            out.close()

            with open(out._csv_path) as f:
                lines = f.readlines()
            data_lines = [l for l in lines if not l.startswith("timestamp")]
            self.assertEqual(len(data_lines), 3)

    def test_delta_encoding_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "ch0"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)

            out.write_frame(0, {0: 1.0}, {0: 1})
            out.write_frame(5, {0: 1.0}, {0: 2})  # RLE > 1
            out.close()

            with open(out._csv_path) as f:
                lines = f.readlines()
            self.assertIn("rle", lines[2].strip())  # rle_count=2 -> "rle" tag


class TestJSON(unittest.TestCase):
    def test_manifest_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "a", 1: "b"}
            out = MultiOutput(tmpdir, "mrac-weights", schema, channel_map)

            out.write_json_manifest()
            out.close()

            manifest_files = list(Path(tmpdir).glob("*_manifest.json"))
            self.assertEqual(len(manifest_files), 1)

            with open(manifest_files[0]) as f:
                data = json.load(f)
            self.assertEqual(data["manifest"], "mrac-weights")
            self.assertEqual(data["transport"], "usart3")
            self.assertIn("created", data)
            self.assertEqual(data["channel_map"]["0"], "a")
            self.assertEqual(data["channel_map"]["1"], "b")
            self.assertEqual(data["n_channels"], 2)

    def test_manifest_not_overwritten_on_multiple_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)

            out.write_json_manifest()
            out.write_json_manifest()
            out.close()

            manifest_files = list(Path(tmpdir).glob("*_manifest.json"))
            self.assertEqual(len(manifest_files), 1)


class TestUniqueFilename(unittest.TestCase):
    def test_collision_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)
            out.write_frame(0, {0: 1.0}, {0: 1})
            out.close()

            # Create the same path to trigger collision
            with open(out._csv_path, "w") as f:
                f.write("dummy")

            out2 = MultiOutput(tmpdir, "test", schema, channel_map)
            self.assertNotEqual(out2._csv_path.name, "stream.csv")
            self.assertTrue(out2._csv_path.name.startswith("stream_"))
            out2.close()


class TestMultiOutputClose(unittest.TestCase):
    def test_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)
            out.write_frame(0, {0: 1.0}, {0: 1})
            out.close()
            out.close()  # idempotent

    def test_csv_file_exists_after_close(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x"}
            out = MultiOutput(tmpdir, "test", schema, channel_map)
            out.write_frame(0, {0: 1.0}, {0: 1})
            out.close()
            self.assertTrue(out._csv_path.exists())


class TestVOFAIntegration(unittest.TestCase):
    def test_vofa_socket_opened_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x", 1: "y"}
            # Use a bogus port — connect will fail, but socket is created
            out = MultiOutput(
                tmpdir, "test", schema, channel_map,
                vofa_host="127.0.0.1", vofa_port=13470, vofa_enabled=True,
            )
            self.assertIsNotNone(out._vofa_sock)
            out.close()

    def test_vofa_disabled_means_no_socket(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schema = _make_schema()
            channel_map = {0: "x"}
            out = MultiOutput(
                tmpdir, "test", schema, channel_map,
                vofa_enabled=False,
            )
            self.assertIsNone(out._vofa_sock)
            out.close()


if __name__ == "__main__":
    unittest.main()
