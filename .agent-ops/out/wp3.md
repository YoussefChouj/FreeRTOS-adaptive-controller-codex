STATUS: done

files changed:
  ground_station/analysis/flightlab/plugins/spectrum.py — SpectrumPlugin (order 60): rate loop PSD peaks, band power, RPM peaks, spectrum.png
  ground_station/analysis/flightlab/plugins/mrac.py — MracPlugin (order 70): mode_frac, airborne/steady stats, weight metrics, mrac_weights.png, mrac_uad.png
  ground_station/analysis/flightlab/tests/test_spectrum.py — unit tests S1-S6 for SpectrumPlugin and schema validation
  ground_station/analysis/flightlab/tests/test_mrac.py — unit tests M1-M9 for MracPlugin and schema validation

verification:
  python3 -m py_compile ground_station/analysis/flightlab/plugins/spectrum.py → exit 0
  python3 -m py_compile ground_station/analysis/flightlab/plugins/mrac.py → exit 0
  python3 -m py_compile ground_station/analysis/flightlab/tests/test_spectrum.py → exit 0
  python3 -m py_compile ground_station/analysis/flightlab/tests/test_mrac.py → exit 0
  python3 -m pytest ground_station/analysis/flightlab/tests/test_spectrum.py -v → 6 passed, 0 failed (exit 0)
  python3 -m pytest ground_station/analysis/flightlab/tests/test_mrac.py -v → 9 passed, 0 failed (exit 0)
  python3 -m pytest ground_station/analysis/flightlab/tests -q → 84 passed, 0 failed (exit 0)

open questions / risks:
  - Real flight logs with intermittent NaNs rely on _fill() linear interpolation; intervals with fewer than nperseg finite samples return None as specified.

SUBSTITUTIONS: none
