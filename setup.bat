@echo off
:: ============================================================
::  COC Bot — First-Time Setup Script
::  Run this once after cloning or moving the project to a new PC.
:: ============================================================
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo   Clash of Clans Bot - Setup
echo  ============================================
echo.

:: ----------------------------------------------------------
:: 1. Check Python is installed
:: ----------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Found %PYVER%

:: ----------------------------------------------------------
:: 2. Create virtual environment
:: ----------------------------------------------------------
set VENV_DIR=%~dp0.venv

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo [OK] Virtual environment already exists at .venv\
) else (
    echo [..] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created at .venv\
)

:: ----------------------------------------------------------
:: 3. Install dependencies
:: ----------------------------------------------------------
echo [..] Installing dependencies from requirements.txt...
"%VENV_DIR%\Scripts\pip.exe" install --upgrade pip --quiet
"%VENV_DIR%\Scripts\pip.exe" install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] All dependencies installed

:: ----------------------------------------------------------
:: 4. Check Tesseract OCR
:: ----------------------------------------------------------
set TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
if exist "%TESSERACT_PATH%" (
    echo [OK] Tesseract OCR found
) else (
    echo [WARN] Tesseract OCR not found at: %TESSERACT_PATH%
    echo        Download from: https://github.com/UB-Mannheim/tesseract/wiki
    echo        Install to the default path: C:\Program Files\Tesseract-OCR\
)

:: ----------------------------------------------------------
:: 5. Create required directories
:: ----------------------------------------------------------
if not exist "%~dp0data" mkdir "%~dp0data"
if not exist "%~dp0logs" mkdir "%~dp0logs"
if not exist "%~dp0logs\gem_guard_diagnostics" mkdir "%~dp0logs\gem_guard_diagnostics"
echo [OK] Data and log directories ready

:: ----------------------------------------------------------
:: 6. Run preflight check
:: ----------------------------------------------------------
echo.
echo [..] Running preflight validation...
"%VENV_DIR%\Scripts\python.exe" "%~dp0main.py" --preflight
if errorlevel 1 (
    echo.
    echo [WARN] Preflight check reported issues (see above).
    echo        This is normal if Google Play Games is not running yet.
) else (
    echo [OK] Preflight check passed!
)

:: ----------------------------------------------------------
:: Done!
:: ----------------------------------------------------------
echo.
echo  ============================================
echo   Setup Complete!
echo  ============================================
echo.
echo  To run the bot:
echo    .venv\Scripts\python.exe main.py
echo.
echo  To run in test mode (no clicks):
echo    .venv\Scripts\python.exe main.py --dry-run --once
echo.
echo  To set up autostart on Windows boot:
echo    Run setup_autostart.bat as Administrator
echo.
pause
