"""SchemaRegistry -- the single source of truth for DWARF-name -> spec-key mapping.

Replaces the 90-line hardcoded dict that lived in
``ground_station/comm/wifi_bridge.py::_slot0_to_sidebar``. That dict
silently dropped 19 of 22 sidebar keys because the dict was hand-written
and DWARF names drifted across firmware builds. The replacement is
**schema-driven**: a registry loaded from ``manifests.yaml:dashboard_frame_a``
plus a small list of integer-rounding rules for status fields.

Why a class (not just a dict)?

* The dashboard cares about three orthogonal concerns:

    1. ``resolve(dwarf_name)`` -- is this DWARF name known?
    2. ``resolve_all(pairs)`` -- bulk-convert a (name, value) iterable
       into the spec-keyed dict the dashboard consumes.
    3. Per-key formatting rules (status fields round to int, attitude
       floats round to 3 decimal places, etc.).

  Three concerns -> three methods. A bare dict would force callers to
  rebuild (2) and (3) at every call site.

* Forward compatibility. The boot-default and dashboard frames are different
  subsets of the firmware symbol table. A registry lets us add a third
  frame (e.g. ``high_rate_frame_b``) by composing two registries or
  registering a name override, without touching call sites.

* Test seam. The ``TelemetryAdapter`` tests can inject a fixture
  registry instead of monkey-patching the static dict. The deletion
  test (see ``codebase-design/SKILL.md``) passes: deleting the registry
  collapses both ``wifi_bridge`` and ``service`` back to "any dict works",
  which means the mapping is *not* a pass-through.

Public surface (kept deliberately small -- deep module):

    SchemaRegistry()                  empty registry
    SchemaRegistry.from_dashboard_manifest(manifests_path)
                                       load from manifests.yaml
    .register(dwarf, spec)             add / override one mapping
    .register_int_keys(keys)          declare which spec keys round to int
    .resolve(dwarf) -> spec | None     single lookup
    .resolve_all(pairs) -> dict        bulk lookup with rounding
    .spec_keys() -> set[str]           the set of spec keys we ever emit

If a DWARF name is not in the registry, ``resolve_all`` emits the value
under its raw DWARF name -- preserving the S15 behaviour where plugins
could still read unmapped variables. The dashboard sees both forms
(spec-keyed when known, raw DWARF name otherwise).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping


class SchemaRegistry:
    """DWARF-name -> spec-key registry with per-key formatting rules."""

    # Default rounding for spec keys NOT declared as integer-valued.
    _DEFAULT_DECIMALS: int = 3

    def __init__(
        self,
        mapping: Mapping[str, str] | None = None,
        *,
        int_keys: Iterable[str] = (),
    ) -> None:
        self._map: dict[str, str] = dict(mapping or {})
        # Spec keys that round to 0 decimals (status flags, counters).
        # Stored as a frozenset for O(1) membership.
        self._int_keys: frozenset[str] = frozenset(int_keys)

    # ---- registration --------------------------------------------------

    def register(self, dwarf_name: str, spec_key: str) -> None:
        """Add or override one DWARF -> spec mapping.

        Idempotent: re-registering the same pair is a no-op. Registering
        a different spec_key for an existing dwarf_name overrides.
        """
        self._map[dwarf_name] = spec_key

    def register_int_keys(self, keys: Iterable[str]) -> None:
        """Declare spec keys whose values round to integer.

        Called once at construction time; safe to call multiple times to
        layer on extra keys (e.g. a future register from a custom layout).
        """
        self._int_keys = frozenset(set(self._int_keys) | set(keys))

    # ---- lookup --------------------------------------------------------

    def resolve(self, dwarf_name: str) -> str | None:
        """Map a DWARF name to its spec key, or None if unknown."""
        return self._map.get(dwarf_name)

    def spec_keys(self) -> set[str]:
        """The set of spec keys the registry ever emits.

        Useful for tests ("did we register the expected set?") and for
        the dashboard plugin to know which ``status.*``/``mrac.*`` keys
        are guaranteed to be populated when the firmware emits matching
        DWARF names.
        """
        return set(self._map.values())

    def dwarf_names(self) -> set[str]:
        """The set of DWARF names the registry knows about.

        Symmetric to ``spec_keys()``. Tests use this to assert "every
        declared DWARF name in the dashboard manifest has a mapping".
        """
        return set(self._map.keys())

    def resolve_all(
        self,
        pairs: Iterable[tuple[str, float]],
    ) -> dict[str, float]:
        """Map (dwarf_name, value) pairs to ``{spec_key | dwarf_name: value}``.

        Unmapped DWARF names are emitted under their raw name so plugins
        can still read them. This preserves the S15 behaviour where
        unmapped vars appeared under their DWARF path.

        Per-key formatting:

        * Spec keys declared via ``register_int_keys`` round to int.
        * Everything else rounds to ``_DEFAULT_DECIMALS`` decimal places.

        The rounding keeps the dashboard payload compact and avoids
        floating-point noise (e.g. ``-1.1410000000000001`` for an angle
        that the firmware really meant ``-1.141``).
        """
        out: dict[str, float] = {}
        for dwarf_name, value in pairs:
            spec = self._map.get(dwarf_name, dwarf_name)
            decimals = 0 if spec in self._int_keys else self._DEFAULT_DECIMALS
            out[spec] = round(float(value), decimals)
        return out

    # ---- factory -------------------------------------------------------

    @classmethod
    def from_dashboard_manifest(cls, manifests_path: str | Path) -> "SchemaRegistry":
        """Load the slot-0 mapping from ``manifests.yaml:dashboard_frame_a``.

        The manifest YAML has a ``dashboard_frame_a`` section that lists
        the spec keys the dashboard cares about. Each entry carries a
        ``dwarf`` field naming the firmware symbol and a ``key`` field
        for the spec key. Status fields are marked ``int: true``.

        Falls back to an empty registry (not a hard error) if the file
        is missing or the section is absent -- the adapter then emits
        every DWARF name raw, which is still strictly better than the
        90-line hardcoded dict silently dropping 19 of 22 keys.
        """
        path = Path(manifests_path)
        if not path.is_file():
            return cls()
        try:
            import yaml  # type: ignore
        except ImportError:
            # PyYAML missing -- fall back to a tiny built-in loader for the
            # very small subset of YAML we use (nested dict, lists of dicts).
            data = _yaml_minimal(path)
        else:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        frame_a = (data.get("manifests") or {}).get("dashboard_frame_a") or {}
        vars_list = frame_a.get("vars") or []
        if not isinstance(vars_list, list):
            return cls()

        mapping: dict[str, str] = {}
        int_keys: set[str] = set()
        for entry in vars_list:
            if isinstance(entry, str):
                # Legacy / short form: bare DWARF path string. Use the
                # DWARF name verbatim as the spec key (no rename) and
                # don't declare it as an int-key (the spec key in this
                # case is the DWARF name itself, never a status flag).
                #
                # This is the format the dashboard_frame_a section uses
                # today. The richer ``{dwarf, key, int}`` form below is
                # the future direction once a YAML maintainer wants
                # to declare spec keys with intentional names.
                mapping[entry] = entry
                continue
            if not isinstance(entry, dict):
                continue
            dwarf = entry.get("dwarf")
            spec = entry.get("key")
            if not dwarf or not spec:
                # Lazy ``{name: spec}`` form -- allow either ``name`` or
                # ``dwarf`` as the source key.
                dwarf = dwarf or entry.get("name")
                spec = spec or entry.get("spec")
            if not dwarf or not spec:
                continue
            mapping[str(dwarf)] = str(spec)
            if entry.get("int"):
                int_keys.add(str(spec))
        return cls(mapping, int_keys=int_keys)

    @classmethod
    def builtin_dashboard(cls) -> "SchemaRegistry":
        """The canonical slot-0 mapping without touching the filesystem.

        Used by tests (no fixture dependency) and by the live service
        when ``manifests.yaml`` is unavailable. Mirrors the manifest's
        ``dashboard_frame_a`` section as of 2026-09-17 -- any new field
        added to the manifest should be reflected here too.

        This is the same mapping the legacy ``_slot0_to_sidebar`` dict
        contained, lifted out of the static method into the registry
        where it can be loaded from YAML or constructed in tests.
        """
        mapping: dict[str, str] = {
            # Attitude
            "imu_data.rol":          "status.roll_deg",
            "imu_data.pit":          "status.pitch_deg",
            "imu_data.yaw":          "status.yaw_deg",
            # Status flags (8 -- mirrors _decode_frame_a 9-byte block)
            "DroneStatus.ARM_Status":  "status.arm",
            "DroneStatus.FlyMode":     "status.flymode",
            "sbus_lost":               "status.sbus_lost",
            "TWC.execute":             "status.twc_execute",
            "TWC_arrived":             "status.twc_arrived",
            "s_authority":             "status.rc_authority",
            "g_of_hold_active":        "status.of_hold",
            "g_estimator_ready":       "status.estimator_ready",
            # Battery
            "real_voltage":            "status.vbat",
            # MRAC bars (8 -- mirrors _decode_frame_a 32-byte block)
            "mrac_state.pitch.e":      "mrac.pitch.e",
            "mrac_state.pitch.u_ad":   "mrac.pitch.u_ad",
            "mrac_state.roll.e":       "mrac.roll.e",
            "mrac_state.roll.u_ad":    "mrac.roll.u_ad",
            "mrac_state.yaw.e":        "mrac.yaw.e",
            "mrac_state.yaw.u_ad":     "mrac.yaw.u_ad",
            "mrac_state.z_rate.e":     "mrac.z.e",
            "mrac_state.z_rate.u_ad":  "mrac.z.u_ad",
            # MRAC full theta vector (4 axes * 6 weights = 24)
            "mrac_state.pitch.Theta[0]":  "mrac.pitch.theta_0",
            "mrac_state.pitch.Theta[1]":  "mrac.pitch.theta_1",
            "mrac_state.pitch.Theta[2]":  "mrac.pitch.theta_2",
            "mrac_state.pitch.Theta[3]":  "mrac.pitch.theta_3",
            "mrac_state.pitch.Theta[4]":  "mrac.pitch.theta_4",
            "mrac_state.pitch.Theta[5]":  "mrac.pitch.theta_5",
            "mrac_state.roll.Theta[0]":   "mrac.roll.theta_0",
            "mrac_state.roll.Theta[1]":   "mrac.roll.theta_1",
            "mrac_state.roll.Theta[2]":   "mrac.roll.theta_2",
            "mrac_state.roll.Theta[3]":   "mrac.roll.theta_3",
            "mrac_state.roll.Theta[4]":   "mrac.roll.theta_4",
            "mrac_state.roll.Theta[5]":   "mrac.roll.theta_5",
            "mrac_state.yaw.Theta[0]":    "mrac.yaw.theta_0",
            "mrac_state.yaw.Theta[1]":    "mrac.yaw.theta_1",
            "mrac_state.yaw.Theta[2]":    "mrac.yaw.theta_2",
            "mrac_state.yaw.Theta[3]":    "mrac.yaw.theta_3",
            "mrac_state.yaw.Theta[4]":    "mrac.yaw.theta_4",
            "mrac_state.yaw.Theta[5]":    "mrac.yaw.theta_5",
            "mrac_state.z_rate.Theta[0]": "mrac.z.theta_0",
            "mrac_state.z_rate.Theta[1]": "mrac.z.theta_1",
            "mrac_state.z_rate.Theta[2]": "mrac.z.theta_2",
            "mrac_state.z_rate.Theta[3]": "mrac.z.theta_3",
            "mrac_state.z_rate.Theta[4]": "mrac.z.theta_4",
            "mrac_state.z_rate.Theta[5]": "mrac.z.theta_5",
            # EKF 9-state body-frame vector (s_ekf.x[0..8])
            "s_ekf.x[0]":  "ekf.vel_x",
            "s_ekf.x[1]":  "ekf.vel_y",
            "s_ekf.x[2]":  "ekf.vel_z",
            "s_ekf.x[3]":  "ekf.bias_accel_x",
            "s_ekf.x[4]":  "ekf.bias_accel_y",
            "s_ekf.x[5]":  "ekf.bias_accel_z",
            "s_ekf.x[6]":  "ekf.bias_gyro_x",
            "s_ekf.x[7]":  "ekf.bias_gyro_y",
            "s_ekf.x[8]":  "ekf.bias_gyro_z",
            # -------------------------------------------------------------
            # Slot-0 subscribe aliases (2026-09-21 binding-table task).
            # Frame B/C quantities the shell reads under their legacy
            # spec keys are now streamed as raw DWARF symbols on slot 0
            # (boot_default_layout.DASHBOARD_FRAME_A_VARS). Frame C packs
            # exactly these symbols (TASK/send_data.c:1090-1130), Frame B
            # packs the PID loop fields (send_data.c:1196-1198), so each
            # alias names the same firmware variable the legacy decoder
            # read -- never a proxy.
            # -------------------------------------------------------------
            "Gyro_X_Real":       "c.gyro_x",
            "Gyro_Y_Real":       "c.gyro_y",
            "Gyro_Z_Real":       "c.gyro_z",
            "Acc_X_Real":        "imu.acc_x",
            "Acc_Y_Real":        "imu.acc_y",
            "Acc_Z_Real":        "imu.acc_z",
            "ano_of.earth_x":    "c.earth_x",
            "ano_of.earth_y":    "c.earth_y",
            # Altitude stays in cm in the symbol (ano_of.of_alt_cm);
            # Frame C divided by 100. The *_cm suffix keeps the unit
            # honest -- the panel converts for display.
            "ano_of.of_alt_cm":  "c.altitude_cm",
            "Ctrler.gyroxPID.FB": "pid.gyrox.FB",
            "Ctrler.gyroxPID.U":  "pid.gyrox.U",
            "Ctrler.gyroyPID.FB": "pid.gyroy.FB",
            "Ctrler.gyroyPID.U":  "pid.gyroy.U",
            "Ctrler.gyrozPID.FB": "pid.gyroz.FB",
            "Ctrler.gyrozPID.U":  "pid.gyroz.U",
            # Identity spellings: panels read these keys unprefixed while
            # the raw typed stream carries them as ``slot0.<name>``.
            "s_state":               "s_state",
            "flight_phase":          "flight_phase",
            "g_of_bias_mode":        "g_of_bias_mode",
            "g_of_bias_ema_freeze":  "g_of_bias_ema_freeze",
            "gs_max_horizontal_speed_mps": "gs_max_horizontal_speed_mps",
            "gs_max_vertical_speed_mps":   "gs_max_vertical_speed_mps",
            "gs_max_pitch_deg":             "gs_max_pitch_deg",
            "gs_max_roll_deg":              "gs_max_roll_deg",
            "gs_throttle_max_pct":          "gs_throttle_max_pct",
            "gs_throttle_min_pct":         "gs_throttle_min_pct",
        }
        int_keys = {
            "status.arm", "status.flymode", "status.sbus_lost",
            "status.twc_execute", "status.twc_arrived",
            "status.rc_authority", "status.of_hold",
            "status.estimator_ready",
            "s_state", "flight_phase",
            "g_of_bias_mode", "g_of_bias_ema_freeze",
        }
        return cls(mapping, int_keys=int_keys)


def _yaml_minimal(path: Path) -> dict:
    """Fallback YAML loader for the small subset of ``manifests.yaml``.

    Avoids the PyYAML dependency for tests / minimal environments. The
    ``manifests.yaml`` file uses a simple ``key: value`` and ``key:``-
    then-indented-block structure; nothing more exotic. If parsing
    fails, returns an empty dict (the registry will emit raw DWARF
    names, which is still strictly better than silent drops).
    """
    out: dict = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    stack: list[tuple[int, dict]] = [(-1, out)]
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        # Pop stack to current indent
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else out
        if content.startswith("- "):
            # List item under parent
            if isinstance(parent, list):
                item_val: object = content[2:].strip()
                parent.append(_coerce(item_val))
            continue
        if ":" not in content:
            continue
        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            # Nested mapping -- defer
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        elif val == "|" or val == ">":
            # Block scalar -- skip in minimal loader
            parent[key] = ""
        elif val.startswith("[") and val.endswith("]"):
            items = [s.strip() for s in val[1:-1].split(",")]
            parent[key] = [_coerce(s) for s in items if s]
        elif val.startswith("{") and val.endswith("}"):
            inner = val[1:-1]
            pairs = [p.split(":", 1) for p in inner.split(",") if ":" in p]
            parent[key] = {p[0].strip(): _coerce(p[1].strip()) for p in pairs}
        else:
            parent[key] = _coerce(val)
    return out


def _coerce(val: str) -> object:
    """Coerce a string token to int / float / bool / str -- minimal YAML subset."""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val