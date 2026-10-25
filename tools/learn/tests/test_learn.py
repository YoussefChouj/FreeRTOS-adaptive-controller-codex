from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LEARN_DIR = REPO_ROOT / "tools" / "learn"
VENDOR_DIR = LEARN_DIR / "vendor"
CARDS_DIR = REPO_ROOT / ".claude" / "skills" / "learn" / "cards"
REAL_DATA_DIR = REPO_ROOT / "docs" / "learning" / "harness" / "data"
GOLDEN_FIXTURE_DIR = REPO_ROOT.parent / "learnharness-ref" / "tests" / "fixtures" / "golden-data"

if str(LEARN_DIR) not in sys.path:
    sys.path.insert(0, str(LEARN_DIR))

import learn  # noqa: E402


def _snapshot_dir(root: Path) -> dict[str, str]:
    """Return relative file paths mapped to SHA256 digests."""
    snap: dict[str, str] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


# ---------------------------------------------------------------------------
# Test 1: selftest exits 0
# ---------------------------------------------------------------------------
def test_1_selftest_exits_0() -> None:
    rc = learn.main(["selftest"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Test 2: Format parity with vendored graph.py and sessions.py
# ---------------------------------------------------------------------------
def test_2_format_parity(tmp_path: Path) -> None:
    real_before = _snapshot_dir(REAL_DATA_DIR)

    # 1. Run vendored scripts against real docs/learning/harness/data (read-only)
    g_real = subprocess.run(
        [sys.executable, str(VENDOR_DIR / "graph.py"), "--data-dir", str(REAL_DATA_DIR), "overview"],
        capture_output=True,
        text=True,
        check=False,
    )
    s_real = subprocess.run(
        [sys.executable, str(VENDOR_DIR / "sessions.py"), "--data-dir", str(REAL_DATA_DIR), "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert g_real.returncode == 0, g_real.stderr
    assert s_real.returncode == 0, s_real.stderr
    assert "# Overview" in g_real.stdout
    assert "# Sessions" in s_real.stdout

    # Verify real data dir was never mutated
    assert _snapshot_dir(REAL_DATA_DIR) == real_before

    # 2. Run vendored scripts against a temp copy of ../learnharness-ref/tests/fixtures/golden-data
    golden_copy = tmp_path / "golden-data"
    if GOLDEN_FIXTURE_DIR.exists():
        shutil.copytree(GOLDEN_FIXTURE_DIR, golden_copy)
    else:
        learn.ensure_data_dir(golden_copy)

    g_gold = subprocess.run(
        [sys.executable, str(VENDOR_DIR / "graph.py"), "--data-dir", str(golden_copy), "overview"],
        capture_output=True,
        text=True,
        check=False,
    )
    s_gold = subprocess.run(
        [sys.executable, str(VENDOR_DIR / "sessions.py"), "--data-dir", str(golden_copy), "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert g_gold.returncode == 0, g_gold.stderr
    assert s_gold.returncode == 0, s_gold.stderr
    assert "# Overview" in g_gold.stdout
    assert "# Sessions" in s_gold.stdout
    assert "Skipped/unreadable sessions: 0" in s_gold.stdout


# ---------------------------------------------------------------------------
# Test 3: Round trip in a temp data dir
# ---------------------------------------------------------------------------
def test_3_round_trip(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    # session new
    sid = learn.main_Return([
        "--data-dir", str(data_dir),
        "session", "new",
        "--topic", "PID Integral Action",
        "--goal", "Eliminate steady-state altitude sag with integral control",
        "--track", "internship",
    ]).strip()
    assert sid.startswith("chat_")

    # plan set (5 steps)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps([
            {"id": "s1", "title": "Proportional sag under gravity", "deps": [], "status": "current"},
            {"id": "s2", "title": "Integral term accumulation", "deps": ["s1"], "status": "todo"},
            {"id": "s3", "title": "Steady-state error elimination", "deps": ["s2"], "status": "todo"},
            {"id": "s4", "title": "Integrator windup on saturation", "deps": ["s3"], "status": "todo"},
            {"id": "s5", "title": "Conditional anti-windup clamping", "deps": ["s4"], "status": "todo"},
        ]),
        encoding="utf-8",
    )
    learn.main_Return(["--data-dir", str(data_dir), "plan", "set", sid, "--file", str(plan_path)])

    # log predict + reading + check
    for kind, payload_text in [
        (
            "predict",
            f"[learn] session={sid} step=s1 kind=predict confidence=60\n"
            "think1: P thrust is Kp * e, so zero error gives zero thrust\n"
            "q1: Drone hovers below target where Kp * e = m * g",
        ),
        (
            "reading",
            f"[learn] session={sid} step=s1 kind=reading confidence=75\n"
            "think1: The integral term accumulates past error to supply hover offset at e=0",
        ),
        (
            "check",
            f"[learn] session={sid} step=s1 kind=check confidence=90\n"
            "think1: Integral holds constant bias when error reaches zero\n"
            "think2: Ki * integral(e) balances gravity\n"
            "think3: Windup occurs if motors saturate\n"
            "q1: Integral term holds non-zero output at zero error\n"
            "q2: Steady-state error becomes zero",
        ),
    ]:
        pfile = tmp_path / f"{kind}.txt"
        pfile.write_text(payload_text, encoding="utf-8")
        learn.main_Return([
            "--data-dir", str(data_dir),
            "log", sid,
            "--kind", kind,
            "--step", "s1",
            "--payload-file", str(pfile),
        ])

    # plan advance -> moves to step 2 (s2)
    learn.main_Return(["--data-dir", str(data_dir), "plan", "advance", sid])

    # extract 3 nodes + 2 chains
    extract_path = tmp_path / "extract.json"
    extract_path.write_text(
        json.dumps({
            "nodes": [
                {
                    "id": "n_p_sag",
                    "name": "P-only steady-state sag",
                    "statement": "Proportional control requires non-zero error to balance a constant gravity load.",
                },
                {
                    "id": "n_integral_mem",
                    "name": "Integral error memory",
                    "statement": "The integral term accumulates error over time and retains its output when error is zero.",
                },
                {
                    "id": "n_zero_ss_err",
                    "name": "Zero steady-state error via integral action",
                    "statement": "Adding an integral term drives steady-state altitude error to zero under constant disturbance.",
                },
            ],
            "chains": [
                {"premises": ["n_p_sag", "n_integral_mem"], "conclusion": "n_zero_ss_err"},
                {"premises": ["n_integral_mem"], "conclusion": "n_zero_ss_err"},
            ],
        }),
        encoding="utf-8",
    )
    learn.main_Return(["--data-dir", str(data_dir), "extract", sid, "--file", str(extract_path)])

    # search integral finds both the session and the node
    search_json = json.loads(learn.main_Return(["--data-dir", str(data_dir), "search", "integral", "--json"]))
    hit_kinds = {h["kind"] for h in search_json["hits"]}
    hit_ids = {h["id"] for h in search_json["hits"]}
    assert "chat" in hit_kinds and sid in hit_ids
    assert "node" in hit_kinds and ("n_integral_mem" in hit_ids or "n_zero_ss_err" in hit_ids)

    # continue prints current step 2, the last card, and stays < 1500 bytes
    cont_out = learn.main_Return(["--data-dir", str(data_dir), "continue", sid])
    assert "current step 2" in cont_out.lower()
    assert "s2" in cont_out
    assert "[check]" in cont_out.lower()
    assert len(cont_out.encode("utf-8")) < 1500


# ---------------------------------------------------------------------------
# Test 4: session link A B bidirectional on CLI and HTTP API
# ---------------------------------------------------------------------------
def test_4_session_link_bidirectional(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sid_a = learn.main_Return([
        "--data-dir", str(data_dir),
        "session", "new", "--topic", "Session A", "--goal", "Goal A",
    ]).strip()
    sid_b = learn.main_Return([
        "--data-dir", str(data_dir),
        "session", "new", "--topic", "Session B", "--goal", "Goal B",
    ]).strip()

    learn.main_Return(["--data-dir", str(data_dir), "session", "link", sid_a, sid_b])

    show_a = learn.main_Return(["--data-dir", str(data_dir), "session", "show", sid_a])
    show_b = learn.main_Return(["--data-dir", str(data_dir), "session", "show", sid_b])
    assert sid_b in show_a
    assert sid_a in show_b

    status_a, _, body_a = learn.build_api_response(data_dir, "GET", f"/api/session/{sid_a}", {})
    status_b, _, body_b = learn.build_api_response(data_dir, "GET", f"/api/session/{sid_b}", {})
    assert status_a == 200 and status_b == 200
    doc_a = json.loads(body_a.decode("utf-8"))
    doc_b = json.loads(body_b.decode("utf-8"))
    links_a = doc_a.get("links") if "links" in doc_a else doc_a["session"]["links"]
    links_b = doc_b.get("links") if "links" in doc_b else doc_b["session"]["links"]
    assert sid_b in links_a
    assert sid_a in links_b


# ---------------------------------------------------------------------------
# Test 5: Viewer HTTP server ephemeral port & live plan advance < 3 s
# ---------------------------------------------------------------------------
def test_5_viewer_http_server(tmp_path: Path) -> None:
    data_dir = learn.ensure_data_dir(tmp_path / "data")
    sid = learn.main_Return([
        "--data-dir", str(data_dir),
        "session", "new", "--topic", "HTTP Test", "--goal", "Test live viewer polling",
    ]).strip()

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps([
            {"id": "step_1", "title": "First step", "deps": [], "status": "current"},
            {"id": "step_2", "title": "Second step", "deps": ["step_1"], "status": "todo"},
        ]),
        encoding="utf-8",
    )
    learn.main_Return(["--data-dir", str(data_dir), "plan", "set", sid, "--file", str(plan_path)])

    class _TestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            st, ctype, payload = learn.build_api_response(data_dir, "GET", parsed.path, qs)
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            st, ctype, payload = learn.build_api_response(data_dir, "POST", parsed.path, qs, body)
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{port}"
        routes = [
            "/api/sessions",
            f"/api/session/{sid}",
            f"/api/plan/{sid}",
            "/api/graph",
            "/api/search?q=HTTP",
            f"/api/practice/{sid}",
            "/api/jobs",
            "/api/demo/default",
        ]
        for route in routes:
            with urlopen(f"{base_url}{route}", timeout=3) as resp:
                assert resp.status == 200
                parsed_json = json.loads(resp.read().decode("utf-8"))
                assert parsed_json is not None

        # Verify POST /api/graph persists node positions
        req = Request(
            f"{base_url}/api/graph",
            data=json.dumps({"positions": {"n1": {"x": 42.5, "y": -18.0}}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=3) as resp:
            assert resp.status == 200
            pos_res = json.loads(resp.read().decode("utf-8"))
            assert pos_res["ok"] is True
            assert pos_res["positions"]["n1"] == {"x": 42.5, "y": -18.0}

        # Advance plan and verify /api/plan/<id> reflects it immediately (< 3 s)
        t0 = time.monotonic()
        learn.main_Return(["--data-dir", str(data_dir), "plan", "advance", sid])
        with urlopen(f"{base_url}/api/plan/{sid}", timeout=3) as resp:
            plan_after = json.loads(resp.read().decode("utf-8"))
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0
        assert plan_after["currentStep"] == "step_2"
        assert plan_after["currentStepIndex"] == 2
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Test 6: All 5 HTML cards meet constraints & parse_card_payload parses them
# ---------------------------------------------------------------------------
def test_6_cards_and_payload_parser() -> None:
    card_names = ("predict", "reading", "check", "probe", "practice")
    forbidden_tag_re = re.compile(r"<\s*/?\s*(?:!doctype|html|head|body)\b", re.IGNORECASE)
    slider_re = re.compile(
        r'<input\b[^>]*type=["\']range["\'][^>]*min=["\']0["\'][^>]*max=["\']100["\']',
        re.IGNORECASE,
    )

    for name in card_names:
        card_path = CARDS_DIR / f"{name}.html"
        assert card_path.exists(), f"Missing card file: {card_path}"
        raw_bytes = card_path.read_bytes()
        assert len(raw_bytes) < 6144, f"{name}.html is {len(raw_bytes)} bytes (>= 6144)"
        html_text = raw_bytes.decode("utf-8")
        assert not forbidden_tag_re.search(html_text), f"{name}.html contains forbidden outer HTML tags"
        assert slider_re.search(html_text), f"{name}.html missing 0-100 range confidence slider"

    sample_payloads = {
        "predict": (
            "[learn] session=chat_l02 step=s1 kind=predict confidence=70\n"
            "think1: If error is zero, P term outputs zero.\n"
            "q1: It hovers below the target altitude."
        ),
        "reading": (
            "[learn] session=chat_l02 step=s2 kind=reading confidence=80\n"
            "think1: The integral accumulates error*dt so u_I stays non-zero at e=0."
        ),
        "check": (
            "[learn] session=chat_l02 step=s2 kind=check confidence=85\n"
            "think1: Step 1 error accumulation\n"
            "think2: Step 2 steady state balance\n"
            "think3: Step 3 saturation clamp\n"
            "q1: Ki * sum(e)*dt provides hover thrust\n"
            "q2: Anti-windup stops integrating when saturated"
        ),
        "probe": (
            "[learn] session=chat_l02 step=probe1 kind=probe confidence=55 choice=B\n"
            "think1: Checking sign of gyro feedback on pitch axis\n"
            "q1: Positive pitch rate must produce negative restoring torque"
        ),
        "practice": (
            "[learn] session=chat_l02 step=prob_l02_integral kind=practice confidence=90 hints=1\n"
            "think1: e=0.2 for 2.5s gives integral=0.5, times Ki=4.0 gives 2.0 N\n"
            "q1: u_I = 2.0 N"
        ),
    }

    for kind, raw in sample_payloads.items():
        parsed = learn.parse_card_payload(raw)
        assert parsed["session"] == "chat_l02"
        assert parsed["kind"] == kind
        assert isinstance(parsed["confidence"], int)
        assert 0 <= parsed["confidence"] <= 100
        assert parsed["think1"]
        if kind != "reading":
            assert parsed["q1"]
        if kind == "probe":
            assert parsed["choice"] == "B"
        if kind == "practice":
            assert parsed["hints"] == 1


# ---------------------------------------------------------------------------
# Test 7: practice next difficulty rule & practice record updates progress.json
# ---------------------------------------------------------------------------
def test_7_practice_difficulty_and_internship_progress(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    progress_copy = tmp_path / "progress.json"
    progress_copy.write_text(
        json.dumps({
            "current": {"stage": 1, "lesson": "L02", "status": "in_progress"},
            "breakpoints": [
                {
                    "lesson": "L02",
                    "date": "2026-10-24",
                    "topic": "prob_l02_integral steady-state sag",
                    "symptom": "Forgot integral memory at zero error",
                    "fix": "Re-test integral accumulation",
                    "status": "open",
                }
            ],
        }),
        encoding="utf-8",
    )

    learn.main_Return(["--data-dir", str(data_dir), "import-internship"])
    prob = json.loads(
        learn.main_Return(["--data-dir", str(data_dir), "practice", "next", "chat_internship_l02", "--json"])
    )
    assert prob["difficulty"] == prob["number_of_ideas_combined"] + prob["cue_unfamiliarity"]
    assert prob["number_of_ideas_combined"] >= 1
    assert prob["cue_unfamiliarity"] in (0, 1, 2)

    rec_out = json.loads(
        learn.main_Return([
            "--data-dir", str(data_dir),
            "--internship-progress", str(progress_copy),
            "practice", "record", "chat_internship_l02",
            "--problem", prob["id"],
            "--result", "correct",
            "--hints", "0",
            "--json",
        ])
    )
    assert rec_out["ok"] is True
    assert rec_out["breakpointUpdated"] is True

    cov_rows = learn.read_jsonl(data_dir / "graph" / "coverage.jsonl")
    assert any(r.get("problemId") == prob["id"] and r.get("result") == "correct" for r in cov_rows)

    updated_prog = json.loads(progress_copy.read_text(encoding="utf-8"))
    bp0 = updated_prog["breakpoints"][0]
    assert bp0["status"] == "retested"
    assert bp0["last_practice"]["problem"] == prob["id"]
    assert bp0["last_practice"]["result"] == "correct"


# ---------------------------------------------------------------------------
# Test 8: job new --kind demo with helper_worker=argon writes brief & prints cmd
# ---------------------------------------------------------------------------
def test_8_job_new_argon_dispatch(tmp_path: Path) -> None:
    data_dir = learn.ensure_data_dir(tmp_path / "data")
    briefs_dir = tmp_path / "briefs"

    # Set helper_worker.demo = argon in settings.json
    settings_path = data_dir / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["helper_worker"]["demo"] = "argon"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    input_brief = tmp_path / "demo_brief.md"
    input_brief.write_text("Create an interactive Bode phase margin slider demo.", encoding="utf-8")

    out = learn.main_Return([
        "--data-dir", str(data_dir),
        "--briefs-dir", str(briefs_dir),
        "job", "new",
        "--kind", "demo",
        "--brief-file", str(input_brief),
    ])
    assert ".agent-ops/argon.ps1 -Id learn-" in out
    assert "-Brief " in out

    written_briefs = list(briefs_dir.glob("learn-job-*.md"))
    assert len(written_briefs) == 1
    assert "Bode phase margin" in written_briefs[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 9: import-internship is idempotent when run twice
# ---------------------------------------------------------------------------
def test_9_import_internship_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    learn.main_Return(["--data-dir", str(data_dir), "import-internship"])
    snap_first = _snapshot_dir(data_dir)
    nodes_first = learn.read_jsonl(data_dir / "graph" / "nodes.jsonl")
    chains_first = learn.read_jsonl(data_dir / "graph" / "chains.jsonl")
    assert len(nodes_first) >= 7
    assert len(chains_first) >= 4

    learn.main_Return(["--data-dir", str(data_dir), "import-internship"])
    snap_second = _snapshot_dir(data_dir)
    assert snap_second == snap_first


# ---------------------------------------------------------------------------
# Test 10: PARITY.md exists, covers transcript/prompts/UI, and drops nothing
# ---------------------------------------------------------------------------
def test_10_parity_matrix() -> None:
    parity_path = REPO_ROOT / "docs" / "learning" / "harness" / "PARITY.md"
    assert parity_path.exists(), f"Missing {parity_path}"
    text = parity_path.read_text(encoding="utf-8")
    table_rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    assert len(table_rows) >= 50, f"Expected comprehensive parity table, got {len(table_rows)} rows"
    assert "05-method.md" in text
    assert "10-planning.md" in text
    assert "20-teaching.md" in text
    assert "50-demos.md" in text
    assert "10-probe-doctrine.md" in text
    assert "10-doctrine.md" in text
    for line in table_rows:
        cells = [c.strip().lower() for c in line.split("|")]
        assert "dropped" not in cells, f"Found forbidden 'dropped' status in PARITY.md row: {line}"

