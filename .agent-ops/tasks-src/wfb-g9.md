<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including all existing tests,
   conftest.py, model.py, registry.py, pipeline.py, config/*.yaml and schema/*.json.
2. Make every assertion pass by fixing implementation code. Keep existing tests and assertions exactly
   as they are. [ImpossibleBench, arXiv 2510.20270]
3. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
4. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
6. Catch only specific exceptions (ValueError, KeyError, ...) and re-raise or record them in "warnings".
   Bare `except:` and `except Exception: pass` are forbidden. [general, arXiv 2603.28592]
7. Compare floats with `np.isclose`/`pytest.approx`; test NaN with `np.isnan`/`math.isnan`.
8. State array axes explicitly (`axis=0`); write interval ends as half-open [t0, t1) unless told otherwise;
   check lengths before slicing. [general, arXiv 2511.18782]
9. Keep every function signature, name and return shape exactly as the brief specifies.
10. Import only: python stdlib, numpy, scipy, pandas (loaders only), matplotlib, jsonschema, yaml.
    Use only functions you have seen documented; when unsure, check with `python -c "help(...)"`.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest `.agent-ops/out/<id>.md` BEFORE printing DONE; the supervisor rejects a run whose
    digest file is missing, regardless of exit code.
13. Research tasks: cite canonical URLs only (no vertexaisearch / google.com/url redirects); quote <= 25 words.
14. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Repository: STM32F407 flight-controller firmware plus a Python ground station (package `ground_station`).
This is "task G9" of workflow B (autonomous tuning flights): the phone-camera recorder. An Android phone
on USB films each flight; the video is position truth for the offline analysis. The recorder starts at
takeoff and stops at landing. It must never block or fail a flight: when the tools or the phone are
missing it does nothing and says so once.
Recording path (supervisor ruling): `scrcpy` with the camera as video source, recording straight to a file
on the PC. No pull step, PC clock timestamps. `adb` is used only to check that a phone is attached.
Python 3.10+, standard library only (subprocess, shutil, json, logging, time, pathlib, dataclasses).
Type hints on every public function. No global mutable state. No real process is started in the tests:
every external call goes through an injected callable.
Read first: docs/workflow-b/interfaces.md section 4 (the `service/adb_recorder.py` row).
</context>

<task>
Implement the recorder module and its tests.

ALLOW-LIST (create only these two files, nothing else anywhere):
- ground_station/service/adb_recorder.py
- ground_station/service/tests/test_adb_recorder.py

Interface (names and signatures exact):
- CAMERA_ARGS: tuple[str, ...] = ("--video-source=camera", "--camera-facing=back", "--no-audio",
  "--no-playback")    # PROPOSED, comment it so
- class AdbRecorder:
    __init__(self, adb: str = "adb", scrcpy: str = "scrcpy", camera_args: tuple[str, ...] = CAMERA_ARGS,
             popen=subprocess.Popen, run=subprocess.run, which=shutil.which, clock=time.time,
             stop_timeout_s: float = 5.0)
    available(self) -> bool
      True only when `which(adb)` and `which(scrcpy)` both return a path and
      `run([adb, "get-state"], capture_output=True, text=True, timeout=3.0)` returns returncode 0 with
      stdout.strip() == "device". Any exception from `which` or `run` (OSError, subprocess.SubprocessError)
      gives False. Never raises.
    recording (read-only property) -> bool
    start(self, session_dir: str | os.PathLike) -> bool
      - Already recording: raise RuntimeError (programming error, not a device fault).
      - Not available: log one WARNING through logging.getLogger(__name__) the first time only (later
        calls stay silent), start nothing, create nothing on disk, return False.
      - Otherwise: create session_dir (parents, exist_ok); stamp = time.strftime("%Y%m%d_%H%M%S",
        time.localtime(t0)) with t0 = clock(); video path = session_dir / f"camera_{stamp}.mkv";
        command = [scrcpy, *camera_args, f"--record={video}"]; process =
        popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).
        Write the sidecar session_dir / f"camera_{stamp}.json" (UTF-8, indent 2, sorted keys) with
        {"file": video.name, "t_start_host_s": t0, "command": command}. Return True.
      - `popen` raising OSError: log a WARNING, leave no sidecar, stay not recording, return False.
    stop(self) -> pathlib.Path | None
      - Not recording: return None (no warning).
      - Otherwise: process.terminate(); process.wait(timeout=stop_timeout_s); on
        subprocess.TimeoutExpired call process.kill() then process.wait(). An OSError from terminate or
        kill is logged as WARNING and does not propagate. Add "t_stop_host_s" (clock()) and "returncode"
        (process.returncode, may be None) to the sidecar, keeping the three start keys. The recorder is
        not recording afterwards, whatever happened. Return the video path when the file exists with a
        size above 0; otherwise log a WARNING and return None.
    After stop() a new start() works and produces new file names (the stamp comes from the clock).
- The module is import-safe without adb or scrcpy installed and starts nothing at import.

Required tests (plain pytest functions; tmp_path; caplog for the warnings; fakes written in the test file:
a FakeProc class with terminate/wait/kill/returncode that records the calls, a fake popen that records the
command and keyword arguments and can create the video file, a fake run returning an object with returncode
and stdout, a fake which, a fake clock returning fixed values):
1. available(): True with both tools and state "device"; False when adb is missing; False when scrcpy is
   missing; False for state "unauthorized"; False for returncode 1; False when run raises
   subprocess.TimeoutExpired; False when run raises OSError.
2. start() when not available returns False, calls popen zero times, creates nothing in tmp_path, and
   logs exactly one WARNING across two calls.
3. start() happy path: returns True; recording is True; the command equals
   [scrcpy, *CAMERA_ARGS, "--record=<video path>"]; the three keyword arguments are subprocess.DEVNULL;
   the video name is camera_<stamp>.mkv for the fixed clock value; the sidecar holds exactly the three
   start keys with the right values.
4. start() creates a missing nested session_dir.
5. start() twice without stop raises RuntimeError and starts one process only.
6. popen raising OSError: start() returns False, recording is False, no sidecar, one WARNING.
7. stop() happy path: terminate called once, wait called with timeout == stop_timeout_s, kill not called;
   returns the video path; the sidecar now has five keys with t_stop_host_s from the clock and the
   returncode; recording is False.
8. stop() when wait raises subprocess.TimeoutExpired the first time: kill called once, wait called twice,
   the path is still returned when the file exists.
9. stop() with a missing video file, and with an empty (0 byte) video file: returns None and logs a WARNING;
   recording is False.
10. stop() without start returns None and logs nothing.
11. terminate raising OSError: stop() does not raise, recording is False.
12. start, stop, start with a later clock value: two different video names, two sidecars.

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/service/tests/test_adb_recorder.py -q

Digest: write .agent-ops/out/wfb-g9.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
