import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="shell script test needs POSIX bash")
def test_vps_bridge():
    script = Path(__file__).parent / "test_vps_bridge.sh"
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert res.returncode == 0, f"Test failed with stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
