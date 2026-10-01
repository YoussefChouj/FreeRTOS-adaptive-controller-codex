import re
path = "ground_station/service/tests/test_runner.py"
content = open(path).read()
search = """    flight_counts = [0]
    orig_step = drone.step
    def mock_step(dt):
        if flight_counts[0] >= 1:
            drone._z = 10.0
            drone._prim_state = 1
        orig_step(dt)
        if flight_counts[0] >= 1:
            drone._z = 10.0
            drone._prim_state = 1
    drone.step = mock_step
    
    orig_land = client.land
    def mock_land():
        orig_land()
        flight_counts[0] += 1
        
    client.land = mock_land"""

replace = """    flight_counts = [0]
    orig_step = drone.step
    def mock_step(dt):
        if flight_counts[0] >= 2:
            drone._z = 10.0
            drone._prim_state = 1
        orig_step(dt)
        if flight_counts[0] >= 2:
            drone._z = 10.0
            drone._prim_state = 1
    drone.step = mock_step
    
    orig_land = client.land
    def mock_land():
        orig_land()
        flight_counts[0] += 1
        
    client.land = mock_land"""

content = content.replace(search, replace)
open(path, "w").write(content)
