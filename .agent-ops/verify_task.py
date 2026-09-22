#!/usr/bin/env python3
"""Deterministic worker-result verifier."""

import re
import sys
import subprocess
import os
import tempfile
import shutil
from typing import List, Tuple


def scan_log(text: str) -> List[Tuple[str, str]]:
    """Scan opencode log text, returning (level, message) pairs."""
    # Strip ANSI escape sequences and Ctrl+C
    cleaned = re.sub(r'\x1b\[[0-9;]*m', '', text)
    cleaned = cleaned.replace('\x07', '')

    lines = cleaned.split('\n')
    findings = []

    cmd_lines = []
    edit_lines = []
    denied_count = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check for $ command lines
        if line.startswith('$ '):
            cmd = line[2:]
            cmd_lower = cmd.lower()

            # Check FAIL patterns
            fail = False
            fail_patterns = [
                r'8081',
                r'agent-keys',
                r'printenv',
                r'^env\s*$',
                r'rebuild_and_flash|safe_flash|flash-write|flash-erase',
                r'UV4(\.exe)?.* -f',
                r'pyocd\s+(flash|erase|reset)',
                r'livewatch.*\s(poke|reset|halt|step|resume|bp|wp|rtt-write|fault-erase)(\s|$)',
                r'git\s+(commit|push|reset|checkout|stash|clean|restore)\b',
                r'rm\s+-rf',
            ]
            for pat in fail_patterns:
                if re.search(pat, cmd, re.IGNORECASE):
                    findings.append(("FAIL", cmd))
                    fail = True
                    break

            if fail:
                continue

            # Check denied/permission warnings
            if 'denied' in cmd_lower or 'permission' in cmd_lower:
                denied_count += 1

            # Collect for loop detection
            cmd_lines.append(line)

        # Check for ← Edit lines for loop detection
        elif line.startswith('← Edit '):
            edit_lines.append(line)

    # WARN for denied/permission
    if denied_count > 0:
        findings.append(("WARN", f"denied/permission rejections: {denied_count}"))

    # WARN for loops (same $ or ← Edit line 5+ times)
    all_edit_lines = cmd_lines + edit_lines
    edit_counts = {}
    for edit_line in all_edit_lines:
        edit_counts[edit_line] = edit_counts.get(edit_line, 0) + 1
    for eline, count in edit_counts.items():
        if count >= 5:
            findings.append(("WARN", f"loop detected: {count}x {eline}"))

    return findings


def check_paths(paths: List[str], task_text: str) -> List[Tuple[str, str]]:
    """Check that paths are safe for the task."""
    # Forbidden prefixes (path starts with)
    forbidden_prefixes = [
        "API/", "TASK/", "BSP/", "USER/", "FreeRTOS/",
        "stm32_lib/", "OBJ/", ".git/",
    ]
    # Forbidden patterns (glob-like)
    forbidden_globs = [".agent-ops/*.sh", ".agent-ops/*.ps1"]
    forbidden_names = ["modules.yaml"]
    forbidden_substrings = ["agent-keys"]

    findings = []

    for path in paths:
        # Check if path starts with forbidden prefix
        fail = False
        for prefix in forbidden_prefixes:
            if path.startswith(prefix):
                findings.append(("FAIL", f"path starts with forbidden prefix {prefix!r}: {path}"))
                fail = True
                break
        if fail:
            continue

        # Check forbidden globs
        for glob_pat in forbidden_globs:
            if glob_pat in path:
                findings.append(("FAIL", f"path matches forbidden pattern {glob_pat!r}: {path}"))
                fail = True
                break
        if fail:
            continue

        # Check forbidden name
        basename = os.path.basename(path)
        for name in forbidden_names:
            if basename == name:
                findings.append(("FAIL", f"path name is forbidden: {path}"))
                fail = True
                break
        if fail:
            continue

        # Check forbidden substring
        for sub in forbidden_substrings:
            if sub in path:
                findings.append(("FAIL", f"path contains forbidden substring {sub!r}: {path}"))
                fail = True
                break
        if fail:
            continue

        # WARN outside task scope
        if path not in task_text and basename not in task_text:
            findings.append(("WARN", f"outside task scope: {path}"))

    return findings


def pick_tests(paths: List[str]) -> List[List[str]]:
    """Pick test argv lists from paths, deduplicated, stable order."""
    seen = set()
    result = []
    for path in paths:
        if path.startswith("ground_station/") and path.endswith('.py'):
            # Extract package: ground_station/<pkg>/<module>.py -> ground_station/<pkg>
            parts = path.split('/')
            if len(parts) >= 3:
                pkg = parts[1]
                argv = ["python", "-m", "pytest", "-q", "-x", f"ground_station/{pkg}"]
                key = tuple(argv)
                if key not in seen:
                    seen.add(key)
                    result.append(argv)
            elif len(parts) == 2:
                # ground_station/module.py -> ground_station/module
                mod = parts[1].removesuffix('.py')
                argv = ["python", "-m", "pytest", "-q", "-x", f"ground_station/{mod}"]
                key = tuple(argv)
                if key not in seen:
                    seen.add(key)
                    result.append(argv)
        elif path.startswith("docs/dashboard-platform/shell/") and path.endswith('.js'):
            argv = ["node", "ground_station/service/tests/node_harness.js"]
            key = tuple(argv)
            if key not in seen:
                seen.add(key)
                result.append(argv)
        elif path.startswith("ground_station/service/tests/") and path.endswith('.js'):
            argv = ["node", "ground_station/service/tests/node_harness.js"]
            key = tuple(argv)
            if key not in seen:
                seen.add(key)
                result.append(argv)
        elif path.startswith(".agent-ops/") and path.endswith('.py'):
            argv = ["python", "-m", "pytest", "-q", ".agent-ops/tests"]
            key = tuple(argv)
            if key not in seen:
                seen.add(key)
                result.append(argv)
    return result


def verdict(findings: List[Tuple[str, str]]) -> Tuple[str, int]:
    """Compute overall verdict from findings."""
    levels = [f[0] for f in findings]
    if "FAIL" in levels:
        return ("FAIL", 2)
    if "WARN" in levels:
        return ("WARN", 1)
    return ("PASS", 0)


def ignore(path: str) -> bool:
    """Check if a path is noise (should be excluded from diffs)."""
    noise_patterns = [
        ".agent-ops/tasks/",
        ".agent-ops/logs/",
        ".agent-ops/state.log",
        ".agent-ops/inbox/",
        "logs/",
        "__pycache__/",
        ".pyc",
    ]
    for pat in noise_patterns:
        if pat in path:
            return True
    return False


def main():
    if len(sys.argv) < 2:
        print("usage: verify_task.py <task_id> [--no-tests]", file=sys.stderr)
        sys.exit(2)

    task_id = sys.argv[1]
    no_tests = "--no-tests" in sys.argv

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = []

    # 1. running check
    try:
        result = subprocess.run(
            ["wsl", "-e", "bash", "-c", "pgrep -af 'run-worker[.]sh'"],
            capture_output=True, text=True, cwd=repo_root, timeout=30,
        )
        if task_id in result.stdout:
            print("VERDICT: RUNNING")
            sys.exit(3)
    except Exception:
        pass

    # 2. exit check
    state_log = os.path.join(repo_root, ".agent-ops", "state.log")
    if os.path.exists(state_log):
        with open(state_log, 'r') as f:
            content = f.read()
        exit_line = None
        for line in reversed(content.split('\n')):
            if f"[{task_id}] EXIT:" in line:
                exit_line = line
                break
        if exit_line:
            match = re.search(r'EXIT: rc=(\d+)', exit_line)
            if match:
                rc = int(match.group(1))
                if rc != 0:
                    findings.append(("FAIL", f"exit rc={rc}"))
                else:
                    findings.append(("OK", f"exit rc={rc}"))
        else:
            findings.append(("WARN", "no exit line in state.log"))
    else:
        findings.append(("WARN", "state.log not found"))

    # 3. result file check
    result_file = os.path.join(repo_root, ".agent-ops", "tasks", f"{task_id}.result.md")
    if os.path.exists(result_file):
        size = os.path.getsize(result_file)
        if size >= 600:
            findings.append(("OK", f"result.md exists, {size} bytes"))
        else:
            findings.append(("FAIL", f"result.md too small: {size} bytes"))
        with open(result_file, 'r') as f:
            lines = [l for l in f.readlines() if l.strip()]
        for line in lines[:8]:
            print(f"    {line.rstrip()}")
    else:
        findings.append(("FAIL", "result.md not found"))

    # 4. changed files
    pre_tree_file = os.path.join(repo_root, ".agent-ops", "tasks", f"{task_id}.pre-tree")
    changed = []
    if os.path.exists(pre_tree_file):
        try:
            with open(pre_tree_file, 'r') as f:
                pre_tree = f.read().strip()
            tmp_index = tempfile.NamedTemporaryFile(delete=False)
            tmp_index.close()
            shutil.copy(os.path.join(repo_root, ".git", "index"), tmp_index.name)
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = tmp_index.name
            subprocess.run(
                ["git", "-c", "core.safecrlf=false", "add", "-A", "--", ".", ":(exclude)OBJ"],
                cwd=repo_root, env=env, capture_output=True, text=True,
            )
            now_tree = subprocess.run(
                ["git", "write-tree"], cwd=repo_root, env=env,
                capture_output=True, text=True,
            ).stdout.strip()
            os.unlink(tmp_index.name)
            diff = subprocess.run(
                ["git", "diff", "--name-status", pre_tree, now_tree],
                cwd=repo_root, capture_output=True, text=True,
            ).stdout.strip()
            for line in diff.split('\n'):
                if not line.strip():
                    continue
                parts = line.split('\t')
                status = parts[0] if parts else ''
                path = parts[1] if len(parts) > 1 else ''
                if ignore(path):
                    continue
                if len(changed) < 25:
                    changed.append(f"    M {path}")
            if changed:
                changed.append("    diff: git diff <pre> <now> -- <path>")
        except Exception as e:
            findings.append(("WARN", f"pre-tree diff failed: {e}"))
    else:
        pre_status_file = os.path.join(repo_root, ".agent-ops", "tasks", f"{task_id}.pre-status")
        base = []
        if os.path.exists(pre_status_file):
            with open(pre_status_file, 'rb') as f:
                raw = f.read()
            if raw.startswith(b'\xef\xbb\xbf'):
                raw = raw[3:]
            base = raw.decode('utf-8', errors='replace').splitlines()
        try:
            status = subprocess.run(
                ["git", "status", "--short"], cwd=repo_root,
                capture_output=True, text=True,
            ).stdout.splitlines()
            # Normalise to the same "    M <path>" shape the pre-tree branch
            # emits, so downstream parsing has one format to handle. git status
            # --short puts a 2-char code in columns 1-2; everything after is the
            # path, which may itself start with 'M' or a space.
            raw_changed = [l for l in status if l not in base]
            changed = []
            for line in raw_changed[:25]:
                path = line[2:].strip() if len(line) > 2 else line.strip()
                if ignore(path):
                    continue
                changed.append(f"    M {path}")
                findings.append(("INFO", f"changed: {line}"))
            findings.append(("WARN", "no pre-tree: files already dirty at spawn are invisible"))
        except Exception as e:
            findings.append(("WARN", f"git status failed: {e}"))

    if changed:
        for c in changed[:25]:
            print(c)

    # Run check_paths on changed files vs task text
    task_text_file = os.path.join(repo_root, ".agent-ops", "tasks", f"{task_id}.md")
    task_text = ""
    if os.path.exists(task_text_file):
        with open(task_text_file, 'r') as f:
            task_text = f.read()
    # str.lstrip('M ') strips CHARACTERS, not a prefix: it turned "M MEMORY.md"
    # into "EMORY.md". Both producers of `changed` emit "    M <path>", so cut
    # the known prefix instead.
    path_lines = [c.strip()[2:].strip() for c in changed if c.strip().startswith('M ')]
    if path_lines:
        path_findings = check_paths(path_lines, task_text)
        findings.extend(path_findings)
    findings.append(("INFO", "note: other workers running at the same time also show up here"))

    # 5. log scan
    log_file = os.path.join(repo_root, ".agent-ops", "logs", f"{task_id}.out")
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            log_text = f.read()
        log_findings = scan_log(log_text)
        for level, msg in log_findings:
            findings.append((level, f"log: {msg}"))
    else:
        findings.append(("WARN", "log file not found"))

    # 6. secrets check
    # Scan every path the worker touched, plus its result file and its log: a
    # leaked key most often lands in a NEW file or gets echoed into the log, and
    # the old version scanned neither. Paths stay repo-relative with forward
    # slashes; the WSL side runs with the repo as its working directory, so a
    # Windows absolute path (backslashes) would simply fail to open and the scan
    # would report clean without having read the file.
    rel_files = sorted({p.replace("\\", "/") for p in path_lines})
    rel_files += [f".agent-ops/tasks/{task_id}.result.md",
                  f".agent-ops/logs/{task_id}.out"]
    scanned = [p for p in rel_files if os.path.exists(os.path.join(repo_root, p))]
    if not scanned:
        findings.append(("WARN", "secrets: nothing to scan"))
    else:
        try:
            # grep -l prints FILE NAMES ONLY; a key value must never reach stdout.
            # `set -o pipefail` plus the count guard below is what stops an empty
            # pattern list (missing or unreadable agent-keys.env) from looking
            # like a clean result: grep -f with no patterns matches nothing.
            bash_cmd = (
                "cd \"$1\"; shift; "
                "pat=$(env -i bash -c '"
                "set -a; . ~/.config/agent-keys.env 2>/dev/null; set +a; "
                "for v in $(compgen -e); do "
                "case $v in *KEY*|*TOKEN*|*SECRET*) "
                "val=${!v}; [ ${#val} -ge 16 ] && printf \"%s\\n\" \"$val\";; "
                "esac; done'); "
                "n=$(printf '%s' \"$pat\" | grep -c . || true); "
                "if [ \"$n\" -eq 0 ]; then echo 'NOPATTERNS' >&2; exit 9; fi; "
                "echo \"PATTERNS=$n\" >&2; "
                "printf '%s\\n' \"$pat\" | grep -lFf - -- \"$@\" || true"
            )
            wsl_root = "/mnt/" + repo_root[0].lower() + repo_root[2:].replace("\\", "/")
            cmd = ["wsl", "-e", "bash", "-c", bash_cmd, "_", wsl_root] + scanned
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=repo_root, timeout=120)
            if result.returncode == 9 or "NOPATTERNS" in result.stderr:
                findings.append(("WARN", "secrets: no key patterns loaded; "
                                         "scan proves nothing (check ~/.config/agent-keys.env)"))
            else:
                for f in result.stdout.strip().split('\n'):
                    if f.strip():
                        findings.append(("FAIL", f"key value found in {f.strip()}"))
                findings.append(("INFO", f"secrets: scanned {len(scanned)} file(s)"))
        except Exception as e:
            findings.append(("WARN", f"secrets check failed: {e}"))
        # Shape-based backstop, independent of whether agent-keys.env loaded.
        shapes = re.compile(
            r'sk-or-v1-[0-9a-zA-Z]{16,}|sk-ant-[0-9a-zA-Z_-]{16,}|'
            r'ghp_[0-9a-zA-Z]{20,}|AIza[0-9A-Za-z_-]{20,}|hf_[0-9a-zA-Z]{20,}'
        )
        for rel in scanned:
            try:
                with open(os.path.join(repo_root, rel), 'r',
                          encoding='utf-8', errors='replace') as fh:
                    if shapes.search(fh.read()):
                        findings.append(("FAIL", f"secret key pattern found in {rel}"))
            except Exception:
                pass

    # 7. tests
    if not no_tests:
        test_argvs = pick_tests(path_lines)
        for argv in test_argvs:
            try:
                result = subprocess.run(
                    argv, cwd=repo_root, capture_output=True, text=True, timeout=900,
                )
                output = result.stdout + result.stderr
                if argv[0] == "python" and "pytest" in argv:
                    last_summary = output.strip().split('\n')[-1] if output.strip() else ""
                    if "failed" in last_summary.lower() or "error" in last_summary.lower():
                        findings.append(("FAIL", f"pytest failed: {last_summary}"))
                    else:
                        findings.append(("OK", f"pytest: {last_summary}"))
                elif argv[0] == "node":
                    if "ALL GREEN" in output:
                        findings.append(("OK", "node tests passed"))
                    else:
                        findings.append(("FAIL", "node tests did not pass"))
            except subprocess.TimeoutExpired:
                findings.append(("FAIL", "tests timed out"))
            except Exception as e:
                findings.append(("WARN", f"test run failed: {e}"))

    # Verdict
    word, code = verdict([(f[0], f[1]) for f in findings])
    for level, msg in findings:
        print(f"[{level}] {msg}")
    print(f"VERDICT: {word}")
    sys.exit(code)


if __name__ == "__main__":
    main()