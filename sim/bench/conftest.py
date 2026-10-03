import os

# c_ref/test_equiv.py raises at import when the C reference library is not compiled,
# which aborts collection of the whole sim/bench tree. Skip it until c_ref.so exists.
_HERE = os.path.dirname(os.path.abspath(__file__))
collect_ignore = [] if os.path.exists(os.path.join(_HERE, "c_ref", "c_ref.so")) else ["c_ref/test_equiv.py"]
