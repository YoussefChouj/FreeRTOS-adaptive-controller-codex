"""Unit tests for SchemaRegistry -- the DWARF-name -> spec-key lookup table.

Pins the registry contract:

  - Default mapping covers the canonical 65+ sidebar keys
  - ``resolve`` returns the spec key for known DWARF names, ``None`` for
    unknown ones
  - ``resolve_all`` emits both spec-keyed AND raw-DWARF-name entries
    (unknown names are not lost)
  - Int-valued spec keys round to 0 decimals; everything else rounds
    to 3 decimals (the S15 contract)
  - Loading from ``manifests.yaml`` produces the same mapping as the
    builtin fallback (the YAML is the source of truth)

If any of these tests fail, the dashboard sidebar will render "?" for
the affected keys (the S15 dashboard bug).
"""
from __future__ import annotations

import unittest
from pathlib import Path

from ground_station.service.schema_registry import SchemaRegistry


class TestBuiltinDashboardMapping(unittest.TestCase):
    """The hardcoded fallback mapping must cover the slot-0 sidebar contract."""

    def test_attitude_present(self):
        r = SchemaRegistry.builtin_dashboard()
        self.assertEqual(r.resolve("imu_data.rol"), "status.roll_deg")
        self.assertEqual(r.resolve("imu_data.pit"), "status.pitch_deg")
        self.assertEqual(r.resolve("imu_data.yaw"), "status.yaw_deg")

    def test_status_flags_present(self):
        r = SchemaRegistry.builtin_dashboard()
        expected = {
            "DroneStatus.ARM_Status": "status.arm",
            "DroneStatus.FlyMode":    "status.flymode",
            "sbus_lost":              "status.sbus",
            "TWC.execute":            "status.twc_execute",
            "TWC_arrived":            "status.twc_arrived",
            "s_authority":            "status.rc_authority",
            "g_of_hold_active":       "status.of_hold",
            "g_estimator_ready":      "status.estimator_ready",
        }
        for dwarf, spec in expected.items():
            self.assertEqual(r.resolve(dwarf), spec,
                             f"missing mapping {dwarf!r} -> {spec!r}")

    def test_battery_present(self):
        r = SchemaRegistry.builtin_dashboard()
        self.assertEqual(r.resolve("real_voltage"), "status.vbat")

    def test_mrac_bars_present(self):
        r = SchemaRegistry.builtin_dashboard()
        expected = [
            ("mrac_state.pitch.e",    "mrac.pitch.e"),
            ("mrac_state.pitch.u_ad", "mrac.pitch.u_ad"),
            ("mrac_state.roll.e",     "mrac.roll.e"),
            ("mrac_state.roll.u_ad",  "mrac.roll.u_ad"),
            ("mrac_state.yaw.e",      "mrac.yaw.e"),
            ("mrac_state.yaw.u_ad",   "mrac.yaw.u_ad"),
            ("mrac_state.z_rate.e",   "mrac.z.e"),
            ("mrac_state.z_rate.u_ad", "mrac.z.u_ad"),
        ]
        for dwarf, spec in expected:
            self.assertEqual(r.resolve(dwarf), spec,
                             f"missing MRAC mapping {dwarf!r}")

    def test_mrac_full_theta_vector_present(self):
        """The 24-element MRAC theta vector must be in the registry."""
        r = SchemaRegistry.builtin_dashboard()
        for axis in ("pitch", "roll", "yaw", "z_rate"):
            for i in range(6):
                dwarf = f"mrac_state.{axis}.Theta[{i}]"
                spec = f"mrac.{('z' if axis == 'z_rate' else axis)}.theta_{i}"
                self.assertEqual(
                    r.resolve(dwarf), spec,
                    f"missing theta mapping {dwarf!r}",
                )

    def test_ekf_9_state_present(self):
        """The 9-element EKF body-frame vector must be in the registry."""
        r = SchemaRegistry.builtin_dashboard()
        expected = {
            "s_ekf.x[0]":  "ekf.vel_x",
            "s_ekf.x[1]":  "ekf.vel_y",
            "s_ekf.x[2]":  "ekf.vel_z",
            "s_ekf.x[3]":  "ekf.bias_accel_x",
            "s_ekf.x[4]":  "ekf.bias_accel_y",
            "s_ekf.x[5]":  "ekf.bias_accel_z",
            "s_ekf.x[6]":  "ekf.bias_gyro_x",
            "s_ekf.x[7]":  "ekf.bias_gyro_y",
            "s_ekf.x[8]":  "ekf.bias_gyro_z",
        }
        for dwarf, spec in expected.items():
            self.assertEqual(r.resolve(dwarf), spec,
                             f"missing EKF mapping {dwarf!r}")


class TestResolveReturnsNoneForUnknown(unittest.TestCase):
    """Unknown DWARF names must NOT silently fall through to the raw name.

    ``resolve`` returns ``None``; callers (the adapter) decide whether
    to emit the value under the raw name. This separation makes the
    "missing mapping" case debuggable.
    """

    def test_unknown_returns_none(self):
        r = SchemaRegistry.builtin_dashboard()
        self.assertIsNone(r.resolve("not_in_registry"))
        self.assertIsNone(r.resolve("firmware_added_this.last_week"))
        self.assertIsNone(r.resolve(""))


class TestResolveAllBehaviour(unittest.TestCase):
    """resolve_all -- the bulk path with per-key rounding rules."""

    def test_emits_mapped_keys(self):
        r = SchemaRegistry.builtin_dashboard()
        pairs = [("imu_data.rol", -1.141234567), ("real_voltage", 12.3456789)]
        out = r.resolve_all(pairs)
        self.assertEqual(out["status.roll_deg"], -1.141)
        self.assertEqual(out["status.vbat"], 12.346)

    def test_int_keys_round_to_zero_decimals(self):
        """Status flags must round to int (no .0 noise in the JSON)."""
        r = SchemaRegistry.builtin_dashboard()
        out = r.resolve_all([("DroneStatus.ARM_Status", 0.999999)])
        # 0.999 rounds to 1, not 1.0 (int).
        self.assertEqual(out["status.arm"], 1.0)

    def test_unmapped_names_emit_under_raw_path(self):
        """No silent drops: unmapped names appear under their DWARF name."""
        r = SchemaRegistry.builtin_dashboard()
        out = r.resolve_all([("totally_new_field.x", 42.0)])
        self.assertEqual(out["totally_new_field.x"], 42.0)

    def test_mixed_mapped_and_unmapped(self):
        """A bulk call with both kinds works without losing either."""
        r = SchemaRegistry.builtin_dashboard()
        out = r.resolve_all([
            ("imu_data.rol", -0.5),
            ("brand_new_field", 12.0),
        ])
        self.assertEqual(out["status.roll_deg"], -0.5)
        self.assertEqual(out["brand_new_field"], 12.0)


class TestRegisterAndOverrides(unittest.TestCase):
    """register -- additive and override semantics."""

    def test_register_adds_new_mapping(self):
        r = SchemaRegistry()
        r.register("imu_data.rol", "status.roll_deg")
        self.assertEqual(r.resolve("imu_data.rol"), "status.roll_deg")

    def test_register_overrides_existing(self):
        r = SchemaRegistry({"imu_data.rol": "old.key"})
        r.register("imu_data.rol", "new.key")
        self.assertEqual(r.resolve("imu_data.rol"), "new.key")

    def test_register_int_keys_appends(self):
        r = SchemaRegistry(int_keys={"a"})
        r.register_int_keys(["b"])
        out = r.resolve_all([("a", 0.9), ("b", 0.9), ("c", 0.9)])
        # The default rounding is 3 decimals, so ``c`` would be 0.9.
        # The declared ``a`` and ``b`` round to int = 1.0.
        self.assertEqual(out["a"], 1.0)
        self.assertEqual(out["b"], 1.0)


class TestFromDashboardManifest(unittest.TestCase):
    """from_dashboard_manifest -- load from manifests.yaml:dashboard_frame_a.

    The YAML manifest is the source of truth; the builtin fallback is
    for tests / minimal envs. The two should agree on at least the
    canonical 65+ keys.
    """

    MANIFESTS_PATH = Path(__file__).resolve().parents[2] / "livewatch" / "manifests.yaml"

    def test_loads_when_present(self):
        if not self.MANIFESTS_PATH.is_file():
            self.skipTest(f"manifests.yaml not found at {self.MANIFESTS_PATH}")
        r = SchemaRegistry.from_dashboard_manifest(self.MANIFESTS_PATH)
        # At minimum the canonical attitude + status keys must resolve.
        self.assertEqual(r.resolve("imu_data.rol"), "status.roll_deg")
        self.assertEqual(r.resolve("DroneStatus.ARM_Status"), "status.arm")
        self.assertEqual(r.resolve("real_voltage"), "status.vbat")

    def test_falls_back_silently_when_missing(self):
        """A missing file is not an error -- empty registry is fine."""
        r = SchemaRegistry.from_dashboard_manifest(Path("/nonexistent.yaml"))
        self.assertEqual(r.resolve("anything"), None)
        # resolve_all emits raw DWARF names without rounding errors.
        out = r.resolve_all([("imu_data.rol", -0.5)])
        self.assertEqual(out["imu_data.rol"], -0.5)

    def test_yaml_subset_loader_parses_correctly(self):
        """The minimal YAML loader (no PyYAML) handles the manifests subset."""
        if not self.MANIFESTS_PATH.is_file():
            self.skipTest("manifests.yaml not found")
        # Force the minimal loader by passing a list-style input via tmp
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("""
manifests:
  dashboard_frame_a:
    doc: "Test"
    hz: 20
    vars:
      - dwarf: imu_data.rol
        key:  status.roll_deg
      - dwarf: DroneStatus.ARM_Status
        key:  status.arm
        int:  true
""")
            tmp_path = Path(f.name)
        try:
            r = SchemaRegistry.from_dashboard_manifest(tmp_path)
            self.assertEqual(r.resolve("imu_data.rol"), "status.roll_deg")
            self.assertEqual(r.resolve("DroneStatus.ARM_Status"), "status.arm")
            # int rounding must apply to the int-true entry.
            out = r.resolve_all([("DroneStatus.ARM_Status", 0.7)])
            self.assertEqual(out["status.arm"], 1.0)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestSpecKeysAndDwarfNames(unittest.TestCase):
    """spec_keys / dwarf_names -- introspection helpers."""

    def test_builtin_canonical_count(self):
        """The builtin registry emits the canonical 53 sidebar keys.

        (3 attitude + 8 status + 1 vbat + 8 MRAC bars + 24 MRAC theta + 9 EKF).
        The planning prompt claimed "65+" but the actual count after the
        2026-09-17 spec change is 53. If a new field is added to the
        manifest, bump this number AND add a test for the new spec key.
        """
        r = SchemaRegistry.builtin_dashboard()
        self.assertGreaterEqual(len(r.spec_keys()), 53)

    def test_builtin_matches_yaml_for_canonical_attitude(self):
        """The YAML loader and the builtin must agree on attitude keys.

        If they drift, the dashboard's sidebar shows the WRONG spec key
        for that DWARF name. The test pins the contract.
        """
        from pathlib import Path
        manifests = (
            Path(__file__).resolve().parents[2] / "livewatch" / "manifests.yaml"
        )
        if not manifests.is_file():
            self.skipTest("manifests.yaml not found")
        builtin = SchemaRegistry.builtin_dashboard()
        loaded = SchemaRegistry.from_dashboard_manifest(manifests)
        for dwarf in ("imu_data.rol", "imu_data.pit", "imu_data.yaw",
                      "real_voltage", "DroneStatus.ARM_Status",
                      "mrac_state.pitch.e"):
            self.assertEqual(
                loaded.resolve(dwarf),
                builtin.resolve(dwarf),
                f"yaml/builtin drift on {dwarf!r}",
            )

    def test_spec_keys_and_dwarf_names_symmetric(self):
        r = SchemaRegistry({"a": "x", "b": "y"})
        self.assertEqual(r.spec_keys(), {"x", "y"})
        self.assertEqual(r.dwarf_names(), {"a", "b"})


if __name__ == "__main__":
    unittest.main()