"""Start an offline ApiServer, run the node panel harness against it."""
import subprocess
import sys
from pathlib import Path

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore

root = Path(__file__).resolve().parents[2]

schema = StreamSchema(1, 1, 4,
                      (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
service = GroundStationService(store=SessionStore(), schemas=[schema], source="sim")
service.start()
api = ApiServer(service)
api.start()
try:
    proc = subprocess.run(
        [r"C:\Program Files\nodejs\node.exe",
         str(root / ".agent-ops/tmp/panel_harness.js"),
         str(api.address[1]),
         str(root / "docs/dashboard-platform/shell/plugins/slot-manager-panel.js")],
        capture_output=True, text=True, timeout=120,
    )
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)
finally:
    api.stop()
