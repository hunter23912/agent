@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installation completed. Run start.bat to launch.
pause
exit /b 0
:failed
echo Installation failed. Check Python and network connectivity.
pause
exit /b 1
