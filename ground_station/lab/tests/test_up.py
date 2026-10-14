"""up.py: lab up's table, what stops it, what asks once, and the hand-off to the queue runner."""
from ground_station.lab import fake_drone, up
from ground_station.lab import __main__ as lab
from ground_station.service import campaign_fly as fly

QUEUE = str(up.ROOT / "docs" / "lab" / "queue-2026-10-15.txt")
SERVICE = {"name": "service", "value": "pid 4242, instance ab12, WiFi 192.168.4.1", "pass": True}


class Bench:
    """Everything lab up looks at, faked: git, the fake drone, Drive, 8081 and its preflight."""

    def __init__(self, monkeypatch, tmp_path, checks=(SERVICE,), wifi="192.168.4.1", fake_alive=False):
        self.ctl, self.ran, self.asked, self.started = [], [], [], []
        self.checks, self.wifi, self.fake_alive = list(checks), wifi, fake_alive
        self.state = tmp_path / "day" / "queue_state.json"
        monkeypatch.setattr(up, "_git", lambda *a: "0" if "--count" in a else "abc123")
        monkeypatch.setattr(fake_drone, "ctl", self.fake_ctl)
        monkeypatch.setattr(up, "DRIVE", tmp_path)
        monkeypatch.setattr(up, "MIN_FREE_GB", 0.0)
        monkeypatch.setattr(fly, "pack_problem", lambda p: "" if p == "type2" else f"unknown pack {p!r}")
        monkeypatch.setattr(fly, "remember_pack", lambda p=None: "type2")
        monkeypatch.setattr(fly, "ensure_service", lambda base, port: self.started.append(port))
        monkeypatch.setattr(fly, "resolve_campaign", lambda src, pack: (tmp_path / "launch.yaml", src))
        monkeypatch.setattr(fly, "call", self.call)
        monkeypatch.setattr(up.events, "log", lambda *a, **k: None)
        monkeypatch.setattr(up.runner, "main", lambda argv: self.ran.append(argv) or 0)

    def fake_ctl(self, word, **k):
        self.ctl.append(word)
        return "error: empty" if self.fake_alive else "error: no reply from the fake drone on 14551 (timed out)"

    def call(self, base, route, body=None, timeout=10):
        assert route.startswith("/api/campaign/preflight?") and "pack=type2" in route and "launch.yaml" in route
        return 200, {"ok": all(c["pass"] is not False for c in self.checks), "checks": self.checks,
                     "wifi_host": self.wifi}

    def up(self, *args, queue=QUEUE, answer=""):
        def ask(prompt):
            self.asked.append(prompt)
            return answer
        return up.main([queue, "--no-camera", "--state", str(self.state), *args], ask=ask)

    def table(self):
        return (self.state.parent / "lab_up.txt").read_text(encoding="utf-8")


def test_all_ok_flies_the_queue_without_asking(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path)
    assert b.up("--pack", "2") == 0
    assert b.asked == [] and b.started == [8081] and b.ctl == [""]
    assert b.ran == [[QUEUE, "--pack", "type2", "--port", "8081", "--no-camera", "--state", str(b.state)]]
    assert "READY\n" in b.table() and "4 cards, 15 flights" in b.table()


def test_a_fail_row_stops_before_8081(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path)
    assert b.up("--pack", "9") == 1
    assert b.ran == [] and b.started == [] and "NOT READY (pack)" in b.table()


def test_a_queue_that_does_not_parse_stops(monkeypatch, tmp_path):
    bad = tmp_path / "queue.txt"
    bad.write_text("no-such-card\n", encoding="utf-8")
    b = Bench(monkeypatch, tmp_path)
    assert b.up("--pack", "2", queue=str(bad)) == 1
    assert b.ran == [] and "FAIL  queue" in b.table()


def test_a_warn_asks_once_and_q_quits(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path)
    monkeypatch.setattr(up, "DRIVE", tmp_path / "no-drive")
    assert b.up("--pack", "2", answer="q") == 0
    assert len(b.asked) == 1 and b.ran == [] and "READY, 1 to read (drive)" in b.table()


def test_a_failing_preflight_row_waits_for_the_arm(monkeypatch, tmp_path):
    rc = {"name": "rc_link", "value": "no SBUS frames", "pass": False, "fix": "switch the RC on"}
    b = Bench(monkeypatch, tmp_path, checks=(SERVICE, rc))
    assert b.up("--pack", "2") == 0
    assert len(b.asked) == 1 and len(b.ran) == 1
    assert "WAIT  rc_link" in b.table() and "-> switch the RC on" in b.table()


def test_8081_on_the_fake_drone_fails_on_a_lab_day(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path, wifi="127.0.0.2", fake_alive=True)
    assert b.up("--pack", "2") == 1
    assert b.ctl == ["", "quit"] and b.ran == []
    assert "FAIL  wiring" in b.table() and "taskkill /F /PID 4242" in b.table()


def test_the_rehearsal_wants_the_fake_drone(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path, wifi="127.0.0.2", fake_alive=True)
    assert b.up("--pack", "2", "--fake") == 0
    assert b.ctl == [""] and len(b.ran) == 1
    b = Bench(monkeypatch, tmp_path, fake_alive=False)
    assert b.up("--pack", "2", "--fake") == 1 and b.started == []


def test_8081_that_does_not_start_is_a_fail_row(monkeypatch, tmp_path):
    b = Bench(monkeypatch, tmp_path)

    def down(base, port):
        raise SystemExit("service did not answer on 8081 in 60 s: check its window")
    monkeypatch.setattr(fly, "ensure_service", down)
    assert b.up("--pack", "2") == 1 and "FAIL  service" in b.table() and b.ran == []


def test_lab_dispatches_its_words(monkeypatch):
    seen = []
    monkeypatch.setattr(up, "main", lambda argv: seen.append(argv) or 0)
    assert lab.main(["up", "--pack", "2"]) == 0 and seen == [["--pack", "2"]]
    assert lab.main([]) == 0 and lab.main(["nope"]) == 2
