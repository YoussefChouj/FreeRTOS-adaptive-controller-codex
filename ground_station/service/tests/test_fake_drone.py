"""Tests for FakeDrone software simulation (Workflow B, Task G3)."""

from __future__ import annotations

import math
import pytest

from ground_station.platform.transactions import Outcome
from ground_station.platform.wfb_commands import (
    CMD_PRIM,
    CMD_TRAJ,
    PrimIdx,
    TrajIdx,
    WfbClient,
    encode,
)
from ground_station.service.fake_drone import (
    FakeDrone,
    FakeParams,
    PrimState,
    TrajState,
    Trip,
    WfbErr,
)
from ground_station.service.trajectory_pipeline import (
    Profile,
    TrajLimits,
    TrajPoint,
    crc32,
    generate,
)


def upload(client: WfbClient, points: list[TrajPoint]) -> bool:
    """Helper to upload a trajectory and commit it."""
    if not client.traj_begin(len(points)):
        return False
    for pt in points:
        for val in (pt.x, pt.y, pt.z, pt.yaw_deg, pt.t):
            if not client.traj_append(val):
                return False
    crc = crc32(points)
    return client.traj_commit(crc)


def run(drone: FakeDrone, client: WfbClient, seconds: float, heartbeat: bool = True) -> None:
    """Helper to step the simulation at dt = 0.02 and send heartbeats every 0.2 s."""
    dt = 0.02
    steps = int(math.ceil(seconds / dt))
    for step_i in range(steps):
        if heartbeat and step_i % 10 == 0:
            client.heartbeat()
        drone.step(dt)


def test_1_takeoff_rejections_and_climb() -> None:
    """TAKEOFF is rejected when not armed, when armed without idle, and with sbus_live False;

    after arm + idle it is applied, gs_flight_active becomes 1, and the drone reaches
    primitive state HOVER at (0, 0, hover_z) within 0.15 m.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)

    # 1. Rejected when not armed
    assert client.takeoff() is False
    assert drone.status()["last_err"] != 0

    # 2. Rejected when armed without idle
    assert client.arm() is True
    assert client.takeoff() is False
    assert drone.status()["last_err"] != 0

    # 3. Rejected with sbus_live False
    drone2 = FakeDrone()
    client2 = WfbClient(drone2.send)
    assert client2.arm() is True
    assert client2.idle() is True
    drone2.sbus_live = False
    assert client2.takeoff() is False
    assert drone2.status()["last_err"] != 0

    # 4. Accepted after arm + idle with sbus_live True
    drone2.sbus_live = True
    assert client2.takeoff() is True
    assert drone2.status()["gs_flight_active"] == 1.0

    # Drone climbs and reaches HOVER within 0.15 m of (0, 0, hover_z)
    run(drone2, client2, 3.0, heartbeat=True)
    assert drone2.status()["prim_state"] == PrimState.HOVER
    dist = math.dist(drone2.position, (0.0, 0.0, 0.5))
    assert dist <= 0.15


def test_2_set_hover_z() -> None:
    """SET_HOVER_Z: 0.7 applied on the ground and used by the next takeoff;

    0.2, 1.3 and NaN rejected; rejected while airborne.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)

    # 0.7 applied on the ground
    assert client.set_hover_z(0.7) is True
    assert drone.status()["hover_z"] == pytest.approx(0.7)

    # Used by the next takeoff
    client.arm()
    client.idle()
    assert client.takeoff() is True
    run(drone, client, 3.5, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER
    assert drone.position[2] == pytest.approx(0.7, abs=0.02)

    # Rejected while airborne
    assert client.set_hover_z(0.6) is False
    assert drone.status()["last_err"] == WfbErr.STATE

    # 0.2, 1.41, and NaN rejected on the ground
    drone_ground = FakeDrone()
    client_ground = WfbClient(drone_ground.send)
    assert client_ground.set_hover_z(0.2) is False
    assert drone_ground.status()["last_err"] == WfbErr.RANGE

    assert client_ground.set_hover_z(1.41) is False
    assert drone_ground.status()["last_err"] == WfbErr.RANGE

    assert client_ground.set_hover_z(float("nan")) is False
    assert drone_ground.status()["last_err"] == WfbErr.RANGE


def test_3_upload_trajectory() -> None:
    """Upload of generate('circle', {'radius_m': 0.3}, Profile(0.3, 0.5, 0.02, 0.5)):

    state READY, traj_n == N, traj_rx == 5N, traj_crc_hi and traj_crc_lo equal
    the halves of trajectory_pipeline.crc32.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)
    profile = Profile(0.3, 0.5, 0.02, 0.5)
    points = generate("circle", {"radius_m": 0.3}, profile)
    n = len(points)

    assert upload(client, points) is True
    st = drone.status()
    assert st["traj_state"] == TrajState.READY
    assert st["traj_n"] == n
    assert st["traj_rx"] == 5 * n
    expected_crc = crc32(points)
    assert st["traj_crc_hi"] == (expected_crc >> 16) & 0xFFFF
    assert st["traj_crc_lo"] == expected_crc & 0xFFFF


def test_4_upload_rejections() -> None:
    """One test per upload rejection, asserting returned False, resulting state and last_err."""
    # 1. BEGIN with 1
    d1 = FakeDrone()
    c1 = WfbClient(d1.send)
    assert c1.traj_begin(1) is False
    assert d1.status()["traj_state"] == TrajState.EMPTY
    assert d1.status()["last_err"] == WfbErr.RANGE

    # 2. BEGIN with 601
    d2 = FakeDrone()
    c2 = WfbClient(d2.send)
    assert c2.traj_begin(601) is False
    assert d2.status()["traj_state"] == TrajState.EMPTY
    assert d2.status()["last_err"] == WfbErr.RANGE

    # 3. BEGIN with 2.5 (raw frame via encode)
    d3 = FakeDrone()
    res3 = d3.send(encode(CMD_TRAJ, TrajIdx.BEGIN, 2.5, 1))
    assert res3 == int(Outcome.REJECTED)
    assert d3.status()["traj_state"] == TrajState.EMPTY
    assert d3.status()["last_err"] == WfbErr.RANGE

    # 4. APPEND outside LOADING
    d4 = FakeDrone()
    c4 = WfbClient(d4.send)
    assert c4.traj_append(0.0) is False
    assert d4.status()["traj_state"] == TrajState.EMPTY
    assert d4.status()["last_err"] == WfbErr.STATE

    # 5. (5N+1)-th APPEND
    d5 = FakeDrone()
    c5 = WfbClient(d5.send)
    assert c5.traj_begin(2) is True
    for _ in range(10):
        assert c5.traj_append(0.0) is True
    assert c5.traj_append(0.0) is False
    assert d5.status()["traj_state"] == TrajState.LOADING
    assert d5.status()["last_err"] == WfbErr.COUNT

    # 6. APPEND NaN
    d6 = FakeDrone()
    c6 = WfbClient(d6.send)
    assert c6.traj_begin(2) is True
    assert c6.traj_append(float("nan")) is False
    assert d6.status()["traj_state"] == TrajState.LOADING
    assert d6.status()["last_err"] == WfbErr.RANGE

    # 7. CRC_HI 65536 (raw frame via encode)
    d7 = FakeDrone()
    c7 = WfbClient(d7.send)
    assert c7.traj_begin(2) is True
    res7 = d7.send(encode(CMD_TRAJ, TrajIdx.CRC_HI, 65536.0, 2))
    assert res7 == int(Outcome.REJECTED)
    assert d7.status()["traj_state"] == TrajState.LOADING
    assert d7.status()["last_err"] == WfbErr.RANGE

    # 8. COMMIT before CRC_HI
    d8 = FakeDrone()
    c8 = WfbClient(d8.send)
    assert c8.traj_begin(2) is True
    for _ in range(10):
        c8.traj_append(0.0)
    res8 = d8.send(encode(CMD_TRAJ, TrajIdx.COMMIT, 0.0, 3))
    assert res8 == int(Outcome.REJECTED)
    assert d8.status()["traj_state"] == TrajState.EMPTY
    assert d8.status()["last_err"] == WfbErr.STATE

    # 9. COMMIT with too few floats
    d9 = FakeDrone()
    c9 = WfbClient(d9.send)
    assert c9.traj_begin(2) is True
    c9.traj_append(0.0)
    d9.send(encode(CMD_TRAJ, TrajIdx.CRC_HI, 0.0, 4))
    res9 = d9.send(encode(CMD_TRAJ, TrajIdx.COMMIT, 0.0, 5))
    assert res9 == int(Outcome.REJECTED)
    assert d9.status()["traj_state"] == TrajState.EMPTY
    assert d9.status()["last_err"] == WfbErr.COUNT

    # 10. COMMIT with wrong CRC
    d10 = FakeDrone()
    c10 = WfbClient(d10.send)
    pts10 = [TrajPoint(0.0, 0.0, 0.5, 0.0, 0.0), TrajPoint(0.0, 0.0, 0.5, 0.0, 1.0)]
    assert c10.traj_begin(2) is True
    for pt in pts10:
        for val in (pt.x, pt.y, pt.z, pt.yaw_deg, pt.t):
            c10.traj_append(val)
    assert c10.traj_commit(0xDEADBEEF) is False
    assert d10.status()["traj_state"] == TrajState.EMPTY
    assert d10.status()["last_err"] == WfbErr.CRC

    # 11. Hand-built list failing TIME (t[0] != 0)
    d_time = FakeDrone()
    c_time = WfbClient(d_time.send)
    pts_time = [TrajPoint(0.0, 0.0, 0.5, 0.0, 1.0), TrajPoint(0.0, 0.0, 0.5, 0.0, 2.0)]
    assert upload(c_time, pts_time) is False
    assert d_time.status()["traj_state"] == TrajState.EMPTY
    assert d_time.status()["last_err"] == WfbErr.TIME

    # 12. Hand-built list failing BOUNDS (|x| > 0.8)
    d_bounds = FakeDrone()
    c_bounds = WfbClient(d_bounds.send)
    pts_bounds = [
        TrajPoint(0.0, 0.0, 0.5, 0.0, 0.0),
        TrajPoint(2.0, 0.0, 0.5, 0.0, 1.0),
        TrajPoint(0.0, 0.0, 0.5, 0.0, 2.0),
    ]
    assert upload(c_bounds, pts_bounds) is False
    assert d_bounds.status()["traj_state"] == TrajState.EMPTY
    assert d_bounds.status()["last_err"] == WfbErr.BOUNDS

    # 13. Hand-built list failing ENDPOINT
    d_endpoint = FakeDrone()
    c_endpoint = WfbClient(d_endpoint.send)
    pts_endpoint = [TrajPoint(0.0, 0.0, 0.8, 0.0, 0.0), TrajPoint(0.0, 0.0, 0.8, 0.0, 1.0)]
    assert upload(c_endpoint, pts_endpoint) is False
    assert d_endpoint.status()["traj_state"] == TrajState.EMPTY
    assert d_endpoint.status()["last_err"] == WfbErr.ENDPOINT

    # 14. Hand-built list failing SPEED
    d_speed = FakeDrone()
    c_speed = WfbClient(d_speed.send)
    pts_speed = [
        TrajPoint(0.0, 0.0, 0.5, 0.0, 0.0),
        TrajPoint(0.5, 0.0, 0.5, 0.0, 0.1),
        TrajPoint(0.0, 0.0, 0.5, 0.0, 1.0),
    ]
    assert upload(c_speed, pts_speed) is False
    assert d_speed.status()["traj_state"] == TrajState.EMPTY
    assert d_speed.status()["last_err"] == WfbErr.SPEED


def test_5_traj_start_execution_and_completion() -> None:
    """START is rejected on the ground and while climbing; applied in HOVER.

    During execution the position stays within 0.05 m of the interpolated trajectory;
    it ends with trajectory state DONE, primitive state HOVER, position within 0.15 m
    of the hover point. BEGIN and CLEAR are rejected while EXECUTING.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)
    profile = Profile(0.3, 0.5, 0.02, 0.5)
    points = generate("circle", {"radius_m": 0.3}, profile)
    assert upload(client, points) is True

    # START rejected on the ground
    assert client.traj_start() is False
    assert drone.status()["last_err"] == WfbErr.STATE

    # Takeoff
    client.arm()
    client.idle()
    client.takeoff()
    # While climbing
    assert client.traj_start() is False
    assert drone.status()["last_err"] == WfbErr.STATE

    # Wait until HOVER
    run(drone, client, 2.5, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER

    # START applied in HOVER
    assert client.traj_start() is True
    assert drone.status()["traj_state"] == TrajState.EXECUTING
    assert drone.status()["prim_state"] == PrimState.TRAJ

    # BEGIN and CLEAR rejected while EXECUTING
    assert client.traj_begin(10) is False
    assert drone.status()["last_err"] == WfbErr.STATE
    assert client.traj_clear() is False
    assert drone.status()["last_err"] == WfbErr.STATE

    # Step through execution and verify position stays within 0.05 m of interpolated trajectory
    dt = 0.02
    total_time = points[-1].t
    elapsed = 0.0
    while elapsed < total_time + 0.2:
        t_now = drone.status()["traj_t"]
        _, pt_expected = drone._sample_traj(t_now)
        dist = math.dist(drone.position, (pt_expected.x, pt_expected.y, pt_expected.z))
        assert dist <= 0.05

        drone.step(dt)
        elapsed += dt
        if int(round(elapsed / dt)) % 10 == 0:
            client.heartbeat()

    assert drone.status()["traj_state"] == TrajState.DONE
    assert drone.status()["prim_state"] == PrimState.HOVER
    dist_hover = math.dist(drone.position, (0.0, 0.0, 0.5))
    assert dist_hover <= 0.15


def test_6_traj_stop() -> None:
    """STOP in mid-trajectory: state READY, and the drone returns to the hover point and reaches HOVER.

    STOP when not executing is rejected.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)
    profile = Profile(0.3, 0.5, 0.02, 0.5)
    points = generate("circle", {"radius_m": 0.3}, profile)
    upload(client, points)

    # STOP when not executing is rejected
    assert client.traj_stop() is False
    assert drone.status()["last_err"] == WfbErr.STATE

    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER

    client.traj_start()
    run(drone, client, 1.0, heartbeat=True)
    assert drone.status()["traj_state"] == TrajState.EXECUTING

    # STOP in mid-trajectory
    assert client.traj_stop() is True
    assert drone.status()["traj_state"] == TrajState.READY

    # Returns to hover point and reaches HOVER
    run(drone, client, 6.0, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER
    dist_hover = math.dist(drone.position, (0.0, 0.0, 0.5))
    assert dist_hover <= 0.15


def test_7_land_behavior() -> None:
    """LAND from HOVER: descends, touches down, ends disarmed with primitive state IDLE

    and gs_flight_active 0. LAND in mid-trajectory away from the hover point: the horizontal
    distance from the origin is below the settle radius at the first step in which z decreases.
    LAND on the ground is rejected.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)

    # LAND on ground rejected
    assert client.land() is False
    assert drone.status()["last_err"] == WfbErr.STATE

    # Takeoff to HOVER
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER

    # LAND from HOVER
    assert client.land() is True
    run(drone, client, 5.0, heartbeat=True)
    assert drone.position[2] == 0.0
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.status()["gs_flight_active"] == 0.0
    assert not drone.armed

    # LAND in mid-trajectory away from the hover point
    drone2 = FakeDrone()
    client2 = WfbClient(drone2.send)
    profile = Profile(0.3, 0.5, 0.02, 0.5)
    points = generate("circle", {"radius_m": 0.3}, profile)
    upload(client2, points)
    client2.arm()
    client2.idle()
    client2.takeoff()
    run(drone2, client2, 2.5, heartbeat=True)
    client2.traj_start()
    run(drone2, client2, 2.0, heartbeat=True)

    # Away from hover point
    assert math.hypot(drone2.position[0], drone2.position[1]) > 0.1
    assert client2.land() is True

    prev_z = drone2.position[2]
    first_dec_h_dist: float | None = None
    dt = 0.02
    for step_i in range(500):
        if step_i % 10 == 0:
            client2.heartbeat()
        drone2.step(dt)
        curr_z = drone2.position[2]
        if curr_z < prev_z and first_dec_h_dist is None:
            first_dec_h_dist = math.hypot(drone2.position[0], drone2.position[1])
            break
        prev_z = curr_z

    assert first_dec_h_dist is not None
    assert first_dec_h_dist <= drone2.params.settle_radius_m


def test_8_heartbeat_timeout() -> None:
    """With heartbeats every 0.2 s, 5 s of hover trips nothing;

    without heartbeats safety_trip becomes HEARTBEAT after the timeout and the drone lands by itself.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.HOVER

    # 5 s with heartbeats
    run(drone, client, 5.0, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.NONE
    assert drone.status()["prim_state"] == PrimState.HOVER

    # Step without heartbeats until past 1.0 s timeout
    run(drone, client, 1.1, heartbeat=False)
    assert drone.status()["safety_trip"] == Trip.HEARTBEAT

    # Lands by itself
    run(drone, client, 10.0, heartbeat=False)
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.position[2] == 0.0


def test_9_airborne_cap() -> None:
    """Hovering with heartbeats past the cap gives safety_trip AIRBORNE_CAP and a landing."""
    drone = FakeDrone(params=FakeParams(airborne_cap_s=3.0))
    client = WfbClient(drone.send)
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.NONE

    # Cross 3.0 s airborne cap
    run(drone, client, 1.0, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.AIRBORNE_CAP

    # Lands
    run(drone, client, 8.0, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.position[2] == 0.0


def test_10_low_voltage() -> None:
    """vbat_v just under the threshold for the hold time gives LOW_V and a landing;

    a dip shorter than the hold time trips nothing.
    """
    drone = FakeDrone()
    client = WfbClient(drone.send)
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)

    # Dip under 14.0 V for 2 s (< 3 s hold time)
    drone.vbat_v = 13.9
    run(drone, client, 2.0, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.NONE

    # Recover
    drone.vbat_v = 16.0
    run(drone, client, 0.5, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.NONE

    # Dip under 14.0 V for >= 3 s hold time
    drone.vbat_v = 13.9
    run(drone, client, 3.1, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.LOW_V

    # Lands
    run(drone, client, 8.0, heartbeat=True)
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.position[2] == 0.0


def test_11_tilt() -> None:
    """roll_deg over the threshold for the hold time gives TILT and motors off at once."""
    drone = FakeDrone()
    client = WfbClient(drone.send)
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)

    drone.roll_deg = 65.0
    run(drone, client, 0.25, heartbeat=True)
    assert drone.status()["safety_trip"] == Trip.TILT
    assert drone.armed is False
    assert drone.motors_idle is False
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.position[2] == 0.0


def test_12_kill_while_airborne() -> None:
    """kill() while airborne: applied, disarmed, primitive state IDLE, z == 0."""
    drone = FakeDrone()
    client = WfbClient(drone.send)
    client.arm()
    client.idle()
    client.takeoff()
    run(drone, client, 2.5, heartbeat=True)
    assert drone.position[2] == pytest.approx(0.5, abs=0.02)

    assert client.kill() is True
    assert drone.armed is False
    assert drone.motors_idle is False
    assert drone.status()["prim_state"] == PrimState.IDLE
    assert drone.position[2] == 0.0


def test_13_corrupt_and_unknown_frames() -> None:
    """A frame with a wrong checksum, a truncated frame, and an unknown command id each

    return REJECTED and leave status() unchanged.
    """
    drone = FakeDrone()
    valid_frame = encode(CMD_PRIM, PrimIdx.HEARTBEAT, 0.0, 1)
    status_before = drone.status()

    # Wrong checksum
    bad_crc_frame = valid_frame[:-1] + bytes([(valid_frame[-1] ^ 0xFF),])
    assert drone.send(bad_crc_frame) == int(Outcome.REJECTED)
    assert drone.status() == status_before

    # Truncated frame
    trunc_frame = valid_frame[:8]
    assert drone.send(trunc_frame) == int(Outcome.REJECTED)
    assert drone.status() == status_before

    # Unknown command id
    unknown_frame = encode(0x99, 0, 0.0, 2)
    assert drone.send(unknown_frame) == int(Outcome.REJECTED)
    assert drone.status() == status_before


def test_14_determinism() -> None:
    """Two drones given the same command and step sequence return equal status() and position."""
    drone1 = FakeDrone()
    client1 = WfbClient(drone1.send)
    drone2 = FakeDrone()
    client2 = WfbClient(drone2.send)

    profile = Profile(0.3, 0.5, 0.02, 0.5)
    points = generate("circle", {"radius_m": 0.3}, profile)

    for d, c in ((drone1, client1), (drone2, client2)):
        c.arm()
        c.idle()
        c.takeoff()
        run(d, c, 2.0, heartbeat=True)
        upload(c, points)
        c.traj_start()
        run(d, c, 1.5, heartbeat=True)

    assert drone1.status() == drone2.status()
    assert drone1.position == drone2.position


def test_15_status_fields_and_types() -> None:
    """status() has exactly the 13 keys of interfaces.md section 2 and every value is a float."""
    drone = FakeDrone()
    st = drone.status()
    expected_keys = {
        "fence_push",
        "prim_state",
        "traj_state",
        "traj_n",
        "traj_rx",
        "traj_crc_hi",
        "traj_crc_lo",
        "traj_t",
        "last_err",
        "safety_trip",
        "hb_age",
        "gs_flight_active",
        "hover_z",
        "airborne_t",
    }
    assert set(st.keys()) == expected_keys
    assert len(st) == 14
    for k, v in st.items():
        assert isinstance(v, float), f"field {k} is not a float: {type(v)}"


def test_fence_push_back() -> None:
    """Just past the fence the drone is pushed back inside (no trip); held outside fence_hold_s or far past it, it lands."""
    drone = FakeDrone()
    client = WfbClient(drone.send)
    assert client.arm() is True and client.idle() is True and client.takeoff() is True
    run(drone, client, 3.0)
    assert drone.status()["prim_state"] == PrimState.HOVER

    # 1.7 > fence_x_m 1.6, within fence_over_m: pushed, no trip, then back to the hover point
    drone._x = 1.7
    run(drone, client, 0.02)
    assert drone.status()["fence_push"] == 1.0
    assert drone.status()["safety_trip"] == 0.0
    run(drone, client, 1.0)
    st = drone.status()
    assert st["fence_push"] == 0.0 and st["safety_trip"] == 0.0 and st["prim_state"] == PrimState.HOVER
    assert abs(drone.position[0]) < 1.6

    # ceiling: z 1.8 > ceiling_m 1.7 pushes z only (bit 4)
    drone._z = 1.8
    run(drone, client, 0.02)
    assert drone.status()["fence_push"] == 4.0
    run(drone, client, 1.0)
    assert drone.status()["fence_push"] == 0.0 and drone.status()["safety_trip"] == 0.0

    # held just outside for fence_hold_s 2.0 s -> LAND_IN_PLACE, trip FENCE, push cleared
    for i in range(110):
        drone._y = -2.1
        if i % 10 == 0:
            client.heartbeat()
        drone.step(0.02)
    assert drone.status()["safety_trip"] == Trip.FENCE
    assert drone.status()["fence_push"] == 0.0


def test_fence_far_lands_at_once() -> None:
    drone = FakeDrone()
    client = WfbClient(drone.send)
    assert client.arm() is True and client.idle() is True and client.takeoff() is True
    run(drone, client, 3.0)
    drone._z = 2.1   # past ceiling_m + fence_over_m (2.0)
    run(drone, client, 0.02)
    assert drone.status()["safety_trip"] == Trip.CEILING
    assert drone.status()["prim_state"] == PrimState.DESCEND
