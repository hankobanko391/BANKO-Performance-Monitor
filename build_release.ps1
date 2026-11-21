# Build BANKO.exe (--uac-admin + LibreHardwareMonitorLib.dll) and BANKO_Portable_Setup.zip
param(
    [switch]$ZipOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$LhmDll = Join-Path $Root "LibreHardwareMonitorLib.dll"
if (-not (Test-Path $LhmDll)) {
    throw "Required LibreHardwareMonitorLib.dll was not found in $Root"
}

$DllFiles = Get-ChildItem -Path $Root -Filter "*.dll" -File | Sort-Object Name

if (-not $ZipOnly) {
    Write-Host "[1/4] Generating BANKO Tactical icon..."
    python generate_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

    Write-Host "[2/4] Cleaning previous build/dist outputs..."
    if (Test-Path "$Root\build") { Remove-Item "$Root\build" -Recurse -Force }
    if (Test-Path "$Root\dist") { Remove-Item "$Root\dist" -Recurse -Force }

    Write-Host "[3/4] Compiling standalone BANKO.exe via PyInstaller..."
    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--uac-admin",
        "--noupx",
        "--collect-all", "customtkinter",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--hidden-import", "clr",
        "--name", "BANKO",
        "--icon", "banko_icon.ico",
        "--add-data", "banko_icon.ico;."
    )
    foreach ($dll in $DllFiles) {
        $PyInstallerArgs += "--add-binary"
        $PyInstallerArgs += "$($dll.FullName);."
    }
    $PyInstallerArgs += "main.py"

    & pyinstaller @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}

Write-Host "[4/4] Packaging BANKO_Portable_Setup.zip..."
$StageDir = "$Root\build\portable_stage"
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

$FilesToCopy = @(
    "main.py",
    "monitor.py",
    "fan_engine.py",
    "tuner_engine.py",
    "generate_icon.py",
    "requirements.txt",
    "setup_and_run.bat",
    "README.md",
    "banko_icon.ico"
)
foreach ($f in $FilesToCopy) {
    Copy-Item -Path "$Root\$f" -Destination "$StageDir\$f" -Force
}
foreach ($dll in $DllFiles) {
    Copy-Item -Path $dll.FullName -Destination (Join-Path $StageDir $dll.Name) -Force
}

$ZipPath = "$Root\dist\BANKO_Portable_Setup.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($StageDir, $ZipPath)

Get-Item "$Root\dist\BANKO.exe", $ZipPath | Format-Table Name, Length, LastWriteTime
