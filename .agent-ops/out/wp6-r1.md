STATUS: done

FILES CHANGED:
- docs/skills/flight-campaign.md (new flight-campaign skill rules and validation steps)
- ground_station/service/tests/test_flight_campaign_skill.py (tests for the skill doc)

VERIFICATION:
`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py`
3 passed in 0.26s

`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_schema.py`
94 passed in 0.27s

`python -c "from ground_station.service.campaign_schema import load_campaign; print(load_campaign('ground_station/service/campaigns/example_circle.yaml').campaign)"`
circle_pid_01

DEVIATIONS: none
OPEN RISKS: none
SUBSTITUTIONS: none
