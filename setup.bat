@echo off
setlocal EnableDelayedExpansion
title Battle Cats EN Patcher - setup
cd /d "%~dp0"
set "HOME_DIR=%USERPROFILE%\.bcen-patcher"
set "VENV=%HOME_DIR%\venv"

echo === Battle Cats EN Patcher setup ===
echo.

rem --- Python 3.12 -------------------------------------------------------------
set "PY="
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%ProgramFiles%\Python312\python.exe") do (
    if exist %%P set "PY=%%~P"
)
if not defined PY (
    py -3.12 -c "import sys" >nul 2>&1 && for /f "delims=" %%I in ('py -3.12 -c "import sys;print(sys.executable)"') do set "PY=%%I"
)
if not defined PY (
    echo Python 3.12 was not found.
    choice /m "Install Python 3.12 now with winget"
    if errorlevel 2 goto :no_python
    winget install --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not exist "!PY!" goto :no_python
)
echo Python: %PY%

rem --- Java JDK (keytool + jarsigner, used to sign the app) --------------------
set "JDK_OK="
where keytool >nul 2>&1 && where jarsigner >nul 2>&1 && set "JDK_OK=1"
for /d %%J in ("%ProgramFiles%\Java\*" "%ProgramFiles%\Eclipse Adoptium\*" "%ProgramFiles%\Microsoft\jdk-*") do (
    if exist "%%J\bin\jarsigner.exe" set "JDK_OK=1"
)
if not defined JDK_OK (
    echo A Java JDK was not found ^(needed to sign the app^).
    choice /m "Install the free Eclipse Temurin JDK 21 now with winget"
    if errorlevel 2 (
        echo Install any JDK 17+ yourself, then run setup.bat again.
        goto :end
    )
    winget install --id EclipseAdoptium.Temurin.21.JDK --accept-package-agreements --accept-source-agreements
)
echo Java JDK: found

rem --- Python environment ------------------------------------------------------
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating the Python environment in %VENV% ...
    "%PY%" -m venv "%VENV%" || goto :fail
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip -q
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :fail

echo.
echo === Setup complete ===
echo.
echo Before the first run, in LDPlayer:
echo   1. Settings ^> Other settings ^> ADB debugging: "Open local connection"
echo   2. Settings ^> Other settings ^> Root permission: on  (recommended)
echo   3. Install the Japanese game (e.g. from QooApp) and the English game (Play Store),
echo      open each once so they download their data.
echo Then double-click run.bat.
goto :end

:no_python
echo Install Python 3.12 from https://www.python.org/downloads/ and run setup.bat again.
goto :end
:fail
echo.
echo Setup failed (see the messages above).
:end
echo.
pause
