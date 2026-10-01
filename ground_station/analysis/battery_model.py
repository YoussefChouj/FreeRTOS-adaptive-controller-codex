"""Battery and SoC model for Workflow B autonomous flight gating.

Maps resting battery voltages to SoC using per-pack or shared LiPo curves,
tracks observed per-pack discharge drops across flights, gates pre-flight
readiness, and evaluates in-flight voltage sag against safety thresholds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class PackError(ValueError):
    """Raised for any invalid battery pack configuration, drop, or voltage query."""


@dataclass
class _Pack:
    id: str
    label: str
    cells: int
    capacity_mah: float
    ocv_table: list[list[float]]
    recorded_drops: list[float] = field(default_factory=list)


class PackRegistry:
    """Registry of known battery packs and gating logic."""

    def __init__(
        self,
        gate_soc_pct: float,
        min_rest_s: float,
        sag_v_per_cell: float,
        default_flight_drop_pct: float,
        ocv_table: list[list[float]],
        packs: dict[str, _Pack],
    ) -> None:
        self.gate_soc_pct: float = float(gate_soc_pct)
        self.min_rest_s: float = float(min_rest_s)
        self.sag_v_per_cell: float = float(sag_v_per_cell)
        self.default_flight_drop_pct: float = float(default_flight_drop_pct)
        self.ocv_table: list[list[float]] = [[float(v), float(soc)] for v, soc in ocv_table]
        self._packs: dict[str, _Pack] = packs

    @classmethod
    def load(cls, path: str | Path | None = None) -> PackRegistry:
        """Load and validate a packs.yaml configuration file.

        None means the packs.yaml next to the module. Validation collects every problem
        and raises one PackError whose message lists them all, one per line.
        """
        if path is None:
            resolved_path = Path(__file__).resolve().parent / "packs.yaml"
        else:
            resolved_path = Path(path)

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                raw: Any = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            raise PackError(f"unreadable file or invalid YAML: {e}") from e

        if not isinstance(raw, dict):
            raise PackError("unreadable file or invalid YAML: root must be a YAML mapping")

        problems: list[str] = []

        # 1. Missing or non-numeric top-level keys
        top_numeric_keys = [
            "gate_soc_pct",
            "min_rest_s",
            "sag_v_per_cell",
            "default_flight_drop_pct",
        ]
        for key in top_numeric_keys:
            if key not in raw:
                problems.append(f"missing or non-numeric top-level key: {key} (missing)")
            else:
                val = raw[key]
                if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
                    problems.append(f"missing or non-numeric top-level key: {key} (non-numeric)")

        if "gate_soc_pct" in raw and isinstance(raw["gate_soc_pct"], (int, float)) and not isinstance(raw["gate_soc_pct"], bool):
            gate_val = float(raw["gate_soc_pct"])
            if not (0.0 <= gate_val <= 100.0):
                problems.append(f"gate_soc_pct outside 0..100: {gate_val}")

        # Shared ocv_table check
        shared_table: list[list[float]] = []
        if "ocv_table" not in raw:
            problems.append("missing or non-numeric top-level key: ocv_table (missing)")
        else:
            cls._validate_table(raw["ocv_table"], "shared ocv_table", problems)
            if isinstance(raw["ocv_table"], list):
                try:
                    shared_table = [
                        [float(r[0]), float(r[1])]
                        for r in raw["ocv_table"]
                        if isinstance(r, (list, tuple)) and len(r) == 2
                    ]
                except (ValueError, TypeError):
                    pass

        # Packs list check
        packs_dict: dict[str, _Pack] = {}
        if "packs" not in raw:
            problems.append("missing or non-numeric top-level key: packs (missing)")
        elif not isinstance(raw["packs"], list):
            problems.append("missing or non-numeric top-level key: packs (must be a list)")
        elif len(raw["packs"]) == 0:
            problems.append("empty pack list")
        else:
            seen_ids: set[str] = set()
            for idx, pack_entry in enumerate(raw["packs"]):
                if not isinstance(pack_entry, dict):
                    problems.append(f"pack #{idx} must be a mapping")
                    continue

                pid = pack_entry.get("id")
                if not pid or not isinstance(pid, str):
                    problems.append(f"pack #{idx} missing or invalid id")
                    pid_str = f"#{idx}"
                else:
                    pid_str = pid
                    if pid in seen_ids:
                        problems.append(f"duplicate pack id: '{pid}'")
                    seen_ids.add(pid)

                cells = pack_entry.get("cells")
                cells_bad = False
                if cells is None or not isinstance(cells, int) or isinstance(cells, bool) or cells < 1:
                    cells_bad = True

                cap = pack_entry.get("capacity_mah")
                cap_bad = False
                if cap is None or not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0 or not math.isfinite(cap):
                    cap_bad = True

                if cells_bad or cap_bad:
                    problems.append(
                        f"pack with cells < 1 or capacity_mah <= 0 in '{pid_str}' (cells={cells}, capacity_mah={cap})"
                    )

                pack_table = shared_table
                if "ocv_table" in pack_entry:
                    cls._validate_table(pack_entry["ocv_table"], f"pack '{pid_str}' ocv_table", problems)
                    if isinstance(pack_entry["ocv_table"], list):
                        try:
                            pack_table = [
                                [float(r[0]), float(r[1])]
                                for r in pack_entry["ocv_table"]
                                if isinstance(r, (list, tuple)) and len(r) == 2
                            ]
                        except (ValueError, TypeError):
                            pass

                if not cells_bad and not cap_bad and pid and pid not in packs_dict:
                    packs_dict[pid] = _Pack(
                        id=pid,
                        label=str(pack_entry.get("label", pid)),
                        cells=int(cells),
                        capacity_mah=float(cap),
                        ocv_table=pack_table,
                    )

        if problems:
            raise PackError("\n".join(problems))

        return cls(
            gate_soc_pct=float(raw["gate_soc_pct"]),
            min_rest_s=float(raw["min_rest_s"]),
            sag_v_per_cell=float(raw["sag_v_per_cell"]),
            default_flight_drop_pct=float(raw["default_flight_drop_pct"]),
            ocv_table=shared_table,
            packs=packs_dict,
        )

    @classmethod
    def _validate_table(cls, table: Any, table_name: str, problems: list[str]) -> None:
        """Validate an OCV table structure and monotonic values."""
        if not isinstance(table, list) or len(table) < 2:
            problems.append(f"a table with fewer than 2 rows in {table_name}")
            return

        for idx, row in enumerate(table):
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                problems.append(f"invalid row format at row {idx} in {table_name}")
                return
            v, soc = row[0], row[1]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                problems.append(f"non-numeric voltage at row {idx} in {table_name}")
                return
            if not isinstance(soc, (int, float)) or isinstance(soc, bool) or not math.isfinite(soc):
                problems.append(f"non-numeric soc at row {idx} in {table_name}")
                return

        # Check strictly descending voltages
        v_descending = True
        for i in range(len(table) - 1):
            if float(table[i][0]) <= float(table[i + 1][0]):
                v_descending = False
                break
        if not v_descending:
            problems.append(f"table voltages not strictly descending in {table_name}")

        # Check SoC values: non-increasing and in 0..100
        soc_in_range = True
        for row in table:
            soc_val = float(row[1])
            if not (0.0 <= soc_val <= 100.0):
                soc_in_range = False
                break

        soc_non_increasing = True
        for i in range(len(table) - 1):
            if float(table[i][1]) < float(table[i + 1][1]):
                soc_non_increasing = False
                break

        if not soc_in_range or not soc_non_increasing:
            problems.append(f"table soc values not non-increasing or outside 0..100 in {table_name}")

    def pack_ids(self) -> list[str]:
        """Return the list of known pack IDs in file order."""
        return list(self._packs.keys())

    def predict_soc(self, pack_id: str, resting_v: float) -> float:
        """Predict battery SoC percentage from resting voltage.

        Pack voltage / cells -> volts per cell -> linear interpolation in that pack's
        table, clamped to 0..100. Unknown pack_id raises PackError. Non-finite resting_v
        raises PackError.
        """
        if pack_id not in self._packs:
            raise PackError(f"unknown pack_id: '{pack_id}'")

        if not isinstance(resting_v, (int, float)) or isinstance(resting_v, bool) or not math.isfinite(resting_v):
            raise PackError(f"non-finite resting_v: {resting_v}")

        pack = self._packs[pack_id]
        v_cell = float(resting_v) / pack.cells
        table = pack.ocv_table

        # Boundary checks
        if v_cell >= table[0][0]:
            return float(min(100.0, max(0.0, table[0][1])))
        if v_cell <= table[-1][0]:
            return float(min(100.0, max(0.0, table[-1][1])))

        # Linear interpolation in descending voltage table
        for i in range(len(table) - 1):
            v_high, soc_high = table[i][0], table[i][1]
            v_low, soc_low = table[i + 1][0], table[i + 1][1]
            if v_low <= v_cell <= v_high:
                if v_high == v_low:
                    interp_soc = soc_high
                else:
                    frac = (v_cell - v_low) / (v_high - v_low)
                    interp_soc = soc_low + frac * (soc_high - soc_low)
                return float(min(100.0, max(0.0, interp_soc)))

        return float(min(100.0, max(0.0, table[-1][1])))

    def record_flight(self, pack_id: str, soc_before: float, soc_after: float) -> None:
        """Record an observed flight SoC drop for a pack.

        Stores one observed SoC drop (soc_before - soc_after) for the pack, in memory.
        A drop that is non-finite or <= 0 raises PackError and stores nothing.
        """
        if pack_id not in self._packs:
            raise PackError(f"unknown pack_id: '{pack_id}'")

        if (
            not isinstance(soc_before, (int, float))
            or isinstance(soc_before, bool)
            or not isinstance(soc_after, (int, float))
            or isinstance(soc_after, bool)
        ):
            raise PackError(f"non-numeric soc values: before={soc_before}, after={soc_after}")

        drop = float(soc_before) - float(soc_after)
        if not math.isfinite(drop) or drop <= 0.0:
            raise PackError(f"observed SoC drop must be finite and > 0, got {drop}")

        self._packs[pack_id].recorded_drops.append(drop)

    def expected_drop(self, pack_id: str) -> float:
        """Return the expected flight SoC drop percentage for a pack.

        Mean of the recorded drops for the pack; default_flight_drop_pct when none
        are recorded.
        """
        if pack_id not in self._packs:
            raise PackError(f"unknown pack_id: '{pack_id}'")

        pack = self._packs[pack_id]
        if not pack.recorded_drops:
            return float(self.default_flight_drop_pct)

        return float(sum(pack.recorded_drops) / len(pack.recorded_drops))

    def next_flight_allowed(self, pack_id: str, resting_v: float, cooldown_s: float) -> tuple[bool, str]:
        """Check whether the next flight is allowed for the given pack.

        Allowed when cooldown_s >= min_rest_s AND predict_soc(...) - expected_drop(...) >= gate_soc_pct
        (exactly at the gate is allowed). The reason string is "ok" when allowed; otherwise it starts
        with "REST:" or "SOC:" and states the measured and the required value. When both fail, report REST.
        Unknown pack or non-finite input returns (False, "INPUT: ...") instead of raising.
        """
        if pack_id not in self._packs:
            return (False, f"INPUT: unknown pack '{pack_id}'")

        if not isinstance(resting_v, (int, float)) or isinstance(resting_v, bool) or not math.isfinite(resting_v):
            return (False, f"INPUT: non-finite resting_v: {resting_v}")

        if not isinstance(cooldown_s, (int, float)) or isinstance(cooldown_s, bool) or not math.isfinite(cooldown_s):
            return (False, f"INPUT: non-finite cooldown_s: {cooldown_s}")

        cooldown_val = float(cooldown_s)
        rest_ok = (cooldown_val >= self.min_rest_s - 1e-9)

        soc = self.predict_soc(pack_id, resting_v)
        drop = self.expected_drop(pack_id)
        post_soc = soc - drop
        soc_ok = (post_soc >= self.gate_soc_pct - 1e-9)

        if rest_ok and soc_ok:
            return (True, "ok")

        if not rest_ok:
            return (False, f"REST: cooldown {cooldown_val:.1f}s < required {self.min_rest_s:.1f}s")

        return (
            False,
            f"SOC: predicted post-flight SoC {post_soc:.1f}% < required {self.gate_soc_pct:.1f}% "
            f"(measured resting SoC {soc:.1f}%, expected drop {drop:.1f}%)",
        )

    def sag_critical(self, pack_id: str, loaded_v_filtered: float) -> bool:
        """Check if loaded filtered voltage indicates critical sag.

        True when loaded_v_filtered / cells < sag_v_per_cell. True for a non-finite
        voltage and for an unknown pack (fail safe).
        """
        if not isinstance(loaded_v_filtered, (int, float)) or isinstance(loaded_v_filtered, bool) or not math.isfinite(loaded_v_filtered):
            return True

        if pack_id not in self._packs:
            return True

        pack = self._packs[pack_id]
        v_cell = float(loaded_v_filtered) / pack.cells
        return bool(v_cell < self.sag_v_per_cell)
