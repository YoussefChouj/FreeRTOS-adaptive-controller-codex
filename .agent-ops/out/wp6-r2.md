STATUS: done

Files changed:
- ground_station/service/tests/test_flight_campaign_skill.py: removed unused `pytest` import (line 8).

Verification:
`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py`
Output:
...                                                                      [100%]
3 passed in 0.21s

`ruff check ground_station/service/tests/test_flight_campaign_skill.py`
Output:
Found 1 error.
[*] 1 fixable with the `--fix` option.

Open questions / risks: None (I001 was left unfixed as instructed).
SUBSTITUTIONS: none
