# BANKO Safe GPU & Performance Tuners (v2.0.0)

Lightweight, universal Windows 10/11 desktop hardware monitor, gaming overlay, and one-click system optimizer built with Python, Pillow, `customtkinter`, and `psutil`. Designed to work across all PCs and laptops (HP, Dell, Lenovo, ASUS, Acer, MSI, and custom builds) with zero vendor lock-in.

## Official Release & Publisher Information

- **Official Publisher**: [`hankobanko391`](https://github.com/hankobanko391)
- **Version**: `v2.0.0`
- **Official Website (GitHub Pages)**: https://hankobanko391.github.io/BANKO-Performance-Monitor/
- **Official Releases**: https://github.com/hankobanko391/BANKO-Performance-Monitor/releases
- **Source Repository**: https://github.com/hankobanko391/BANKO-Performance-Monitor

![BANKO Safe GPU & Performance Tuners](docs/preview.png)

## Official SHA-256 Binary Verification

Verify downloaded Windows executables against the official SHA-256 checksum generated directly from the built release binary:

| File | Version | Size | SHA-256 |
| --- | --- | --- | --- |
| `BANKO.exe` | `v2.0.0` | `19,428,602 bytes` (~18.5 MB) | `39BFEB75FB8B69F30782FCF75D7C72CE3454F615DC26B2E2F1E915A75464A3A1` |

Verify locally in Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\BANKO.exe
```

## Features

- **Universal Brand & Hardware Auto-Detection**: Automatically detects your PC manufacturer and model via Windows registry / WMI (`Win32_ComputerSystem`) and displays it in the header.
- **Universal GPU Telemetry (Neon Green/Cyan)**: Supports NVIDIA GPUs via silent streaming `nvidia-smi` (`CREATE_NO_WINDOW`) and gracefully falls back to `Win32_VideoController` + Windows GPU Engine performance counters for AMD Radeon and Intel Iris/Arc/UHD GPUs.
- **CPU Card (Neon Cyan)**: Displays processor name, circular load ring (`psutil`), physical core/thread counts, clock frequency (GHz), and thermal sensor temperature (°C).
- **RAM Card (Neon Purple)**: Shows total memory, purple-to-green usage progress bar, and live used/free GB metrics.
- **Mini Overlay Mode**: Collapses into a compact, borderless, semi-transparent, draggable always-on-top bar for in-game monitoring.
- **Universal Boost System**: Switches Windows to Ultimate / High Performance power plan (`powercfg`), trims working sets and idle background bloat, sets Windows DirectX High-Performance GPU preferences for detected games, and flushes DNS / optimizes TCP latency (running as Administrator also flushes the standby memory list and DNS cache).
- **FPS Overlay**: Toggles a click-through corner compositor FPS readout using Windows DWM timing (`DwmGetCompositionTimingInfo`).

## Run From Source

```powershell
pip install -r requirements.txt
python main.py
```

## Build Standalone Executable

```powershell
pyinstaller --onefile --windowed --collect-all customtkinter --name BANKO main.py
```
