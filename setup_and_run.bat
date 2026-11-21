@echo off
setlocal EnableExtensions

:: Auto-elevate to Administrator (required for powercfg, nvidia-smi clock locks, and memory purge)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [BANKO] Requesting Administrator privileges...
    set "BANKO_BAT=%~f0"
    set "BANKO_DIR=%~dp0"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:BANKO_BAT -WorkingDirectory $env:BANKO_DIR -Verb RunAs"
    exit /b 0
)

cd /d "%~dp0"
title BANKO v3.0.0 - Setup & Workspace Launcher

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3 was not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/ and re-run this script.
    pause
    exit /b 1
)

python -c "import customtkinter, psutil, PIL, clr" >nul 2>&1
if %errorlevel% neq 0 (
    echo [BANKO] Installing required dependencies ^(customtkinter, psutil, pillow, pythonnet^)...
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r "%~dp0requirements.txt"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install Python dependencies.
        pause
        exit /b 1
    )
)

if not exist "%~dp0banko_icon.ico" (
    echo [BANKO] Generating BANKO icon...
    python "%~dp0generate_icon.py"
)

set "BANKO_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "if ($desktop) {" ^
  "  $ws = New-Object -ComObject WScript.Shell;" ^
  "  $lnk = $ws.CreateShortcut((Join-Path $desktop 'BANKO Performance Monitor.lnk'));" ^
  "  $lnk.TargetPath = (Join-Path $env:BANKO_ROOT 'setup_and_run.bat');" ^
  "  $lnk.WorkingDirectory = $env:BANKO_ROOT.TrimEnd('\');" ^
  "  $lnk.IconLocation = (Join-Path $env:BANKO_ROOT 'banko_icon.ico') + ',0';" ^
  "  $lnk.Description = 'BANKO v3.0.0 - Hardware Telemetry & System Workspace';" ^
  "  $lnk.Save();" ^
  "}" >nul 2>&1

where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw.exe "%~dp0main.py"
) else (
    start "" python.exe "%~dp0main.py"
)
exit /b 0
