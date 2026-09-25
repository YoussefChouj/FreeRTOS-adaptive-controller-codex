import subprocess
from pathlib import Path


def test_vps_bridge():
    script = Path(__file__).parent / "test_vps_bridge.sh"
    res = subprocess.run([str(script)], capture_output=True, text=True)
    assert res.returncode == 0, f"Test failed with stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
