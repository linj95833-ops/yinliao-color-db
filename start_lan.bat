@echo off
setlocal
cd /d "%~dp0"
echo ==========================================================
echo   Beverage Color DB - LAN / mobile access (direct)
echo   Phone on the SAME Wi-Fi: http://<PC-LAN-IP>:8765/
echo   If the phone cannot connect, use start_mobile.bat instead.
echo ==========================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py --lan %*
) else (
  python start.py --lan %*
)
echo.
echo [Service stopped] Press any key to close this window.
pause >nul
