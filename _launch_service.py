"""Spawn the ground-station service with NO_PROXY in the environment."""
import os
import subprocess
import sys

env = os.environ.copy()
env["NO_PROXY"] = "localhost,127.0.0.1,192.168.4.1"
env["no_proxy"] = "localhost,127.0.0.1,192.168.4.1"
# Use an unbuffered Python so we see logs immediately.
import sys as _sys
preset = _sys.argv[1] if len(_sys.argv) > 1 else None
cmd = [
    sys.executable, "-u", "-m", "ground_station.service",
    "--wifi-host", "192.168.4.1",
    "--wifi-port", "14550",
    "--port", "8081",
    "--rtos-bridge",
    "--rtos-interval", "5",
    "--freshness-ttl", "10",
]
if preset:
    cmd.extend(["--preset", preset])
out_path = r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\ground_station\service_output.txt"
err_path = r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\ground_station\service_error.txt"
# truncate
open(out_path, "wb").close()
open(err_path, "wb").close()
out = open(out_path, "wb")
err = open(err_path, "wb")
proc = subprocess.Popen(cmd, cwd=r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex",
                        stdout=out, stderr=err, env=env,
                        creationflags=0x00000008)  # DETACHED_PROCESS
print(f"Started PID={proc.pid}, preset={preset!r}")

