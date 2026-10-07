@echo off
rem Starts the Battle Cats EN Patcher window (add --tray to start minimized in the tray).
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.bcen-patcher\venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo The patcher isn't set up yet. Run setup.bat first.
    pause
    exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" -m bcen_patcher %*
