import re

with open("ground_station/analysis/tests/test_mrac_variants_host.py", "r") as f:
    text = f.read()

# The failure is 'assert [1, 0, 0, 0, 0, 0, ...] == [0, 0, 0, 0, 0, 0, ...]'
# Meaning "4:0:1" gave 1 (accepted) instead of 0 (rejected).
# Why did it get accepted?
# "4:0:1" means axis 4.
