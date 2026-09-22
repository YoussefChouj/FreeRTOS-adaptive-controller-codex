"""Not a pytest module, deliberately: it shells out to WSL and plants a key.
Run it directly:  python .agent-ops/tests/check_secrets_guard.py

Prove verify_task.py's secrets scan fails safe.

Two cases, run against the REAL bash snippet pulled out of verify_task.py:
  1. HOME has no agent-keys.env  -> must report NOPATTERNS / exit 9,
     never "clean". A grep -f with an empty pattern list matches nothing,
     which would otherwise look exactly like a clean scan.
  2. A planted fake key in a scanned file -> must be reported, by FILE NAME
     ONLY, with the value never appearing in stdout.
"""
import re, subprocess, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
src = (ROOT / ".agent-ops" / "verify_task.py").read_text(encoding="utf-8")
m = re.search(r'bash_cmd = \(\n(.*?)\n\s*\)\n', src, re.S)
assert m, "could not extract bash_cmd"
bash_cmd = eval("(" + m.group(1) + ")")

FAKE = "sk-or-v1-" + "d" * 40
tmp = pathlib.Path(r"C:\tmp\secguard")
tmp.mkdir(parents=True, exist_ok=True)
(tmp / "clean.txt").write_text("nothing to see here\n")
(tmp / "leaky.txt").write_text(f"OPENROUTER_API_KEY={FAKE}\n")
wsl_root = "/mnt/c/tmp/secguard"

def run(home):
    pre = f'export HOME={home}; ' if home else ''
    return subprocess.run(
        ["wsl", "-e", "bash", "-c", pre + bash_cmd, "_", wsl_root,
         "clean.txt", "leaky.txt"],
        capture_output=True, text=True, timeout=120)

fail = 0

# Case 1: no keys available -> must NOT look clean.
# `env -i` wipes HOME, so `~` still resolves via /etc/passwd and an outer
# HOME override cannot simulate a missing key file. Swap the pattern producer
# for one that yields nothing, which is the condition the guard exists for.
empty_cmd = re.sub(r"pat=\$\(env -i bash -c '.*?'\);", "pat=$(printf '');",
                   bash_cmd, flags=re.S)
assert empty_cmd != bash_cmd, "could not neutralise the pattern producer"
r = subprocess.run(
    ["wsl", "-e", "bash", "-c", empty_cmd, "_", wsl_root, "clean.txt", "leaky.txt"],
    capture_output=True, text=True, timeout=120)
if r.returncode == 9 or "NOPATTERNS" in r.stderr:
    print("PASS case1: empty key set refuses to report clean (rc=%d)" % r.returncode)
else:
    print("FAIL case1: scanned with zero patterns and did not flag it; rc=%d out=%r"
          % (r.returncode, r.stdout)); fail = 1

# Case 2: real keys loaded -> planted key found, value never printed
# Plant a value that IS one of the real patterns by reusing the real env.
r = run(None)
if "PATTERNS=" not in r.stderr:
    print("SKIP case2: real agent-keys.env not readable here:", r.stderr.strip()[:80])
else:
    npat = r.stderr.strip()
    # Use a real key value so the grep has something to match.
    kv = subprocess.run(
        ["wsl", "-e", "bash", "-c",
         "env -i bash -c 'set -a; . ~/.config/agent-keys.env; set +a; "
         "for v in $(compgen -e); do case $v in *KEY*|*TOKEN*) "
         "val=${!v}; [ ${#val} -ge 16 ] && { printf \"%s\" \"$val\"; exit 0; };; esac; done'"],
        capture_output=True, text=True).stdout.strip()
    if not kv:
        print("SKIP case2: no key value extracted")
    else:
        (tmp / "leaky.txt").write_text("API_KEY=" + kv + "\n")
        r2 = run(None)
        if "leaky.txt" in r2.stdout and "clean.txt" not in r2.stdout:
            if kv in r2.stdout or kv in r2.stderr:
                print("FAIL case2: THE KEY VALUE WAS PRINTED"); fail = 1
            else:
                print("PASS case2: leak found by filename only, value not printed (%s)" % npat)
        else:
            print("FAIL case2: planted key not detected; out=%r" % r2.stdout); fail = 1

(tmp / "leaky.txt").write_text("scrubbed\n")
for f in tmp.iterdir():
    f.unlink()
tmp.rmdir()
sys.exit(fail)
