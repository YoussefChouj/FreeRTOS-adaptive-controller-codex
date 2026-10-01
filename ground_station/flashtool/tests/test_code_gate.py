import pytest
import os
from ground_station.flashtool.code_gate import CodeGate, load_protected_set, ProtectedSet

class FakeCustody:
    def __init__(self, tmp_path, fail_snapshot=False, missing_hex=False):
        self.tmp_path = tmp_path
        self.calls = []
        self.fail_snapshot = fail_snapshot
        self.missing_hex = missing_hex
        self._has_snapshot = False

    def has_snapshot(self, obj_dir):
        self.calls.append(("has_snapshot", obj_dir))
        return self._has_snapshot

    def snapshot(self, obj_dir):
        self.calls.append(("snapshot", obj_dir))
        if not self.fail_snapshot:
            self._has_snapshot = True
            os.makedirs(self.cache_dir(obj_dir), exist_ok=True)
            if not self.missing_hex:
                with open(os.path.join(self.cache_dir(obj_dir), "JX_FLY.hex"), "wb") as f:
                    f.write(b"fake_hex")

    def restore(self, obj_dir):
        self.calls.append(("restore", obj_dir))

    def commit(self, obj_dir):
        self.calls.append(("commit", obj_dir))
        
    def cache_dir(self, obj_dir):
        return str(self.tmp_path / "cache")

@pytest.fixture
def base_kwargs(tmp_path):
    return {
        "build": lambda: (0, "map_path"),
        "ram_check": lambda map_path: 1000,
        "sil": lambda c_files: (True, 1.0),
        "custody": FakeCustody(tmp_path),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
        "clock": lambda: 1.0,
        "obj_dir": str(tmp_path / "obj"),
        "ram_limit_bytes": 2000,
        "tolerance_frac": 0.1,
        "protected": ProtectedSet(
            paths=["protected.c"],
            regions=["test_region"],
            functions=["test_func"],
            param_ids=["test_param"],
            unresolved=[]
        ),
        "lkg_j": 1.0
    }

def test_real_yaml_loads():
    ps = load_protected_set()
    assert "rc_kill" in ps.regions
    assert "rc_ch5_land" in ps.regions
    assert "wfb_apply" in ps.regions
    assert "AutoflyTask_PathArbitrate" in ps.functions
    assert "TASK/RemoterTask.c" in ps.paths

@pytest.mark.parametrize("diff, files, expect_ok, reason_sub", [
    ("--- a/protected.c\n+++ b/protected.c\n@@ -1,1 +1,1 @@\n+foo\n", {"protected.c": "foo\n"}, False, "protected path"),
    ("--- a/open.c\n+++ b/open.c\n@@ -2,1 +2,1 @@\n+foo\n", {"open.c": "line1\n/* PROTECTED BEGIN test_region */\nfoo\n/* PROTECTED END test_region */\n"}, False, "protected region"),
    ("--- a/open.c\n+++ b/open.c\n@@ -3,1 +3,1 @@\n+foo\n", {"open.c": "void test_func() {\n  int x;\n  foo\n}\n"}, False, "protected function"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,1 +1,1 @@\n+int x = test_param;\n", {"open.c": "int x = test_param;\n"}, False, "param ID"),
    ("--- a/open.c\n+++ b/open.c\n@@ -10,1 +10,1 @@\n+foo\n", {"open.c": "void test_func() {\n}\n\n\n\n\n\n\n\nfoo\n"}, True, ""),
    ("--- a/protected.c\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-foo\n", {}, False, "protected path"),
    ("--- a/protected.c\n+++ b/x.c\n@@ -1,1 +1,1 @@\n-foo\n+foo\n", {"x.c": "foo\n"}, False, "protected path"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,1 +1,0 @@\n-/* PROTECTED END test_region */\n", {"open.c": ""}, False, "PROTECTED marker"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,1 +1,1 @@\n-foo\n+bar\n", {}, False, "Missing file text"),
    ("--- a/open.c\n+++ b/open.c\n@@ -6,1 +6,1 @@\n-foo\n+bar\n", {"open.c": "1\n2\nstatic void test_func(void);\n4\n5\nfoo\n7\n8\n9\nvoid test_func(void) {\n11\nbar\n13\n14\n}\n"}, True, ""),
    ("--- a/open.c\n+++ b/open.c\n@@ -12,1 +12,1 @@\n-foo\n+bar\n", {"open.c": "1\n2\nstatic void test_func(void);\n4\n5\nfoo\n7\n8\n9\nvoid test_func(void) {\n11\nbar\n13\n14\n}\n"}, False, "protected function"),
    # Removed C line "-- x;" is the diff line "--- x;": it must stay inside its hunk, not read as a file header.
    ("--- a/open.c\n+++ b/open.c\n@@ -2,3 +2,2 @@\n /* PROTECTED BEGIN test_region */\n--- x;\n int b;\n", {"open.c": "int a;\n/* PROTECTED BEGIN test_region */\nint b;\n/* PROTECTED END test_region */\n"}, False, "protected region"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,2 +1,2 @@\n--- x;\n+++ y;\n int a;\n@@ -7,1 +7,1 @@\n-int q;\n+int b;\n", {"open.c": "++ y;\nint a;\n\n\n\n/* PROTECTED BEGIN test_region */\nint b;\n/* PROTECTED END test_region */\n"}, False, "protected region"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,2 +1,1 @@\n--- test_param;\n int a;\n", {"open.c": "int a;\n"}, False, "param ID"),
    ("--- a/open.c\n+++ b/open.c\n@@ -1,2 +1,1 @@\n-a\n@@ -3,1 +3,1 @@\n+b\n", {"open.c": "x\n"}, False, "Malformed diff"),
])
def test_step1(base_kwargs, diff, files, expect_ok, reason_sub):
    gate = CodeGate(**base_kwargs)
    res = gate.check_change(diff, {"argument": "a", "predicted_effect": "b", "metric": "c"}, files)
    assert res.ok == expect_ok
    if not expect_ok:
        assert res.step == 1
        assert any(reason_sub in r for r in res.reasons)

def test_step1_f4b_bug(base_kwargs):
    base_kwargs["protected"].functions.append("AutoflyTask_PathArbitrate")
    gate = CodeGate(**base_kwargs)
    
    file_text = "1\nvoid f(void) { if (x) { AutoflyTask_PathArbitrate(); } }\n3\n4\nstatic void AutoflyTask_PathArbitrate(void)\n{\n7\n8\n}\n"
    
    # Hunk at line 2 (PASSES)
    res_pass = gate.check_change("--- a/open.c\n+++ b/open.c\n@@ -2,1 +2,1 @@\n-foo\n+bar\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": file_text})
    assert res_pass.ok

    # Hunk at line 7 (FAILS)
    gate2 = CodeGate(**base_kwargs)
    res_fail = gate2.check_change("--- a/open.c\n+++ b/open.c\n@@ -7,1 +7,1 @@\n-foo\n+bar\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": file_text})
    assert not res_fail.ok
    assert res_fail.step == 1
    assert any("protected function" in r for r in res_fail.reasons)

def test_step2_fail(base_kwargs):
    gate = CodeGate(**base_kwargs)
    diff = "+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n"
    res = gate.check_change(diff, {"argument": ""}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 2

def test_step2_pass(base_kwargs):
    gate = CodeGate(**base_kwargs)
    diff = "+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n"
    res = gate.check_change(diff, {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert res.ok
    assert 'justification' in open(base_kwargs["ledger_path"]).read()

def test_step3_fail_build(base_kwargs):
    base_kwargs["build"] = lambda: (1, "map_path")
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 3
    assert "Build failed" in res.reasons[0]

def test_step3_fail_ram(base_kwargs):
    base_kwargs["ram_check"] = lambda m: 3000
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 3
    assert "RAM usage" in res.reasons[0]

def test_step3_pass(base_kwargs):
    def fake_build():
        base_kwargs["custody"].calls.append(("build",))
        return (0, "map_path")
    base_kwargs["build"] = fake_build
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert res.ok
    calls = base_kwargs["custody"].calls
    assert ("snapshot", base_kwargs["obj_dir"]) in calls
    assert ("build",) in calls
    assert calls.index(("snapshot", base_kwargs["obj_dir"])) < calls.index(("build",))

def test_step4_fail_default_sil(base_kwargs):
    del base_kwargs["sil"]
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 4
    assert "SIL hook not wired" in res.reasons[0]

def test_step4_fail_unstable(base_kwargs):
    base_kwargs["sil"] = lambda c: (False, 1.0)
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 4

def test_step4_fail_worse_j(base_kwargs):
    base_kwargs["sil"] = lambda c: (True, 2.0)
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 4

def test_step4_pass(base_kwargs):
    base_kwargs["sil"] = lambda c: (True, 0.5)
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert res.ok

def test_step5_fail_custody(base_kwargs):
    base_kwargs["custody"] = FakeCustody(base_kwargs["custody"].tmp_path, fail_snapshot=True)
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 5

def test_step5_fail_missing_hex(base_kwargs):
    base_kwargs["custody"] = FakeCustody(base_kwargs["custody"].tmp_path, missing_hex=True)
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert not res.ok
    assert res.step == 5

def test_step5_pass(base_kwargs):
    gate = CodeGate(**base_kwargs)
    res = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert res.ok
    assert res.lkg_hash is not None

def test_step6_fail(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    res2 = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+bar\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "bar\n"})
    assert not res2.ok
    assert res2.step == 6

def test_step6_pass(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    gate.record_flight("f1", "hash")
    res2 = gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+bar\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "bar\n"})
    assert res2.ok

def test_step7_next_flight_must_hover_fail(base_kwargs):
    gate = CodeGate(**base_kwargs)
    assert not gate.next_flight_must_hover()

def test_step7_next_flight_must_hover_pass(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    assert gate.next_flight_must_hover()

def test_step8_revert_abort(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    dec = gate.on_flight_result(True, 0.5)
    assert dec == "revert"
    assert ("restore", base_kwargs["obj_dir"]) in base_kwargs["custody"].calls

def test_step8_revert_worse(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    dec = gate.on_flight_result(False, 2.0)
    assert dec == "revert"

def test_step8_keep(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.check_change("+++ b/open.c\n@@ -1,1 +1,1 @@\n+foo\n", {"argument": "a", "predicted_effect": "b", "metric": "c"}, {"open.c": "foo\n"})
    dec = gate.on_flight_result(False, 0.5)
    assert dec == "keep"
    assert ("commit", base_kwargs["obj_dir"]) in base_kwargs["custody"].calls

def test_step9_record_flight(base_kwargs):
    gate = CodeGate(**base_kwargs)
    gate.record_flight("flight123", "hash123")
    lines = open(base_kwargs["ledger_path"]).readlines()
    assert any("flight123" in line for line in lines)
