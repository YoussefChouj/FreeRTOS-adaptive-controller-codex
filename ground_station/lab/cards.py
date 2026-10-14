"""Cards: one experiment as a short text file `docs/lab/cards/<name>.card`, expanded into the flights of the day's
queue (docs/lab/pipeline-design.md, decisions 2-4).

    # Does LQR or MPC hold the xy track better than PID?    <- the first comment is the hypothesis
    path lab_c_pid                                           <- the flight path: a .fly name or file
    stage screen                                             <- screen | list | confirm <screen card>
    ground fusion=3                                          <- lab settings for every variant (camera fusion mode)
    variant pid set g_xy_ctrl_kind=0 g_traj_ff_shift_s=0    <- a name, then any of: set k=v (firmware values, written
    variant tuned set g_traj_ff_shift_s=0.31 ask "gains?"       on the ground), ground k=v, ask "<check before>",
                                                                after "<step after, when the next variant differs>"
    baseline pid
    repeats 3                                                <- flights per variant (confirm 3, list 1 by default)
    metric overshoot_x_cm lower margin 2                     <- the ranking number; pass X = a per-flight limit,
                                                                margin M = the confirm verdict margin

Orders: screen = the baseline, the others in file order, the baseline again (one flight each, ranking only);
list = each variant `repeats` times, in file order; confirm = the screen's winner and its baseline in ABBA pairs
(B W W B B W), so a slow drift over the day favours neither. A confirm card copies the path, variants, baseline and
metric of its screen card unless it gives its own.
"""

from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ground_station.service.campaign_launch import ROOT

CARDS_DIR = ROOT / "docs" / "lab" / "cards"
PATHS_DIR = ROOT / "ground_station" / "service" / "campaigns"
STAGES = ("screen", "list", "confirm")
METRICS = ("overshoot_x_cm", "traj_xy_rms_cm", "cam_landing_cm")     # scored at each landing by the queue runner
GROUND = {"fusion": "camera fusion mode 1, 2 or 3", "min_v": "the lowest resting pack voltage to take off"}
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")     # the fly_file campaign name rule: <card>-<variant> must fit it


class CardError(ValueError):
    """Every problem in a card, each as `<file>:<line>: message`."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass
class Variant:
    name: str
    sets: dict = field(default_factory=dict)
    ground: dict = field(default_factory=dict)
    ask: str = ""
    after: str = ""


@dataclass
class Card:
    name: str
    hypothesis: str = ""
    path: str = ""
    stage: str = "list"
    source: str = ""                  # confirm: the screen card it confirms
    ground: dict = field(default_factory=dict)
    variants: dict = field(default_factory=dict)
    baseline: str = ""
    repeats: int = 0                  # 0: the stage default
    metric: str = ""
    better: str = "lower"
    pass_at: float | None = None
    margin: float = 0.0


@dataclass(frozen=True)
class Flight:
    card: str
    variant: str
    n: int                            # 1.. within the card


def _pairs(tokens, where, problems):
    out = {}
    for tok in tokens:
        key, eq, val = tok.partition("=")
        if eq and key:
            out[key] = val
        else:
            problems.append(f"{where}: expected key=value, got {tok!r}")
    return out


def _ground(pairs, where, problems):
    for key, val in pairs.items():
        if key not in GROUND:
            problems.append(f"{where}: unknown ground setting {key!r} (" + ", ".join(f"{k}: {d}" for k, d in GROUND.items()) + ")")
        elif key == "fusion" and val not in ("1", "2", "3") or key == "min_v" and not val.replace(".", "", 1).isdigit():
            problems.append(f"{where}: {key}={val}: {GROUND[key]}")
    return pairs


def _variant(rest, where, problems):
    v, target, toks = Variant(rest[0]), None, iter(rest[1:])
    for tok in toks:
        if tok in ("set", "ground"):
            target = v.sets if tok == "set" else v.ground
        elif tok in ("ask", "after"):
            text, target = next(toks, ""), None
            setattr(v, tok, text)
            if not text:
                problems.append(f"{where}: {tok} needs its text, in quotes")
        elif target is None:
            problems.append(f"{where}: {tok!r}: expected set, ground, ask or after")
        else:
            target.update(_pairs([tok], where, problems))
    _ground(v.ground, where, problems)
    return v


def _metric(card, rest, where, problems):
    card.metric, card.better, opts = rest[0], rest[1], rest[2:]
    if card.metric not in METRICS:
        problems.append(f"{where}: unknown metric {card.metric!r} (one of {', '.join(METRICS)})")
    if len(opts) % 2:
        problems.append(f"{where}: metric options come in pairs: pass X, margin M")
    for key, text in zip(opts[::2], opts[1::2]):
        try:
            x = float(text)
        except ValueError:
            problems.append(f"{where}: {key} {text!r} is not a number")
            continue
        if key == "pass":
            card.pass_at = x
        elif key == "margin":
            card.margin = x
        else:
            problems.append(f"{where}: unknown metric option {key!r} (pass, margin)")


def parse_card(text, src="<card>", load=None):
    """The Card in `text`, named after the file. `load(name)` reads the screen card of a confirm (default
    load_card). Raises CardError with every problem found."""
    problems = []
    card = Card(name=Path(src).stem)
    for n, raw in enumerate(text.splitlines(), 1):
        where = f"{src}:{n}"
        body = raw.split("#", 1)[0].strip()
        if not body:
            comment = raw.strip().lstrip("#").strip()
            if raw.strip().startswith("#") and comment and not card.hypothesis:
                card.hypothesis = comment
            continue
        try:
            word, *rest = shlex.split(body)
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        if word == "path" and len(rest) == 1:
            card.path = rest[0]
        elif word == "stage" and rest and rest[0] in STAGES and len(rest) == 1 + (rest[0] == "confirm"):
            card.stage, card.source = rest[0], (rest[1] if len(rest) == 2 else "")
        elif word == "ground":
            card.ground.update(_ground(_pairs(rest, where, problems), where, problems))
        elif word == "variant" and rest:
            v = _variant(rest, where, problems)
            if v.name in card.variants:
                problems.append(f"{where}: variant {v.name} given twice")
            card.variants[v.name] = v
        elif word == "baseline" and len(rest) == 1:
            card.baseline = rest[0]
        elif word == "repeats" and len(rest) == 1 and rest[0].isdigit() and int(rest[0]) > 0:
            card.repeats = int(rest[0])
        elif word == "metric" and len(rest) >= 2 and rest[1] in ("lower", "higher"):
            _metric(card, rest, where, problems)
        else:
            problems.append(f"{where}: cannot read {body!r} (the format is in ground_station/lab/cards.py)")
    if card.stage == "confirm":
        try:
            screen = (load or load_card)(card.source)
        except (OSError, ValueError) as e:
            problems.append(f"{src}: stage confirm {card.source}: " + str(e).replace("\n", "; "))
        else:
            card.path = card.path or screen.path
            card.ground = {**screen.ground, **card.ground}
            card.variants = card.variants or screen.variants
            card.baseline = card.baseline or screen.baseline
            if not card.metric:
                card.metric, card.better = screen.metric, screen.better
                card.pass_at, card.margin = screen.pass_at, screen.margin
    if not card.path:
        problems.append(f"{src}: no path line")
    if not card.variants:
        problems.append(f"{src}: no variant line")
    if card.stage != "list" and card.baseline not in card.variants:
        problems.append(f"{src}: baseline {card.baseline!r} is not one of the variants")
    if card.stage != "list" and not card.metric:
        problems.append(f"{src}: a {card.stage} needs a metric line")
    problems += [f"{src}: {card.name}-{v} must match ^[a-z0-9_-]{{1,40}}$ (it names the flight)"
                 for v in card.variants if not _NAME_RE.match(f"{card.name}-{v}")]
    if problems:
        raise CardError(problems)
    return card


def load_card(name):
    """A card by file path or by name (docs/lab/cards/<name>.card)."""
    p = Path(name)
    if not p.is_file():
        p = CARDS_DIR / f"{name}.card"
    return parse_card(p.read_text(encoding="utf-8"), str(p))


def flights(card, winner=None):
    """The card's flights in flying order. A confirm needs `winner`, the screen's pick."""
    names = list(card.variants)
    if card.stage == "screen":
        order = [card.baseline] + [v for v in names if v != card.baseline] + [card.baseline]
    elif card.stage == "confirm":
        if winner not in card.variants or winner == card.baseline:
            raise CardError([f"{card.name}: a confirm needs a winner other than the baseline, got {winner!r}"])
        pairs = [(card.baseline, winner) if i % 2 == 0 else (winner, card.baseline) for i in range(card.repeats or 3)]
        order = [v for pair in pairs for v in pair]
    else:
        order = [v for v in names for _ in range(card.repeats or 1)]
    return [Flight(card.name, v, i) for i, v in enumerate(order, 1)]


def path_file(card):
    p = Path(card.path)
    for c in (p, PATHS_DIR / p, PATHS_DIR / f"{card.path}.fly"):
        if c.suffix == ".fly" and c.is_file():
            return c
    raise CardError([f"{card.name}: no flight path {card.path!r} (a .fly file, or a name in {PATHS_DIR})"])


def fly_text(card, variant):
    """The card's flight path named <card>-<variant>, with the variant's set line last before the first step: the
    compiler folds the set lines before takeoff into one ground write, later keys win (fly_file.compile_fly)."""
    from ground_station.service.fly_file import HEADERS

    v = card.variants[variant]
    out = [f"# {card.name} / {variant}: {card.hypothesis or card.path}", f"campaign {card.name}-{variant}"]
    pending = ["set " + " ".join(f"{k}={x}" for k, x in v.sets.items())] if v.sets else []
    for raw in path_file(card).read_text(encoding="utf-8").splitlines():
        word = raw.split("#", 1)[0].split()[:1]
        if word == ["campaign"]:
            continue
        if word and pending and not raw[:1].isspace() and word[0] not in HEADERS + ("set",):
            out += pending
            pending = []
        out.append(raw)
    return "\n".join(out + pending) + "\n"


def write_fly(card, variant, out_dir):
    """fly_text written to <out_dir>/<card>-<variant>.fly (the flight's source, kept with its logs)."""
    out = Path(out_dir) / f"{card.name}-{variant}.fly"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(fly_text(card, variant), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check cards: each one's flights, every variant compiled.")
    ap.add_argument("cards", nargs="*", help="card names or files (default: every card in docs/lab/cards)")
    ap.add_argument("--winner", help="a confirm's winner (default: its first variant that is not the baseline)")
    a = ap.parse_args(argv)
    from ground_station.service.fly_file import compile_fly

    bad = 0
    for name in a.cards or sorted(p.stem for p in CARDS_DIR.glob("*.card")):
        try:
            card = load_card(name)
            win = a.winner or next((v for v in card.variants if v != card.baseline), None)
            fl = flights(card, win)
            for v in dict.fromkeys(f.variant for f in fl):
                compile_fly(fly_text(card, v), f"{card.name}-{v}.fly")
        except (OSError, ValueError) as e:
            bad += 1
            print(f"{name}: NOT OK\n  " + str(e).replace("\n", "\n  "))
            continue
        print(f"{card.name}: {card.stage}, {len(fl)} flights: {' '.join(f.variant for f in fl)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
