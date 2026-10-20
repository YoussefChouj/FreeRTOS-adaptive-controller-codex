# Friction: "split-brain" C: vs D: — a false alarm, and the real one

**Trigger:** A full `bash tools/check.sh` reported six failures, with stack
traces rooted at `/mnt/d/Projects/UAV_lab/...` while the session's workspace
path was `C:\Users\Acer\Desktop\UAV_lab\...`. Read quickly, that looks like a
cache holding hardcoded absolute paths from an old drive, and the next agent
will spend a day "fixing" `exe_cache` that was never broken.

**It is not.** `C:\Users\Acer\Desktop\UAV_lab` is a Windows **Junction** to
`D:\Projects\UAV_lab`. One tree, two paths:

```powershell
Get-Item "C:\Users\Acer\Desktop\UAV_lab" | Select LinkType, Target
#   LinkType : Junction
#   Target   : D:\Projects\UAV_lab
```

`bash -c pwd` from either path prints `/mnt/d/Projects/...`; `git rev-parse
--show-toplevel` prints `D:/projects/...`; file tools print `c:\Users\Acer\...`.
All three are the same bytes. `tools/exe_cache.py` is already repo-relative
(`REPO = Path(__file__).resolve().parents[1]`) and was never at fault.

**Token/time cost of the false alarm:** the diagnosis pass that established
this cost ~4 calls and ~6k tokens; it is written down so the next agent skips it.

**The real friction:** the gate reported six **failures** for what is really
one fact — this machine's bash environment has no `gcc`, no `clang-tidy`, and
no `pytest` module for its `python3`. Each missing tool printed a traceback or
a `FAIL`, which reads as "the code is broken" rather than "this machine cannot
run that step", and invites exactly the wrong diagnosis.

**Mechanical cure:** a missing tool is now a **SKIP**, not a FAIL. `rc 77` is
already `tools/check.sh`'s convention for "this input cannot exist here"
(`arm-none-eabi-gcc` used it). `host-tests`, `float`, `clang-tidy`,
`sim/sil/build.py` and `API/tests/run_mrac_equiv.py` now return 77 with a
one-line reason, so a full run on a bare WSL box reports `CHECK PASS` with the
tool-dependent steps named as skipped, instead of six red herrings.

**How to check the toolchain before blaming the code:**
`bash -c 'command -v gcc clang-tidy; python3 -c "import pytest"'`.
(Windows `python` has pytest 9.0.3; the WSL `python3` does not. That split is
real, and it is about interpreters, not about drive letters.)

**Cure status:** done — rc-77 SKIPs and this note (2026-10-20).