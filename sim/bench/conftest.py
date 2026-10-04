# c_ref/test_equiv.py is a script, not a pytest module: run it as `python sim/bench/c_ref/test_equiv.py`.
# Its test_l1/test_mrac_s6 take plain arguments (no fixtures), and it loads c_ref.so through ctypes at import,
# which raises when the library is missing or built for another platform (a 32-bit DLL gives WinError 193 on
# 64-bit Python). Either way collection of the whole tree would abort, so pytest never collects it.
collect_ignore = ["c_ref/test_equiv.py"]
