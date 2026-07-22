@echo off
rem Double-click to launch Minimal Todo.
rem The app starts the backend server by itself, so this is the only step.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    rem pythonw = no console window
    start "" pythonw desktop_app.py
) else (
    start "" python desktop_app.py
)
