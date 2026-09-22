#!/usr/bin/env python3
"""Run a queue of worker tasks without a supervisor in the loop.

One task at a time: spawn, wait, verify, and only then move on. The queue
stops on a FAIL by default, because the whole point of `verify` is that a
worker's own DONE is a claim and not a result.

Two things happen automatically that the supervisor used to do by hand, and
mostly forgot to:

* **WARN goes to a reviewer.** A WARN is the interesting verdict -- not clean,
  not obviously broken -- and it is exactly the one a tired supervisor waves
  through. `review_warn` puts the findings in front of a model with no repo
  access and asks for a verdict, so the WARN arrives already triaged.
* **Every non-PASS is written to the friction log.** `.agent_memory/frictions.jsonl`
  only ever got entries when someone remembered; the failures that taught the
  most were the ones nobody had the energy to write up afterwards.

    python .agent-ops/task_queue.py add .agent-ops/tasks-pending-foo.md -w oc
    python .agent-ops/task_queue.py list
    python .agent-ops/task_queue.py run --max 3

The seam is `Ops`: everything that talks to agent-ops.ps1 or to a model lives
behind those four methods, so the run loop is testable end to end against a
fake without spawning anything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(REPO, ".agent-ops")
QUEUE_FILE = os.path.join(OPS, "queue.json")
FRICTIONS = os.path.join(REPO, ".agent_memory", "frictions.jsonl")

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


@dataclass
class Entry:
    """One queued task. Serialised verbatim, so every field is plain JSON."""
    file: str
    worker: str = "oc"
    model: str = "qwen"
    timeout_min: int = 120
    state: str = PENDING
    task_id: Optional[str] = None
    verdict: Optional[str] = None
    outcome: Optional[str] = None
    review: Optional[str] = None
    notes: List[str] = field(default_factory=list)


class Queue:
    """The queue file, loaded and saved whole.

    Saved after every state transition rather than at the end of the run: a
    run that is interrupted -- and a long one will be -- has to be resumable,
    and an in-memory queue would lose exactly the task that was interesting.
    """

    def __init__(self, path: str = QUEUE_FILE):
        self.path = path
        self.entries: List[Entry] = []
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.entries = []
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.entries = [Entry(**e) for e in raw]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump([asdict(e) for e in self.entries], fh, indent=2)

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)
        self.save()

    def pending(self) -> List[Entry]:
        return [e for e in self.entries if e.state == PENDING]


class Ops:
    """Everything outside this process: agent-ops.ps1, and a review model.

    The seam. The run loop below never shells out itself, so the whole loop --
    including the WARN review and the friction records -- is exercised in
    tests against a fake, with no worker, no quota and no wall-clock wait.
    """

    def __init__(self, repo: str = REPO):
        self.repo = repo
        self.script = os.path.join(repo, ".agent-ops", "agent-ops.ps1")

    def _ps(self, args: List[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", self.script] + args,
            cwd=self.repo, capture_output=True, text=True, timeout=timeout)

    def spawn(self, entry: Entry) -> Optional[str]:
        """Start the worker; return its task id, or None if it never started."""
        out = self._ps(["spawn", "-File", entry.file, "-Worker", entry.worker,
                        "-Model", entry.model,
                        "-TimeoutMin", str(entry.timeout_min)], timeout=300)
        for token in (out.stdout + out.stderr).split():
            # Task ids are yyyymmdd-hhmmss and nothing else in the output is.
            if len(token) == 15 and token[8] == "-" and \
                    token.replace("-", "").isdigit():
                return token
        return None

    def wait(self, task_id: str, timeout_min: int) -> str:
        out = self._ps(["wait", task_id, "-TimeoutMin", str(timeout_min)],
                       timeout=(timeout_min + 15) * 60)
        for line in out.stdout.splitlines():
            if line.startswith("OUTCOME:"):
                return line.strip()
        return "OUTCOME: unknown (no outcome line)"

    def verify(self, task_id: str) -> tuple:
        """(verdict, full text). Verdict is PASS / WARN / FAIL / RUNNING."""
        out = subprocess.run(
            [sys.executable, os.path.join(self.repo, ".agent-ops",
                                          "verify_task.py"), task_id],
            cwd=self.repo, capture_output=True, text=True, timeout=1800)
        text = out.stdout + out.stderr
        for line in text.splitlines():
            if line.startswith("VERDICT:"):
                return line.split(":", 1)[1].strip(), text
        return "UNKNOWN", text

    def review(self, prompt: str, model: str = "qwen") -> str:
        """Ask a model to triage text. Deliberately given no repo access.

        It runs in /tmp/ocwork/review, so the reviewer cannot read around the
        findings or edit anything on the strength of them -- it judges the
        evidence it was handed. That is containment, not a promise in a prompt.
        """
        model_id = {"qwen": "hetzner/Qwen/Qwen3.6-35B-A3B-FP8"}.get(model, model)
        script = "/mnt/c/Users/Acer/Desktop/UAV_lab/" \
                 "FreeRTOS-adaptive-controller-codex/.agent-ops/oc-worker.sh"
        try:
            out = subprocess.run(
                ["wsl", "-e", "bash", "-lc",
                 "mkdir -p /tmp/ocwork/review && cd /tmp/ocwork/review && "
                 "%s %s \"$(cat)\"" % (script, model_id)],
                input=prompt, capture_output=True, text=True, timeout=600)
            return (out.stdout or out.stderr).strip()[:4000]
        except (OSError, subprocess.SubprocessError) as exc:
            return "review unavailable: %s" % exc


REVIEW_PROMPT = """You are triaging the output of an automated verifier that
checked what an AI worker changed in a git repository. The verdict was WARN,
which means nothing failed outright but something was not clean.

Decide, for the findings below, whether a human needs to look. Answer in at
most 8 lines, in this shape:

VERDICT: benign | needs-review | likely-bug
WHY: <one or two sentences, naming the specific finding>
CHECK: <the single thing a human should look at first, or "nothing">

Do not speculate beyond the findings. You have no access to the repository;
if the findings are not enough to judge, say so and answer needs-review.

--- verifier output ---
%s
"""


def log_friction(record: dict, path: Optional[str] = None) -> None:
    """Append one JSON line. Never raises: a failed log must not fail a run.

    `path=None` rather than `path=FRICTIONS`: a default argument is bound once,
    at definition time, so the constant could never be pointed anywhere else --
    which meant a test run appended to the operator's real friction log.
    """
    path = path or FRICTIONS
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def friction_for(entry: Entry, text: str) -> dict:
    """The friction record for a task that did not come back clean.

    `kind` is taken from the taxonomy in .agent_memory/friction-taxonomy.md.
    The finding lines are carried verbatim rather than summarised -- a summary
    written now is a guess, and the raw line is what a later reader can act on.
    """
    findings = [l.strip() for l in text.splitlines()
                if l.strip().startswith(("FAIL", "WARN"))][:12]
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "task": entry.task_id,
        "worker": "%s/%s" % (entry.worker, entry.model),
        "kind": "tool" if entry.verdict == "FAIL" else "knowledge",
        "severity": "high" if entry.verdict == "FAIL" else "medium",
        "source": "queue.py (auto)",
        "what": "verify returned %s for %s" % (entry.verdict,
                                               os.path.basename(entry.file)),
        "outcome": entry.outcome,
        "findings": findings,
        "review": entry.review,
    }


def run_one(entry: Entry, ops: Ops, queue: Queue) -> Entry:
    """Spawn -> wait -> verify -> review -> record. Saves at every step."""
    entry.state = RUNNING
    queue.save()

    entry.task_id = ops.spawn(entry)
    if not entry.task_id:
        entry.state, entry.verdict = FAILED, "FAIL"
        entry.notes.append("spawn produced no task id")
        entry.outcome = "OUTCOME: never started"
        queue.save()
        log_friction(friction_for(entry, "FAIL: spawn produced no task id"))
        return entry
    queue.save()

    entry.outcome = ops.wait(entry.task_id, entry.timeout_min)
    queue.save()

    entry.verdict, text = ops.verify(entry.task_id)
    if entry.verdict == "WARN":
        entry.review = ops.review(REVIEW_PROMPT % text[-6000:], entry.model)
    entry.state = DONE if entry.verdict == "PASS" else FAILED
    queue.save()

    if entry.verdict != "PASS":
        log_friction(friction_for(entry, text))
    return entry


def run(queue: Queue, ops: Ops, max_tasks: Optional[int] = None,
        keep_going: bool = False, out=print) -> List[Entry]:
    """Work the queue. Stops at the first non-PASS unless keep_going.

    Stopping is the default on purpose: queued tasks tend to build on each
    other, and carrying on past a task whose result was never confirmed is how
    a bad edit ends up underneath four more.
    """
    done = []
    for entry in queue.pending():
        if max_tasks is not None and len(done) >= max_tasks:
            break
        out("== %s (%s/%s)" % (os.path.basename(entry.file), entry.worker,
                               entry.model))
        run_one(entry, ops, queue)
        out("   task=%s %s verdict=%s" % (entry.task_id, entry.outcome,
                                          entry.verdict))
        if entry.review:
            out("   review: " + entry.review.replace("\n", "\n           "))
        done.append(entry)
        if entry.verdict != "PASS" and not keep_going:
            out("stopping: %s did not verify clean (--keep-going to continue)"
                % entry.task_id)
            break
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("file")
    a.add_argument("-w", "--worker", default="oc")
    a.add_argument("-m", "--model", default="qwen")
    a.add_argument("-t", "--timeout-min", type=int, default=120)

    sub.add_parser("list")

    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=None)
    r.add_argument("--keep-going", action="store_true")

    args = ap.parse_args(argv)
    queue = Queue()

    if args.cmd == "add":
        # Forward slashes: the queue file is read by eye as often as by code,
        # and PowerShell takes either.
        path = os.path.relpath(os.path.abspath(args.file), REPO).replace("\\", "/")
        if not os.path.exists(os.path.join(REPO, path)):
            print("no such task file: %s" % args.file, file=sys.stderr)
            return 2
        queue.add(Entry(file=path, worker=args.worker, model=args.model,
                        timeout_min=args.timeout_min))
        print("queued %s (%d pending)" % (path, len(queue.pending())))
        return 0

    if args.cmd == "list":
        if not queue.entries:
            print("queue is empty")
        for i, e in enumerate(queue.entries):
            print("%2d  %-8s %-10s %-22s %s" % (
                i, e.state, "%s/%s" % (e.worker, e.model),
                e.task_id or "-", os.path.basename(e.file)))
            if e.verdict and e.verdict != "PASS":
                print("      verdict=%s %s" % (e.verdict, e.outcome or ""))
        return 0

    if args.cmd == "run":
        done = run(queue, Ops(), max_tasks=args.max, keep_going=args.keep_going)
        bad = [e for e in done if e.verdict != "PASS"]
        print("ran %d, %d did not verify clean" % (len(done), len(bad)))
        return 1 if bad else 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
