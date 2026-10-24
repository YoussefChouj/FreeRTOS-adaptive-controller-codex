@echo off
REM Serve the internship learning hub on http://localhost:8765/ and open it.
REM SPEC section 2: static files only, no build step.
setlocal
cd /d "%~dp0"

echo Serving %CD% on http://localhost:8765/
echo Press Ctrl+C to stop the server.

start "" http://localhost:8765/
python -m http.server 8765

endlocal
