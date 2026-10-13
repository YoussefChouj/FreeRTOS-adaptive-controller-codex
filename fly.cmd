@echo off
rem fly <campaign> [--pack P] [--run R] [--once]: the lab flight loop (ground_station/service/campaign_fly.py)
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m ground_station.service.campaign_fly %*
