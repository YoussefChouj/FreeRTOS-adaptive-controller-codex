@echo off
rem CF A/B (ABBA): old-CF emulation (row 60 = 0) vs fixed CF (row 60 = 1), Z Ki 0.005.
rem Each flight loads its own gains file, so no manual flag writes between flights.
rem Usage: cfab            fly the 4 flights (RC arm starts each one)
rem        cfab --dry-run  print the 4 plans, nothing flies
setlocal
set RUN=--once
if /i "%~1"=="--dry-run" set RUN=--dry-run
set G=logs\lab\2026-10-27\2026-10-27-cf-ki005-mode
set N=0
for %%M in (0 1 1 0) do (
  call :one %%M || exit /b 1
)
echo === CF A/B done: 4 flights (modes 0 1 1 0) ===
exit /b 0
:one
set /a N+=1
echo === CF A/B flight %N%/4: ToF sample mode %1 (gains %G%%1.json) ===
call "%~dp0fly.cmd" lab_z_cf_hover %RUN% --gains %G%%1.json
exit /b %ERRORLEVEL%
