import pytest
pytest.main(["-q", "-p", "no:cacheprovider", "ground_station/service/tests/test_workflow_b_e2e.py::test_pause_land_abort", "-s"])
