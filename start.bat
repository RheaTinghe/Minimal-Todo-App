@echo off
rem Double-click to launch Minimal Todo.
rem The app starts the backend server by itself, so this is the only step.
cd /d "%~dp0"

rem Sanity check first, so failures show an error window instead of nothing.
python -c "import tkinter, requests, flask, flask_sqlalchemy" >nul 2>nul
if errorlevel 1 goto :diagnose

where pythonw >nul 2>nul
if %errorlevel%==0 (
    rem pythonw = no console window
    start "" pythonw desktop_app.py
) else (
    start "" /min python desktop_app.py
)
exit /b 0

:diagnose
echo ============================================================
echo  Minimal Todo could not start. Details:
echo ============================================================
python --version
python -c "import tkinter, requests, flask, flask_sqlalchemy"
echo.
echo Common fixes:
echo   * "'python' is not recognized" or the Microsoft Store opened:
echo       Install Python from https://www.python.org/downloads/
echo       and tick "Add python.exe to PATH" during setup.
echo   * "No module named ..." :
echo       Run:  python -m pip install -r requirements.txt
echo ============================================================
pause
