import sys
import re

with open("API/tests/test_mrac_sigma_prior.c", "r") as f:
    content = f.read()

stubs = """
_imu_st imu_data = {0};
float MRAC_VectorNormSquare(const float *v, int n) { return 0.0f; }
"""

content = content.replace('#include "mrac.h"\n', '#include "mrac.h"\n' + stubs)

# Replace the sil_gate line in the header with the gcc command
pattern = r"Built standalone by sil_gate/tests/test_mrac_sigma_prior\.py using gcc\."
replacement = r"gcc -std=c99 -Wall -DMRAC_ENABLE_SIGMA_PRIOR -I../tests/stubs -I.. test_mrac_sigma_prior.c mrac.c -o t -lm && ./t"
content = re.sub(pattern, replacement, content)

with open("API/tests/test_mrac_sigma_prior.c", "w") as f:
    f.write(content)
