@echo off
setlocal
cd /d "%~dp0"
echo ==========================================================
echo   Beverage Color DB - mobile access (proxy mode)
echo   Works when Windows Firewall blocks the venv python.exe.
echo   App: 127.0.0.1:8766   Proxy: 0.0.0.0:8765
echo   Phone on the SAME Wi-Fi: http://<PC-LAN-IP>:8765/
echo ==========================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py --proxy %*
) else (
  python start.py --proxy %*
)
echo.
echo [Service stopped] Press any key to close this window.
pause >nul
