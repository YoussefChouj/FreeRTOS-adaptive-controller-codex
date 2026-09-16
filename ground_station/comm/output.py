"""Multi-destination output: CSV, JSON manifest, and optional VOFA+ JustFloat."""
from __future__ import annotations

import csv
import json
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ground_station.livewatch.stream import StreamSchema


class MultiOutput:
    """Route decoded frames to CSV, JSON manifest, and optionally VOFA+."""

    def __init__(
        self,
        outdir: str | Path,
        manifest_name: str,
        schema: "StreamSchema",
        channel_map: dict[int, str],
        vofa_host: str = "127.0.0.1",
        vofa_port: int = 13470,
        vofa_enabled: bool = False,
    ):
        """
        Args:
            outdir: Where to write CSV and JSON.
            manifest_name: For the JSON metadata.
            schema: StreamSchema from the manifest.
            channel_map: channel_index -> variable name (from vofa_channel_map).
            vofa_host: VOFA+ UDP host.
            vofa_port: VOFA+ UDP port.
            vofa_enabled: Whether to forward raw Float32 to VOFA+.
        """
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.manifest_name = manifest_name
        self.schema = schema
        self.channel_map = channel_map
        self.vofa_enabled = vofa_enabled
        self._csv_file = None
        self._csv_writer = None
        self._vofa_sock = None
        self._json_written = False

        # Open CSV with unique filename
        self._csv_path = self._unique_path(self.outdir / "stream.csv")
        self._csv_file = open(self._csv_path, "w", newline="")
        self._csv_writer = None  # written on first write_frame call

        # VOFA+ socket
        if vofa_enabled:
            self._vofa_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self._vofa_sock.connect((vofa_host, vofa_port))
            except OSError:
                self._vofa_sock.close()
                self._vofa_sock = None

    def _unique_path(self, path: Path) -> Path:
        """If path exists, return path with _2, _3, ... suffix."""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        n = 2
        while True:
            candidate = path.parent / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def write_json_manifest(self) -> None:
        """Write manifest metadata JSON. Call once at session start."""
        manifest_path = self._unique_path(
            self.outdir / f"{self.manifest_name}_manifest.json"
        )
        data = {
            "manifest": self.manifest_name,
            "transport": "usart3",
            "created": datetime.now(timezone.utc).isoformat(),
            "channel_map": {str(k): v for k, v in self.channel_map.items()},
            "n_channels": len(self.channel_map),
            "divider": self.schema.divider,
            "slot": self.schema.slot,
            "total_bytes": self.schema.total_bytes,
        }
        with open(manifest_path, "w") as f:
            json.dump(data, f, indent=2)
        self._json_written = True

    def write_frame(
        self,
        timestamp_ms: int,
        values: dict[int, float],
        rle_counts: dict[int, int] | None = None,
    ) -> None:
        """Write one decoded frame to CSV (always) and VOFA+ (if enabled).

        NOTE on the CSV "encoding" field: it reflects the wire format, not the
        decode layer. The decode pipeline does not track per-channel wire precision
        separately from the decoded value. For RLE rows (rle_count > 1), the
        encoding is tagged "rle" to indicate the value repeated without storing it
        each time. All non-RLE rows are tagged "float32" (the v1 wire format).
        Counter and Float16 precision is handled upstream by DecodePipeline.
        """
        if self._csv_writer is None:
            fieldnames = ["timestamp_ms", "var", "value", "encoding", "rle_count"]
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=fieldnames)
            self._csv_writer.writeheader()

        rle_counts = rle_counts or {}
        for ch, val in values.items():
            var = self.channel_map.get(ch, f"ch{ch}")
            rle = rle_counts.get(ch, 1)
            encoding = "rle" if rle > 1 else "float32"
            self._csv_writer.writerow({
                "timestamp_ms": timestamp_ms,
                "var": var,
                "value": val,
                "encoding": encoding,
                "rle_count": rle,
            })

        if self.vofa_enabled and self._vofa_sock is not None:
            n_ch = len(self.channel_map)
            buf = struct.pack(f"<{n_ch}f", *[values.get(i, 0.0) for i in range(n_ch)])
            self._vofa_sock.send(buf)

    def close(self) -> None:
        """Flush and close all outputs."""
        if self._csv_file is not None:
            self._csv_file.flush()
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None
        if self._vofa_sock is not None:
            self._vofa_sock.close()
            self._vofa_sock = None
