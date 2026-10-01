<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never touch `OBJ/`, never run Keil.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python edit run `python -m py_compile <file>`. Fix before going on.
4. Run every command in the foreground and paste its verbatim output (last 3 lines at least) into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Python 3.10+, standard library + numpy (+ pandas only through the existing loader).
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Write the digest `.agent-ops/out/wp8-r3.md` BEFORE printing DONE.
9. Output: no preamble, no summary prose.
</guardrails>

<context>
FIX round for your own work (HEAD = your wp8-r2 commit; `.agent-ops/tasks-src/wp8-r1.md` and `wp8-r2.md` still
apply). The gate on the laptop (real logs present) now runs `test_replay_real_logs`. Verbatim:

```
              csv = meta_path.parent / f"{stem}.slot{i}.csv"
              csv_paths.append(csv)
              if not csv.exists():
  >               raise LoadError(f"{csv.name}: missing")
  E               ground_station.analysis.flightlab.loaders.LoadError: tmpg5lf292p.slot0.csv: missing
  ground_station\analysis\flightlab\loaders\vofa.py:50: LoadError
  FAILED ground_station/analysis/tests/test_ekf_of_model.py::test_replay_real_logs
  1 failed, 3 passed, 2 skipped in 16.55s
```
Cause (from `ground_station/analysis/flightlab/loaders/vofa.py:39-50`): the loader takes `stem` from the meta FILE
NAME (`<stem>.meta.json`) and opens `<meta dir>/<stem>.slot<i>.csv` where `i` is the slot's POSITION in the meta
slot list (0, 1, 2, ...), not the original slot number. Your temp meta has a random stem in the temp dir, and after
dropping slot 0 the remaining slots are renumbered 0..2. The meta has either `meta["preset"]["slots"]` (list) or
`meta["slots"]` (dict keyed "0","1",...); handle both.
On the laptop the 2 skipped tests are the golden C-vs-Python tests (the local gcc cannot build a loadable library).
Also `ground_station/analysis/ekf_of_replay.py:81` still holds a scratch comment ("No, wait: ...").
</context>

<allow-list>
ground_station/analysis/ekf_of_replay.py
ground_station/analysis/tests/test_ekf_of_model.py
.agent-ops/out/wp8-r3.md         (digest)
</allow-list>

<spec>
1. Log loading: first call `load_vofa(meta_path)` on the original meta. Only on `LoadError` fall back to:
   a `tempfile.TemporaryDirectory()`; write `<tmp>/<orig stem>.meta.json` whose slot list keeps the original slots
   1, 2, 3 (same order, both meta shapes); copy `<orig stem>.slot1.csv`, `.slot2.csv`, `.slot3.csv` to
   `<tmp>/<orig stem>.slot0.csv`, `.slot1.csv`, `.slot2.csv` (`shutil.copyfile`); `load_vofa` that meta inside the
   `with` block. If the fallback also fails, raise `LoadError(f"{log name}: {err}")`.
   Add a unit test that builds a tiny fake log in tmp_path (meta + header-only slot0 + small slot1-3 CSVs with the
   loader's required columns `t_src_ms, t_host_s, seq` plus the signal columns the replay needs) and asserts the
   replay's loader returns the slot1-3 signals. Read the loader fully to get the CSV and meta format right.
2. Remove scratch/thinking comments anywhere in ekf_of_replay.py (e.g. line 81); comments state facts only.
3. `test_replay_real_logs`: also write the full captured replay stdout to
   `os.path.join(tempfile.gettempdir(), "wp8_replay_last.txt")` before asserting (always, pass or fail).
   Keep the `DEFAULTS_MATCH yes` assertion and its rich message.
4. `gcc_lib` fixture: before every `pytest.skip`, write the skip reason to
   `os.path.join(tempfile.gettempdir(), "wp8_gcc_skip.txt")`.
5. Do not change API/, TASK/, ekf_of_model.py or the defaults.
</spec>

<tests>
`PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_ekf_of_model.py ground_station/comm -q`
(VPS: real-log test SKIPS, golden PASSES, new fake-log loader test PASSES);
`ruff check ground_station/analysis/ekf_of_replay.py ground_station/analysis/tests/test_ekf_of_model.py`.
</tests>

<digest>
`.agent-ops/out/wp8-r3.md`, at most 20 lines: files changed, each command + its last 3 output lines verbatim,
deviations, open risks.
</digest>
