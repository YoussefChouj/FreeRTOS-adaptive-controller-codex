@echo off
rem labday <date> [--pack P] [--from ID] [--list] [--sheet]: fly a lab day's flights in order (ground_station/lab/labday.py)
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m ground_station.lab.labday %*
