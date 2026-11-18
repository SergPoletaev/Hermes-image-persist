@echo off
REM ============================================================
REM  Hermes Image Persist - self-check shortcut
REM  Double-click to run 122 offline verification checks.
REM ============================================================
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Hermes Image Persist - Self-check

echo.
echo   ============================================================
echo     Hermes Image Persist - Offline Verification (122 checks)
echo   ============================================================
echo.

REM --- figure out where Hermes lives ---
set "HH=%LOCALAPPDATA%\hermes"
if defined HERMES_HOME set "HH=%HERMES_HOME%"

REM --- find a Python 3.9+ interpreter ---
set "PY="

if exist "%HH%\tools" (
    for /f "delims=" %%I in ('dir /s /b "%HH%\tools\python.exe" 2^>nul') do @(
        if not defined PY (
            "%%I" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
            if !errorlevel!==0 set "PY=%%I"
        )
    )
)
if not defined PY if exist "%HH%\installs" (
    for /f "delims=" %%I in ('dir /s /b "%HH%\installs\python.exe" 2^>nul') do @(
        if not defined PY (
            "%%I" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
            if !errorlevel!==0 set "PY=%%I"
        )
    )
)
if not defined PY (
    python -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if !errorlevel!==0 set "PY=python"
)
if not defined PY (
    py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if !errorlevel!==0 set "PY=py -3"
)

if not defined PY (
    echo   [ERROR] Python 3.9+ was not found.
    echo   Install Python 3.9+ from https://python.org or install Hermes first.
    echo.
    pause
    exit /b 1
)

echo   Using Python: %PY%
echo.
"%PY%" "%~dp0selfcheck.py"
set "RC=!errorlevel!"
echo.
if !RC!==0 (
    echo   All checks passed.
) else (
    echo   Some checks FAILED - see above.
)
echo.
echo   Press any key to close.
pause >nul
exit /b !RC!
