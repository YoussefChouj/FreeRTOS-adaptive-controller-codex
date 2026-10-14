@echo off
rem lab up [queue] [--pack P]: check everything, then fly the day's queue (ground_station/lab/__main__.py)
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m ground_station.lab %*
