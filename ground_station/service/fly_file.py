"""`.fly` flight scripts: one flight as a short text file that the operator or an agent writes, compiled to a
fly-mode campaign (campaign_schema) and checked with the same scenario rules as a campaign yaml.

    # circle then figure-8 from a 0.5 m hover        <- the first comment is the campaign objective
    campaign circle-f8                                <- optional, default: the file name
    rate 33                                           <- log rate, Hz
    log estimator_truth velocity_loops                <- log_plan groups
    slow 25 nn4 xy_ctrl                               <- slow lane: groups in their own slots, Hz optional (25)
    set g_xy_ctrl_kind=2                              <- set rows before takeoff: VAR_WRITE on the ground, before IDLE
    layers xy both                                   <- CMD 0x1D layer target, latched on the ground (xy|att|yaw|z)
    mrac_ref from=lqr                                <- the fitted x/y reference of a controller (logs/ref_fit/<kind>.json)
    mrac_prior from=20261021-193653-lab-e-exp-lqr-3l <- the MRAC prior (Theta) of an earlier session
    takeoff 0.5                                      <- optional, default takeoff 0.5; land is added when missing
    hold 3
    set traj_p.move_s=12                              <- traj_p.* fields are sticky for every later preset line
    preset circle circ_r_cm=30                        <- `run` is the same; own fields beat sticky ones
    set kp_id=3                                       <- any other row is a set step: VAR_WRITE in order, in flight
    preset figure8
    pattern layer=outer speed=slow axes=x,y periods=3  <- CMD 0x2E excitation pattern; must follow a hold
    step axis=z delta=0.15 hold=4                      <- a setpoint step through the onboard program (CMD 0x1C)
    program profile=scurve v=0.3                      <- indented lines are the ops of the step above
      goto x=0.5 dwell=3
    land

One line = one step: `kind [positional] key=value ...`; values are YAML scalars (`params="{r: 0.3}"` for a mapping,
quoted for spaces). Positional values: takeoff z, hold s, preset/run name, path shape. Header lines: campaign,
controller, rate, log, motion k=v, pack. Steps follow scenario_schema (takeoff, hold, goto, path, program, excite,
pattern, step, livetune, preset, set, land); a schema problem is reported with the line it came from.

The ground verbs (layers / xy_layers, mrac_ref, mrac_prior) are written on the ground, before takeoff, and land in
the takeoff's `ground` list (the same list the `set` rows before the takeoff use). Every write is idempotent and
absolute, so the runner can resend one whose reply was lost.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from ground_station.service.campaign_launch import ROOT, write_launch_copy
from ground_station.service.ground_plan import (
    GroundWriteError,
    InputNotReady,
    layer_write,
    prior_writes,
    ref_writes,
    refusing_write,
)

FLY_DIR = ROOT / "logs" / "campaigns" / "fly"  # compiled campaign yaml of each .fly file (the launch template)
DEFAULT_TAKEOFF_Z = 0.5  # operator 2026-10-14: fly-up z stays 0.5 m
DEFAULT_RATE_HZ = 33
DEFAULT_LOG = ("estimator_truth", "optical_flow", "velocity_loops", "takeoff_gate", "thrust_model", "ekf_states")
POSITIONAL = {"takeoff": "z", "hold": "s", "preset": "name", "path": "shape"}
ALIASES = {"run": "preset"}
HEADERS = ("campaign", "controller", "rate", "log", "slow", "motion", "pack")
# The verbs that write on the ground, before the takeoff: they become ground writes, not scenario steps.
GROUND_VERBS = ("layers", "xy_layers", "mrac_ref", "mrac_prior")
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_STEPS_RE = re.compile(r"\bsteps\.(\d+)\b")


class FlyFileError(ValueError):
    """Every problem in a .fly file, each as `<file>:<line>: message`."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def _value(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _pairs(tokens: list[str], where: str, problems: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tok in tokens:
        key, eq, val = tok.partition("=")
        if not eq or not key:
            problems.append(f"{where}: expected key=value, got {tok!r}")
        elif key in out:
            problems.append(f"{where}: {key} given twice")
        else:
            out[key] = _value(val)
    return out


def _default_name(src: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", Path(src).stem.lower()).strip("-")[:40]
    return name or "fly"


def _ground_verb(word: str, rest: list[str], where: str, problems: list[str]) -> list[dict[str, Any]]:
    """The ground writes of one ground verb line.

    layers <group> <inner|outer|both|off>   one CMD 0x1D layer target
    xy_layers <inner|outer|both|off>        the historical alias of `layers xy` (one argument)
    mrac_ref from=<kind>                    the fitted x/y reference of a controller (logs/ref_fit/<kind>.json)
    mrac_prior from=<session>               the MRAC prior (Theta) of an earlier session
    """
    if word == "layers":
        if len(rest) != 2:
            problems.append(f"{where}: expected layers <group> inner|outer|both|off "
                            f"(groups: xy, att, yaw, z)")
            return []
        return [layer_write(rest[0], rest[1])]
    if word == "xy_layers":
        # The alias of `layers xy`: one argument, the target.
        if len(rest) != 1:
            problems.append(f"{where}: expected xy_layers inner|outer|both|off")
            return []
        return [layer_write("xy", rest[0])]
    if word == "mrac_ref":
        fields = _pairs(rest, where, problems)
        kind = fields.pop("from", None)
        if fields:
            problems.append(f"{where}: mrac_ref takes only from=<kind> (got {', '.join(fields)})")
            return []
        if not isinstance(kind, str):
            problems.append(f"{where}: expected mrac_ref from=<kind>")
            return []
        try:
            return ref_writes(kind)
        except InputNotReady as e:
            return [refusing_write(str(e), f"ref_fit:{kind}")]
    if word == "mrac_prior":
        fields = _pairs(rest, where, problems)
        session = fields.pop("from", None)
        if fields:
            problems.append(f"{where}: mrac_prior takes only from=<session> (got {', '.join(fields)})")
            return []
        if not isinstance(session, str):
            problems.append(f"{where}: expected mrac_prior from=<session>")
            return []
        try:
            return prior_writes(session)
        except InputNotReady as e:
            return [refusing_write(str(e), f"prior:{session}")]
    return []


def compile_fly(text: str, src: str = "<fly>") -> tuple[dict[str, Any], list[int]]:
    """(campaign data, source line of each scenario step). Raises FlyFileError with every problem found."""
    problems: list[str] = []
    head: dict[str, Any] = {"controller": "pid", "rate": DEFAULT_RATE_HZ, "log": list(DEFAULT_LOG), "slow": [], "motion": {}}
    objective = ""
    steps: list[dict[str, Any] | str] = []
    lines: list[int] = []
    sticky: dict[str, Any] = {}
    exp_target: tuple[str, int] | None = None
    ground_writes: list[dict[str, Any]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        where = f"{src}:{n}"
        body = raw.split("#", 1)[0].rstrip()
        if not body.strip():
            comment = raw.strip().lstrip("#").strip()
            if raw.strip().startswith("#") and comment and not objective:
                objective = comment
            continue
        try:
            tokens = shlex.split(body.strip(), posix=True)
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        word, rest = tokens[0], tokens[1:]
        if raw[:1] in " \t":  # an op of the step above (program)
            parent = steps[-1] if steps else None
            if not isinstance(parent, dict) or "program" not in parent:
                problems.append(f"{where}: an indented line must follow a program line")
                continue
            parent["program"].setdefault("ops", []).append({word: _pairs(rest, where, problems)})
            continue
        if word == "exp":
            from ground_station.platform import exp_rows
            valid = [r.name for r in exp_rows.exp_rows()]
            if steps:
                problems.append(f"{where}: exp must come before every step line (later set lines override its columns)")
            elif len(rest) != 1 or "=" in rest[0]:
                problems.append(f"{where}: expected exp <name>; valid: {', '.join(valid)}")
            elif exp_target is not None:
                problems.append(f"{where}: exp given twice")
            else:
                row = exp_rows.exp_by_name(rest[0])
                if row is None:
                    problems.append(f"{where}: unknown exp {rest[0]!r}; valid: {', '.join(valid)}")
                else:
                    exp_target = (row.name, row.id)
            continue
        verb = ALIASES.get(word, word)
        if verb in GROUND_VERBS:
            try:
                ground_writes += _ground_verb(verb, rest, where, problems)
            except GroundWriteError as e:
                problems.append(f"{where}: {e}")
            continue
        if word in HEADERS:
            if steps:
                problems.append(f"{where}: header line {word!r} must come before the first step")
            elif word == "log":
                head["log"] = rest
            elif word == "slow":
                hz = _value(rest[0]) if rest else None
                if isinstance(hz, (int, float)) and not isinstance(hz, bool):
                    head["slow_hz"], rest = hz, rest[1:]
                head["slow"] = rest
            elif word == "motion":
                head["motion"].update(_pairs(rest, where, problems))
            elif len(rest) != 1:
                problems.append(f"{where}: {word} takes one value")
            else:
                head[word] = rest[0] if word != "rate" else _value(rest[0])
            continue
        kind = ALIASES.get(word, word)
        if rest and "=" not in rest[0] and kind in POSITIONAL:
            first, rest = {POSITIONAL[kind]: _value(rest[0])}, rest[1:]
        else:
            first = {}
        if kind == "land" and not rest and not first:
            steps.append("land")
            lines.append(n)
            continue
        fields = {**first, **_pairs(rest, where, problems)}
        if kind == "set":
            traj = {k: v for k, v in fields.items() if k.startswith("traj_p.")}
            sticky.update(traj)
            fields = {k: v for k, v in fields.items() if k not in traj}
            if not fields:
                continue
        elif kind == "preset":
            fields = {**{k.split(".", 1)[1]: v for k, v in sticky.items()}, **fields}
        elif kind in ("pattern", "livetune") and isinstance(fields.get("axes"), str):
            fields["axes"] = [a.strip() for a in fields["axes"].split(",") if a.strip()]
        steps.append({kind: fields})
        lines.append(n)

    # set rows before the takeoff are written on the ground: they fold into the takeoff's set
    lead = next((i for i, s in enumerate(steps) if not (isinstance(s, dict) and "set" in s)), len(steps))
    ground = {k: v for s in steps[:lead] for k, v in s["set"].items()}
    del steps[:lead], lines[:lead]
    if not steps and exp_target is None:
        problems.append(f"{src}: no steps")
    if steps and not (isinstance(steps[0], dict) and "takeoff" in steps[0]):
        steps.insert(0, {"takeoff": {"z": DEFAULT_TAKEOFF_Z}})
        lines.insert(0, 0)
    elif not steps and exp_target is not None:
        steps.insert(0, {"takeoff": {"z": DEFAULT_TAKEOFF_Z}})
        lines.insert(0, 0)
    if steps and (ground or exp_target is not None or ground_writes):
        combined = {}
        if exp_target is not None:
            combined["exp_id"] = exp_target[1]
        combined.update(ground)
        if "set" in steps[0]["takeoff"]:
            combined.update(steps[0]["takeoff"]["set"])
        if combined:
            steps[0]["takeoff"]["set"] = combined
        if ground_writes:
            # The ground writes go before the VAR_WRITEs: the layer target and the reference are what the exp row
            # and the set rows then build on (an exp row writes the reference row of its own kind).
            steps[0]["takeoff"]["ground"] = ground_writes
    if steps and steps[-1] != "land":
        steps.append("land")
        lines.append(0)
    name = head.get("campaign") or _default_name(src)
    if not isinstance(name, str) or not _NAME_RE.match(name):
        problems.append(f"{src}: campaign name {name!r} must match ^[a-z0-9_-]{{1,40}}$")
    if problems:
        raise FlyFileError(problems)

    scenario: dict[str, Any] = {"scenario": name, "description": objective or f"{Path(src).name} flight"}
    if head["motion"]:
        scenario["motion"] = head["motion"]
    scenario["steps"] = steps
    data = {
        "campaign": name,
        "objective": objective or f"{Path(src).name} flight",
        "controller": head["controller"],
        "mode": "fly",
        "packs": [head.get("pack", "unset")],
        "max_flights": 1,
        "experiments": [{
            "name": f"f01_{name}".replace("-", "_"),
            "scenario": scenario,
            "capture": "campaign",
            "repeats": 1,
            "log_plan": {"rate_hz": head["rate"], "groups": list(head["log"]),
                         **({"slow": list(head["slow"])} if head["slow"] else {}),
                         **({"slow_hz": head["slow_hz"]} if "slow_hz" in head else {})},
        }],
        "abort": {},
    }
    _check_steps(scenario, lines, src)
    return data, lines


def _check_steps(scenario: dict[str, Any], lines: list[int], src: str) -> None:
    """The scenario rules, with `steps.N` replaced by the .fly line it came from (0 = added by the compiler)."""
    from ground_station.service.scenario_schema import ScenarioError, parse_scenario

    try:
        parse_scenario(scenario)
    except ScenarioError as e:
        def at(m: re.Match) -> str:
            i = int(m.group(1))
            line = lines[i] if i < len(lines) else 0
            return f"line {line}" if line else f"added step {i}"
        raise FlyFileError([f"{src}: " + _STEPS_RE.sub(at, p) for p in e.problems]) from None


def write_campaign(path: Path, out_dir: Path | None = None) -> Path:
    """Compile `path` to `<out_dir>/<campaign>.yaml` (default logs/campaigns/fly/) and return it."""
    data, _ = compile_fly(path.read_text(encoding="utf-8"), str(path))
    out = Path(out_dir or FLY_DIR) / f"{data['campaign']}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# compiled from {path.name} by fly_file; edit the .fly file, not this one.\n"
                   + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out


def launch_fly(path: Path, pack: str, rate: float | None = None, out_dir: Path | None = None,
               launch_dir: Path | None = None) -> tuple[Path, Any, list[dict[str, Any]]]:
    """Compile, then campaign_launch.write_launch_copy: (launch copy path, Campaign, log plans)."""
    return write_launch_copy(write_campaign(path, out_dir), pack, rate, launch_dir)
