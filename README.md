# BANKO Performance Monitor & Tactical Tuner (`v2.2.0`)

Industrial 3-page Windows 10/11 desktop hardware monitor, GPU/CPU MHz & GHz overclocker, hybrid PawnIO multi-fan telemetry engine, and dedicated system booster suite modeled after **Razer Synapse**, **MSI Afterburner**, and **Steam Hardware Monitor**.

Flat matte-obsidian UI (`#0A0C0F` base, `#12161C` gunmetal cards, `1px` `#222933` borders, `#44D62C` Razer Tactical Green telemetry/boosts, `#FF9E00` Thermal Amber clock controls, `Consolas` monospace right-aligned numbers — zero neon glows or rounded bubbles).

## Official Release & Publisher Information

- **Official Publisher**: **Yassin Haitham** ([`hankobanko391`](https://github.com/hankobanko391))
- **Portfolio Hub**: https://hankobanko391.github.io/
- **Version**: `v2.2.0` (`TACTICAL EDITION`)
- **Official Website (GitHub Pages)**: https://hankobanko391.github.io/BANKO-Performance-Monitor/
- **Official Releases**: https://github.com/hankobanko391/BANKO-Performance-Monitor/releases
- **Source Repository**: https://github.com/hankobanko391/BANKO-Performance-Monitor

## Official SHA-256 Binary Verification

Verify downloaded Windows executables against the official SHA-256 checksum generated directly from the built release binary:

| File | Version | Size | SHA-256 |
| --- | --- | --- | --- |
| `BANKO.exe` | `v2.2.0` | `23,677,155 bytes` (~22.6 MB) | `F9ED1AED554BE0E3B387482D68288D8CAF31B78F9D0ADF27C985952A6A447BC4` |

Verify locally in Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\BANKO.exe
```

## What's New in v2.2.0

### `[01] TELEMETRY & POWER`
- **GPU & VRAM Telemetry**: Live core temperature (`°C`), core clock (`MHz`), compute load (`%`), P-state, and exact **VRAM Usage** (`Used MB / Total MB` & `%` 20-segment bar) via streaming `nvidia-smi` (with AMD/Intel WMI/counter fallback).
- **CPU Package Telemetry**: Live temperature (`°C`), dynamic Turbo frequency (`GHz`), all-core utilization (`%`), and peak logical thread load (`%`).
- **Detailed RAM Breakdown**: Total Installed RAM (`GB`), Active Used RAM (`GB` & `%`), Windows OS & Kernel Footprint (`GB` via `GetPerformanceInfo`), and Available Free RAM (`GB` & `%`).
- **Charger & Battery Power**: Battery `%`, charging status (`CHARGING` / `AC PLUGGED IN` / `DISCHARGING` / `AC MAINS`), charge/discharge rate (`W` via `CallNtPowerInformation` / LibreHardwareMonitor), active Windows power plan, and one-click **AC Turbo Power Mode** (`Ultimate / High Performance` + 100% AC CPU state).

### `[02] CLOCKS & FANS`
- **GPU Overclocking (MHz) + 86°C Watchdog**: Precision `800–1850 MHz` slider (`15 MHz` step) executing `nvidia-smi --lock-gpu-clocks` + `RESET STOCK`, protected by an automatic **86°C Thermal Watchdog** auto-cutoff.
- **CPU Overclocking (GHz)**: Precision `1.80–4.50 GHz` slider (`0.10 GHz` step) applying `powercfg SUB_PROCESSOR PROCFREQMAX` (converted to integer MHz) + `TURBO AUTO` reset.
- **Hybrid Multi-Fan Speed Engine (`fan_engine.py`)**: Scans Motherboard, SuperIO, EC, Controller, and GPU nodes via PawnIO-compatible `LibreHardwareMonitorLib.dll` (`pythonnet`) for real `FAN 01`, `FAN 02`, `FAN 03`... RPMs (`LIVE RPM (LHM)`). When an OEM BIOS locks raw EC tachometers, automatically switches to **`HYBRID THERMAL CURVE ESTIMATION`** driven by live CPU/GPU temperatures (`ESTIMATED RPM (EC LOCKED)`).

### `[03] SYSTEM BOOSTERS`
Four dedicated individual boost cards (plus `ACTIVATE ALL BOOSTERS`):
1. **`RAM BOOST`**: Trims process working sets (`EmptyWorkingSet`) and purges Windows standby list (`NtSetSystemInformation`), displaying exact MB freed.
2. **`GPU BOOST`**: Applies DirectX High-Performance GPU preferences, disables PCIe ASPM power saving on AC, enables HAGS scheduling, and maximizes P0 responsiveness.
3. **`CPU BOOST`**: Unparks all CPU logical cores (`CPMINCORES 100`), locks AC min/max state to 100%, and enables Aggressive Boost.
4. **`WINDOWS OS BOOST`**: Suspends `SysMain`/`DiagTrack`, clears temporary & DirectX/NVIDIA/AMD shader caches, flushes DNS, and elevates Game Mode priority.

### Sidebar Overlays
- **Mini Overlay**: Compact, borderless, draggable always-on-top bar showing GPU, CPU, RAM, and VRAM.
- **DWM FPS Overlay**: Click-through corner compositor FPS readout (`DwmGetCompositionTimingInfo`).

## Run From Source

```powershell
pip install -r requirements.txt
python main.py
```

Or run **`setup_and_run.bat`** from `BANKO_Portable_Setup.zip` (auto-elevates UAC, installs dependencies, creates Desktop shortcut, and launches cleanly).

## Build Standalone Executable

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_release.ps1
```

---

## Author & Related Projects by Yassin Haitham (`hankobanko391`)

- **Author:** **Yassin Haitham** ([@hankobanko391](https://github.com/hankobanko391))
- **Portfolio Hub:** https://hankobanko391.github.io/
- **Public Windows Hardware Utilities Ecosystem:**
  - [**HANKO Thermal Suite**](https://github.com/hankobanko391/HANKO-Thermal-Suite) ([Live Site](https://hankobanko391.github.io/HANKO-Thermal-Suite/)) — Industrial Windows hardware telemetry, PawnIO multi-fan RPM array, `nvidia-smi` / `powercfg` clock tuner, and multi-core thermal load verification utility.
  - [**BANKO Performance Monitor**](https://github.com/hankobanko391/BANKO-Performance-Monitor) ([Live Site](https://hankobanko391.github.io/BANKO-Performance-Monitor/)) — Universal Windows 10/11 3-page GPU/VRAM/CPU/RAM/battery monitor, hybrid fan speed estimator, MHz/GHz overclocker, and individual system boosters.
  - [**BANKO Safe OC Tuners (BANKO Tuner)**](https://github.com/hankobanko391/BANKO-Safe-OC-Tuners) ([Live Site](https://hankobanko391.github.io/BANKO-Safe-OC-Tuners/)) — Brand-locked HP and Dell laptop GPU & performance tuners with an 82°C automatic thermal cutoff and zero-voltage safety lock.
