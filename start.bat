@echo off
setlocal
cd /d "%~dp0"
echo ==========================================================
echo   Beverage Color DB / Drink-Wrap Web UI
echo   Local only: http://127.0.0.1:8765/
echo   First run installs dependencies (needs internet once).
echo ==========================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py %*
) else (
  python start.py %*
)
echo.
echo [Service stopped] Press any key to close this window.
pause >nul
