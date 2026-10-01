from ground_station.service.fake_drone import FakeDrone
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.trajectory_pipeline import generate, Profile
from ground_station.platform.trajectory_upload import upload

drone = FakeDrone()
drone.sbus_live = True
client = WfbClient(drone.send)
client.set_hover_z(0.8)
client.arm()
client.idle()
client.takeoff()
for _ in range(100):
    client.heartbeat()
    drone.step(0.02)
    
profile = Profile(0.3, 0.5, 0.05, 0.8, 0.0)
pts = generate("circle", {"radius_m": 0.5}, profile)
print("upload result:", upload(pts, client))
print("traj_state:", drone.status()["traj_state"])
print("last_err:", drone.status()["last_err"])
print("traj_start:", client.traj_start())
print("prim_state:", drone.status()["prim_state"])
