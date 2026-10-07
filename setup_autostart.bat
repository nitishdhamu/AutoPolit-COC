@echo off
REM ======================================================
REM CoC Bot — Windows Task Scheduler Setup
REM ======================================================
REM This script creates a Windows scheduled task that
REM starts the bot on user login.
REM
REM Run as Administrator!
REM ======================================================

echo.
echo ========================================
echo   CoC Bot - Autostart Setup
echo ========================================
echo.

set BOT_DIR=%~dp0
set PYTHON_EXE=%BOT_DIR%.venv\Scripts\python.exe
set TASK_NAME=CoCBot_AutoStart

REM Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This script requires Administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

echo Bot directory: %BOT_DIR%
echo Task name: %TASK_NAME%
echo.

if not exist "%PYTHON_EXE%" (
    echo ERROR: Project runtime not found: %PYTHON_EXE%
    echo Create it first with: py -3.11 -m venv .venv
    echo Then install dependencies with: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create the scheduled task
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%BOT_DIR%main.py\"" ^
    /sc onlogon ^
    /rl highest ^
    /delay 0000:30 ^
    /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS: Scheduled task created!
    echo The bot will start 30 seconds after you log in.
    echo.
    echo To disable: schtasks /disable /tn "%TASK_NAME%"
    echo To delete:  schtasks /delete /tn "%TASK_NAME%" /f
    echo To run now: schtasks /run /tn "%TASK_NAME%"
) else (
    echo.
    echo ERROR: Failed to create scheduled task.
    echo Try running this script as Administrator.
)

echo.
pause
