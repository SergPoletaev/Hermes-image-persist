@echo off
REM ============================================================
REM  Hermes Image Persist 1.0.2 - Windows auto-installer
REM  Double-click this file. No terminal, no commands to type.
REM ============================================================
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Hermes Image Persist - Installer

echo.
echo   ============================================================
echo     Hermes Image Persist 1.0.2 - Windows Installer
echo   ============================================================
echo.

REM --- figure out where Hermes lives ---
set "HH=%LOCALAPPDATA%\hermes"
if defined HERMES_HOME set "HH=%HERMES_HOME%"

REM --- find a Python 3.9+ interpreter ---
set "PY="

REM 1) Hermes' bundled interpreter under tools\ (e.g. python-3.14.x)
if exist "%HH%\tools" (
    for /f "delims=" %%I in ('dir /s /b "%HH%\tools\python.exe" 2^>nul') do @(
        if not defined PY (
            "%%I" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
            if !errorlevel!==0 set "PY=%%I"
        )
    )
)

REM 2) PM dependency generations under installs\
if not defined PY if exist "%HH%\installs" (
    for /f "delims=" %%I in ('dir /s /b "%HH%\installs\python.exe" 2^>nul') do @(
        if not defined PY (
            "%%I" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
            if !errorlevel!==0 set "PY=%%I"
        )
    )
)

REM 3) System python on PATH
if not defined PY (
    python -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if !errorlevel!==0 set "PY=python"
)

REM 4) py launcher
if not defined PY (
    py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if !errorlevel!==0 set "PY=py -3"
)

if not defined PY (
    echo   [ERROR] Python 3.9+ was not found.
    echo.
    echo   Looked in:
    echo     %HH%\tools
    echo     %HH%\installs
    echo     system PATH
    echo.
    echo   Install Python 3.9+ from https://python.org or install Hermes first.
    echo.
    pause
    exit /b 1
)

echo   Python:  %PY%
"%PY%" -c "import sys;print('   Version:', sys.version.split()[0], '('+sys.executable+')')"
echo   Hermes:  %HH%
echo.

REM --- preview pass (no changes) ---
echo   Step 1/2: Preview ^(safe, nothing is changed yet^)
echo   ------------------------------------------------------------
echo.
"%PY%" "%~dp0install.py"
set "RC=!errorlevel!"
echo.

if !RC! neq 0 (
    echo   ------------------------------------------------------------
    echo   [STOP] Preview returned error code !RC!.
    echo   Read the message above - the agent root was not found or no
    echo   layer could be planned. Nothing was changed.
    echo.
    echo   If Hermes is installed in a non-default location, set the
    echo   HERMES_HOME environment variable and retry.
    echo.
    pause
    exit /b !RC!
)

echo   ------------------------------------------------------------
echo.
echo   Preview looks good? Type Y to install, or anything else to cancel.
echo.
set "ANS="
set /p "ANS=   Install now? [Y/N]: "
if /i not "!ANS!"=="Y" (
    echo.
    echo   Cancelled. Nothing was changed.
    echo.
    pause
    exit /b 0
)

REM --- apply pass ---
echo.
echo   Step 2/2: Installing ^(runtime shim + source patch + guard^)
echo   ------------------------------------------------------------
echo.
"%PY%" "%~dp0install.py" --apply --yes
set "RC=!errorlevel!"
echo.

echo   ------------------------------------------------------------
if !RC!==0 (
    echo   SUCCESS - installation complete.
    echo.
    echo   Verify it works:
    echo     1. Open Hermes Desktop, start a new session in Text mode.
    echo     2. Paste an image with a caption, wait for the reply.
    echo     3. The thumbnail must stay after the reply and after
    echo        switching sessions.
    echo     4. Diagnostics anytime: double-click diagnose.bat
) else (
    echo   Installer returned code !RC!. Check the messages above.
)
echo.
echo   Press any key to close this window.
pause >nul
exit /b !RC!
