from ground_station.service.tests.test_runner import *
import ground_station.service.tests.test_runner as tr
import traceback

def main():
    try:
        import pytest
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            tr.test_a_e2e_complete(p)
            print("DONE!")
    except Exception as e:
        traceback.print_exc()

main()
