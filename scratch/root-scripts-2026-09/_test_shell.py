"""Minimal test: just start shell and check if port opens."""
import sys, time, threading
sys.path.insert(0, r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex")

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.platform.shell import start_shell
from ground_station.service.core import GroundStationService

print("Building service stack...", flush=True)
bridge = WifiBridge(wifi_host="192.168.4.1", wifi_port=14550)
service = GroundStationService(bridge=bridge)
service.start(auto_subscribe=False)

print("Starting shell...", flush=True)
api = start_shell(service, port=8081)
print(f"Shell started on {api.address}, waiting...", flush=True)

# Wait 5 seconds then exit
time.sleep(5)
print("Done.", flush=True)
