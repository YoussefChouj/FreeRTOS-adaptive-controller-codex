import importlib.util
import json
import subprocess
import sys
from pathlib import Path

def load_gate():
    repo_root = Path(__file__).resolve().parent.parent.parent
    gate_path = repo_root / ".agent-ops" / "gate.py"
    spec = importlib.util.spec_from_file_location("gate", gate_path)
    gate = importlib.util.module_from_spec(spec)
    sys.modules["gate"] = gate
    spec.loader.exec_module(gate)
    return gate

gate = load_gate()

def test_parse_hunks():
    diff = """\
+++ b/file1.c
@@ -10,0 +11,3 @@
+++ b/file2.c
@@ -5 +5 @@
+++ b/file3.c
@@ -7,2 +6,0 @@
+++ /dev/null
@@ -1,2 +0,0 @@
"""
    res = gate.parse_hunks(diff)
    assert res == {
        "file1.c": {11, 12, 13},
        "file2.c": {5},
        "file3.c": set()
    }

def test_parse_clang_tidy():
    text = """\
C:\\Users\\Acer\\repo\\USER\\main.c:12:5: warning: narrowing conversion [bugprone-narrowing-conversions]
C:\\Users\\Acer\\repo\\USER\\main.c:12:5: warning: narrowing conversion [bugprone-narrowing-conversions]
c:/users/acer/repo/HARDWARE/spi.c:40:1: error: unknown type name 'u8'
C:\\Users\\Acer\\repo\\USER\\main.c:13:1: note: expanded from macro
"""
    root = "C:/Users/Acer/repo"
    res = gate.parse_clang_tidy(text, root)
    assert len(res) == 2
    assert res[0] == gate.Finding("USER/main.c", 12, "bugprone-narrowing-conversions", "narrowing conversion")
    assert res[1] == gate.Finding("HARDWARE/spi.c", 40, "clang-diagnostic-error", "unknown type name 'u8'")

def test_parse_ruff_json():
    text = json.dumps([
        {"filename": "test.py", "location": {"row": 10}, "code": "E501", "message": "line too long"},
        {"filename": "test.py", "location": {"row": 12}, "code": "F401", "message": "unused import"}
    ])
    root = "/repo"
    res = gate.parse_ruff_json(text, root)
    assert len(res) == 2
    assert res[0] == gate.Finding("test.py", 10, "E501", "line too long")
    assert res[1] == gate.Finding("test.py", 12, "F401", "unused import")
    
    assert gate.parse_ruff_json("", root) == []

def test_filter_to_changed():
    findings = [
        gate.Finding("a.c", 10, "X", "msg"),
        gate.Finding("a.c", 20, "X", "msg"),
        gate.Finding("b.c", 10, "X", "msg")
    ]
    changed = {"a.c": {10, 11, 12}}
    res = gate.filter_to_changed(findings, changed)
    assert len(res) == 1
    assert res[0].path == "a.c" and res[0].line == 10

def test_check_scope():
    paths = ["OBJ/main.o", "USER/main.c", "tests/x.py"]
    assert gate.check_scope(paths, ["USER/*"]) == ["forbidden: OBJ/main.o", "outside allow-list: tests/x.py"]
    assert gate.check_scope(paths, []) == ["forbidden: OBJ/main.o"]

def test_map_tests(tmp_path):
    (tmp_path / "tests" / "a").mkdir(parents=True)
    (tmp_path / "tests" / "a" / "test_foo.py").touch()
    (tmp_path / "tests" / "test_bar.py").touch()
    
    paths = ["ground_station/foo.py", "tests/test_bar.py", "README.md"]
    res = gate.map_tests(paths, str(tmp_path))
    assert res == ["tests/a/test_foo.py", "tests/test_bar.py"]

def test_end_to_end_g1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    
    (tmp_path / "test.c").write_text("a\n" * 10)
    subprocess.run(["git", "add", "test.c"], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True)
    
    subprocess.run(["git", "checkout", "-b", "work"], check=True)
    (tmp_path / "test.c").write_text("b\n" * 260)
    subprocess.run(["git", "commit", "-am", "work"], check=True)
    
    monkeypatch.setattr(gate.shutil, "which", lambda x: None)
    monkeypatch.setattr(gate.pathlib.Path, "exists", lambda self: False)
    
    rc = gate.main(["--base", "main"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "FAIL size:" in out
    assert "SKIP clang-tidy: not installed" in out

def test_end_to_end_g2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    
    (tmp_path / "test.py").write_text("a\n" * 2)
    subprocess.run(["git", "add", "test.py"], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True)
    
    subprocess.run(["git", "checkout", "-b", "work"], check=True)
    (tmp_path / "test.py").write_text("b\n" * 7)
    subprocess.run(["git", "commit", "-am", "work"], check=True)
    
    def mock_run(cmd):
        if cmd[0] == "ruff" or cmd[0].endswith("ruff"):
            return 0, "[]"
        if "pytest" in cmd:
            return 0, "1 passed"
        return 0, ""
    
    monkeypatch.setattr(gate, "run", mock_run)
    
    def mock_which(cmd):
        if cmd == "ruff":
            return "ruff"
        return None
    monkeypatch.setattr(gate.shutil, "which", mock_which)
    
    rc = gate.main(["--base", "main", "--tests", "tests/"])
    assert rc == 0
    out, err = capsys.readouterr()
    assert "GATE PASS" in out

def test_end_to_end_g3(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    
    (tmp_path / "OBJ").mkdir()
    (tmp_path / "OBJ" / "x.o").write_text("a")
    subprocess.run(["git", "add", "OBJ/x.o"], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True)
    
    subprocess.run(["git", "checkout", "-b", "work"], check=True)
    (tmp_path / "OBJ" / "x.o").write_text("b")
    subprocess.run(["git", "commit", "-am", "work"], check=True)
    
    rc = gate.main(["--base", "main"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "forbidden: OBJ/x.o" in out

def test_end_to_end_g4(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    
    (tmp_path / "main.c").write_text("a")
    subprocess.run(["git", "add", "main.c"], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True)
    
    subprocess.run(["git", "checkout", "-b", "work"], check=True)
    (tmp_path / "main.c").write_text("b")
    subprocess.run(["git", "commit", "-am", "work"], check=True)
    
    monkeypatch.setattr(gate.shutil, "which", lambda x: None)
    monkeypatch.setattr(gate.pathlib.Path, "exists", lambda self: False)
    
    rc = gate.main(["--base", "main", "--strict"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "WARN pytest: no tests mapped" in out

def test_make_shims(tmp_path):
    (tmp_path / "FreeRTOS/portable/RVDS/ARM_CM4F").mkdir(parents=True)
    (tmp_path / "FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h").write_text("before __asm { /* comment */ } mid __asm { block } after")
    (tmp_path / "stm32_lib").mkdir(parents=True)
    (tmp_path / "stm32_lib/core_cmFunc.h").write_text('__ASM volatile ("VMSR fpscr, %0" : : "r" (fpscr) : "vfpcc");')
    
    dest = tmp_path / "dest"
    dest.mkdir()
    
    written = gate.make_shims(str(tmp_path), str(dest))
    assert written == 2
    
    portmacro_text = (dest / "portmacro.h").read_text()
    assert "__asm" not in portmacro_text
    
    core_text = (dest / "core_cmFunc.h").read_text()
    assert "vfpcc" not in core_text

def test_find_compdb(tmp_path, monkeypatch):
    import os
    (tmp_path / "compile_commands.json").touch()
    assert gate.find_compdb(str(tmp_path)) == str(tmp_path)
    
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    monkeypatch.chdir(repo2)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    (repo2 / "compile_commands.json").touch()
    (repo2 / "x").touch()
    subprocess.run(["git", "add", "x"], check=True)
    subprocess.run(["git", "commit", "-m", "init"], check=True)
    
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-d", str(wt)], check=True)
    
    monkeypatch.chdir(wt)
    res = gate.find_compdb(str(wt))
    assert res is not None
    assert os.path.samefile(res, str(repo2))

def test_end_to_end_g_with_compile_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    
    (tmp_path / "test.c").write_text("a\n" * 10)
    subprocess.run(["git", "add", "test.c"], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True)
    
    subprocess.run(["git", "checkout", "-b", "work"], check=True)
    (tmp_path / "test.c").write_text("b\n" * 3)
    subprocess.run(["git", "commit", "-am", "work"], check=True)
    
    monkeypatch.setattr(gate.shutil, "which", lambda x: "fake-tidy" if x == "clang-tidy" else None)
    monkeypatch.setattr(gate, "find_compdb", lambda root: str(tmp_path))
    
    def mock_run(cmd):
        if cmd[0] == "fake-tidy":
            return 1, f"{tmp_path}/inc/x.h:5:2: error: unsupported architecture [clang-diagnostic-error]"
        return 0, ""
    monkeypatch.setattr(gate, "run", mock_run)
    
    rc = gate.main(["--base", "main"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "compile errors (analysis incomplete)" in out

def test_to_rel():
    assert gate.to_rel("./USER/main.c", "/root") == "USER/main.c"

def test_make_shims_non_utf8(tmp_path):
    (tmp_path / "FreeRTOS/portable/RVDS/ARM_CM4F").mkdir(parents=True)
    content = b"/* " + "中文".encode("gbk") + b" */\n__asm { block }"
    (tmp_path / "FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h").write_bytes(content)
    
    dest = tmp_path / "dest"
    dest.mkdir()
    
    written = gate.make_shims(str(tmp_path), str(dest))
    assert written == 1
    
    out = (dest / "portmacro.h").read_bytes()
    assert "中文".encode("gbk") in out
    assert b"__asm" not in out
