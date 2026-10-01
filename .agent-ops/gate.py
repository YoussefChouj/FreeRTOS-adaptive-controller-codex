import argparse
import fnmatch
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

EXCLUDE_FROM_SIZE = ["OBJ/*", "*.md", "*.hex", "*.axf", "*.htm", "*.lnp", "*.dep", "*.crf", "*.o", "*.d",
                     "*.png", "*.jpg", "*.pdf"]
FORBID = ["OBJ/*", "*.hex", "*.axf", ".mcp.json", ".claude/*", ".env*", "*secret*", "*.pem", "*.key"]
CLANG_TIDY_CHECKS = "-*,bugprone-*,clang-analyzer-*,-bugprone-easily-swappable-parameters"

CLANG_SHIMS = [
    ("FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h", r"__asm\s*\{.*?\}", ";"),
    ("stm32_lib/core_cmFunc.h", r'\s*:\s*"vfpcc"', ""),
]

def find_compdb(root: str) -> str | None:
    if (pathlib.Path(root) / "compile_commands.json").exists():
        return root
    try:
        common_dir = git("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        main_checkout = str(pathlib.Path(common_dir).parent)
        if (pathlib.Path(main_checkout) / "compile_commands.json").exists():
            return main_checkout
    except subprocess.CalledProcessError:
        pass
    return None

def make_shims(root: str, dest: str) -> int:
    written = 0
    for path, pattern, repl in CLANG_SHIMS:
        src_path = pathlib.Path(root) / path
        if src_path.exists():
            text = src_path.read_text(encoding="utf-8")
            text = re.sub(pattern, repl, text, flags=re.S)
            dest_path = pathlib.Path(dest) / src_path.name
            dest_path.write_text(text, encoding="utf-8")
            written += 1
    return written

@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str
    msg: str

def git(*args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True
    )
    return res.stdout

def changed_files(base: str) -> list[str]:
    stdout = git("diff", "--name-only", f"{base}...HEAD")
    return sorted(line for line in stdout.splitlines() if line)

def diff_size(base: str) -> int:
    stdout = git("diff", "--numstat", f"{base}...HEAD")
    total = 0
    for line in stdout.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            adds, dels, path = parts
            if adds == "-" and dels == "-":
                continue
            if any(fnmatch.fnmatch(path, pat) for pat in EXCLUDE_FROM_SIZE):
                continue
            total += int(adds) + int(dels)
    return total

def parse_hunks(diff_text: str) -> dict[str, set[int]]:
    res = {}
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            if path == "/dev/null":
                current_file = None
            elif path.startswith("b/"):
                current_file = path[2:]
                if current_file not in res:
                    res[current_file] = set()
        elif line.startswith("@@ ") and current_file is not None:
            match = re.search(r"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@", line)
            if match:
                c = int(match.group(1))
                d = int(match.group(2)) if match.group(2) is not None else 1
                if d > 0:
                    res[current_file].update(range(c, c + d))
    return res

def changed_lines(base: str) -> dict[str, set[int]]:
    return parse_hunks(git("diff", "-U0", f"{base}...HEAD"))

def check_scope(paths: list[str], allow: list[str]) -> list[str]:
    violations = []
    for p in paths:
        forbidden = False
        for pat in FORBID:
            if fnmatch.fnmatch(p, pat):
                violations.append(f"forbidden: {p}")
                forbidden = True
                break
        if not forbidden and allow:
            matched = False
            for pat in allow:
                if fnmatch.fnmatch(p, pat):
                    matched = True
                    break
            if not matched:
                violations.append(f"outside allow-list: {p}")
    return violations

def to_rel(path: str, root: str) -> str:
    path = path.replace("\\", "/")
    root = root.replace("\\", "/")
    
    if path.startswith("./"):
        path = path[2:]
        
    if len(path) > 1 and path[1] == ":":
        path = path[0].lower() + path[1:]
    if len(root) > 1 and root[1] == ":":
        root = root[0].lower() + root[1:]
        
    if path.lower().startswith(root.lower() + "/"):
        return path[len(root) + 1:]
    elif path.lower() == root.lower():
        return ""
    return pathlib.Path(path).as_posix()

def parse_clang_tidy(text: str, root: str) -> list[Finding]:
    findings = []
    seen = set()
    pat = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): (?P<sev>warning|error): (?P<msg>.*?)(?: \[(?P<code>[^\]]+)\])?$")
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            sev = m.group("sev")
            code = m.group("code")
            if not code:
                code = "clang-diagnostic-error" if sev == "error" else "unknown"
            
            p = to_rel(m.group("path"), root)
            f = Finding(
                path=p,
                line=int(m.group("line")),
                code=code,
                msg=m.group("msg")
            )
            if f not in seen:
                seen.add(f)
                findings.append(f)
    return findings

def parse_ruff_json(text: str, root: str) -> list[Finding]:
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
        
    findings = []
    seen = set()
    for item in data:
        p = to_rel(item["filename"], root)
        f = Finding(
            path=p,
            line=item["location"]["row"],
            code=item["code"],
            msg=item["message"]
        )
        if f not in seen:
            seen.add(f)
            findings.append(f)
    return findings

def filter_to_changed(findings: list[Finding], changed: dict[str, set[int]]) -> list[Finding]:
    return [f for f in findings if f.path in changed and f.line in changed[f.path]]

def map_tests(paths: list[str], root: str) -> list[str]:
    targets = set()
    root_path = pathlib.Path(root)
    for p in paths:
        if p.startswith("tests/") and p.endswith(".py") and pathlib.Path(p).name.startswith("test_"):
            targets.add(p)
        elif p.endswith(".py"):
            name = pathlib.Path(p).stem
            test_name = f"test_{name}.py"
            for test_path in root_path.rglob(test_name):
                if "tests" in test_path.parts:
                    targets.add(to_rel(str(test_path), root))
    return sorted(list(targets))

def run(cmd: list[str]) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return res.returncode, res.stdout + res.stderr
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output or ""

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="main")
    parser.add_argument("--max-lines", type=int, default=200)
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--tests", action="append", default=[])
    parser.add_argument("--strict", action="store_true")
    
    args = parser.parse_args(argv)
    
    try:
        root = git("rev-parse", "--show-toplevel").strip()
    except subprocess.CalledProcessError as e:
        print(f"GATE ERROR: {e}")
        return 2
        
    try:
        paths = changed_files(args.base)
        c_lines = changed_lines(args.base)
        size = diff_size(args.base)
    except subprocess.CalledProcessError as e:
        print(f"GATE ERROR: {e}")
        return 2

    failed_steps = []
    
    # 1. size
    if size <= args.max_lines:
        print(f"PASS size: {size}/{args.max_lines} lines")
    else:
        print(f"FAIL size: {size}/{args.max_lines} lines")
        failed_steps.append("size")
        
    # 2. scope
    violations = check_scope(paths, args.allow)
    if not violations:
        print(f"PASS scope: {len(paths)} files")
    else:
        print("FAIL scope:")
        for v in violations[:10]:
            print(f"  {v}")
        failed_steps.append("scope")
        
    # 3. clang-tidy
    changed_c_files = [p for p in paths if p.endswith(".c")]
    if not changed_c_files:
        print("SKIP clang-tidy: no C changes")
    else:
        tidy_exe = shutil.which("clang-tidy")
        if not tidy_exe:
            if pathlib.Path("C:/Program Files/LLVM/bin/clang-tidy.exe").exists():
                tidy_exe = "C:/Program Files/LLVM/bin/clang-tidy.exe"
                
        if not tidy_exe:
            print("SKIP clang-tidy: not installed")
        else:
            compdb = find_compdb(root)
            if compdb is None:
                print("SKIP clang-tidy: no compile_commands.json")
            else:
                with tempfile.TemporaryDirectory() as shim:
                    make_shims(root, shim)
                    cmd = [tidy_exe, "-p", compdb, f"--extra-arg-before=-I{shim}", f"--checks={CLANG_TIDY_CHECKS}", "--quiet", *changed_c_files]
                    rc, out = run(cmd)
                    all_findings = parse_clang_tidy(out, root)
                    errors = [f for f in all_findings if f.code == "clang-diagnostic-error"]
                    if errors:
                        print(f"FAIL clang-tidy: {len(errors)} compile errors (analysis incomplete)")
                        for f in errors[:10]:
                            print(f"  {f.path}:{f.line} [{f.code}] {f.msg}")
                        failed_steps.append("clang-tidy")
                    else:
                        findings = filter_to_changed(all_findings, c_lines)
                        if findings:
                            print(f"FAIL clang-tidy: {len(findings)} new findings")
                            for f in findings[:10]:
                                print(f"  {f.path}:{f.line} [{f.code}] {f.msg}")
                            failed_steps.append("clang-tidy")
                        else:
                            print(f"PASS clang-tidy: {len(changed_c_files)} files clean")

    # 4. ruff
    changed_py = [p for p in paths if p.endswith(".py")]
    if not changed_py:
        print("SKIP ruff: no Python changes")
    else:
        ruff_exe = shutil.which("ruff")
        if not ruff_exe:
            print("SKIP ruff: not installed")
        else:
            cmd = [ruff_exe, "check", "--output-format=json", "--exit-zero", *changed_py]
            rc, out = run(cmd)
            findings = filter_to_changed(parse_ruff_json(out, root), c_lines)
            if findings:
                print(f"FAIL ruff: {len(findings)} new findings")
                for f in findings[:10]:
                    print(f"  {f.path}:{f.line} [{f.code}] {f.msg}")
                failed_steps.append("ruff")
            else:
                print(f"PASS ruff: {len(changed_py)} files clean")

    # 5. pytest
    targets = map_tests(paths, root) + args.tests
    if not targets:
        print("WARN pytest: no tests mapped")
        if args.strict:
            failed_steps.append("pytest")
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]
        rc, out = run(cmd)
        out_lines = out.splitlines()
        last_line = out_lines[-1] if out_lines else ""
        if rc == 0:
            print(f"PASS pytest: {last_line}")
        else:
            print("FAIL pytest:")
            for line in out_lines[-10:]:
                print(f"  {line}")
            failed_steps.append("pytest")

    if failed_steps:
        print(f"GATE FAIL: {','.join(failed_steps)}")
        return 1
    else:
        print("GATE PASS")
        return 0

if __name__ == "__main__":
    sys.exit(main())
