from pathlib import Path
from ground_station.service.tests.test_runner import create_deps, FakeClock, FakeDrone, YAML_PATH
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_runner import run_campaign
tmp_path = Path("/tmp/test_j")
tmp_path.mkdir(parents=True, exist_ok=True)
campaign_yaml = tmp_path / "camp.yaml"
campaign_yaml.write_text(Path(YAML_PATH).read_text())

drone = FakeDrone()
drone.sbus_live = True
client = WfbClient(drone.send)
clock = FakeClock()
deps = create_deps(drone, client, clock)
deps.flight_timeout_s = 0.01

orig_land = client.land
def mock_land():
    orig_land()
    # Instantly land to avoid landing timeout
    drone._prim_state = 0
client.land = mock_land

report = run_campaign(str(campaign_yaml), deps)
print(f"Status: {report.status}, Reason: {report.reason}")
print(f"Flights: {len(report.flights)}")
for i, f in enumerate(report.flights):
    print(f"Flight {i}: abort_level={f.abort_level}, abort_reason={f.abort_reason}")
