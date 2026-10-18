@echo off

rem cam <fusion|stop|check|start|video|floor|end> <name> [args]: ground_station/lab/camera_truth.sh from PowerShell/cmd.
rem Runs Git Bash explicitly (PowerShell's `bash` is WSL, which has no adb/scrcpy/Windows python).
rem `cam fusion <name>` with no more args = mode 3 (in-EKF) --yaw-deg 94; CT_FLOOR defaults to the newest
rem logs/video_truth/floor-* scan. One flight: .\cam fusion f1; .\fly <campaign> --once; .\cam stop f1

setlocal
cd /d "%~dp0"
set "GITBASH=C:\Program Files\Git\bin\bash.exe"
if "%CT_FLOOR%"=="" for /f "delims=" %%d in ('dir /b /ad /o-d logs\video_truth\floor-* 2^>nul') do if not defined CT_FLOOR set "CT_FLOOR=logs/video_truth/%%d"
if /i "%~1"=="fusion" if "%~3"=="" (
    "%GITBASH%" ground_station/lab/camera_truth.sh fusion %2 3 --yaw-deg 94
    exit /b %errorlevel%
)
"%GITBASH%" ground_station/lab/camera_truth.sh %*
exit /b %errorlevel%
