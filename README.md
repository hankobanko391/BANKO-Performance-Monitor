# BANKO Performance Monitor & System Workspace (`v3.0.0`)

Industrial Windows 10/11 hardware telemetry suite, GPU/CPU MHz & GHz overclocker, hybrid PawnIO multi-fan engine, and 11-channel system workstation merging a **Discord-style 3-column workspace** with **HWiNFO64-grade tabular data grids**.

Zero rounded AI cards, zero neon glows, zero gradients, and zero clipped text boxes — pure flat dark tones (`#111214` rail, `#18191C` sidebar, `#1E1F22` workspace, `#2B2D31` headers, `1px` `#313338` grid borders), Discord Blurple (`#5865F2`), Status Green (`#23A55A`), Thermal Amber (`#F0B232`), Danger Red (`#F23F43`), and fixed-width `Consolas` monospace columns (`1280x780` minimum resizable window).

## Official Release & Publisher Information

- **Official Publisher**: **Yassin Haitham** ([`hankobanko391`](https://github.com/hankobanko391))
- **Portfolio Hub**: https://hankobanko391.github.io/
- **Version**: `v3.0.0` (`DISCORD 3-COLUMN WORKSPACE`)
- **Official Website (GitHub Pages)**: https://hankobanko391.github.io/BANKO-Performance-Monitor/
- **Official Releases**: https://github.com/hankobanko391/BANKO-Performance-Monitor/releases
- **Source Repository**: https://github.com/hankobanko391/BANKO-Performance-Monitor

## Official SHA-256 Binary Verification

Verify downloaded Windows executables against the official SHA-256 checksum generated directly from the compiled release binary:

| File | Version | Size | SHA-256 |
| --- | --- | --- | --- |
| `BANKO.exe` | `v3.0.0` | `23,749,298 bytes` (~22.6 MB) | `0B0852D7840D5D2DCA34FB8AE0603DFB3BC9F54DC7FE16653F7E87141C5EA39E` |

Verify locally in Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\BANKO.exe
```

## What's New in v3.0.0

### Discord-Inspired 3-Column Tabular Architecture
1. **Far-Left Workspace Mode Rail (`64px`, `#111214`)**: Sharp `2px` mode switcher (`[SYS]`, `[OC]`, `[NET]`, `[DISK]`, `[TEST]`) with `#5865F2` active indicator pill, UAC elevation badge (`ADM`/`STD`), and live `1000ms` heartbeat square.
2. **Channel Sidebar (`230px`, `#18191C`)**: Categorized `#`-prefixed channels across `TELEMETRY`, `TUNING`, `SYSTEM`, and `DIAGNOSTICS`, plus bottom dock for Watchdog status, CSV Recording indicator, **MINI HUD**, and **DWM FPS HUD**.
3. **Main Tabular Workspace (`#1E1F22` + `#2B2D31` headers + `1px` `#313338` grids)**: Unclipped KPI strips (displaying full `1366 / 3504 MHz` GPU core/memory clocks cleanly), alternating `#1E1F22`/`#232428` rows, and sharp `2px` action buttons.

### Core Hardware & Tuning Modules (Retained & Upgraded)
- **`# hw-telemetry`**: HWiNFO64-style live sensor tables (`Current`, `Min`, `Max`, `Status`) covering CPU temp (`°C`), CPU clock (`GHz`), CPU load & peak thread, GPU temp (`°C`), full GPU Core/Mem clocks (`MHz`), exact VRAM (`Used / Total MB`), detailed RAM breakdown (`Installed/Usable`, `Windows OS Footprint`, `Apps Used`, `Available`), AC charger/battery telemetry, active Windows power plan, wattage estimate, and `AC TURBO` / `BALANCED` controls.
- **`# fan-cooling`**: Scans Motherboard, SuperIO, Embedded Controller (EC), Controller, and GPU nodes via PawnIO `LibreHardwareMonitorLib.dll` (`pythonnet`) for real hardware fan RPMs (`LIVE RPM (LHM)`), automatically switching to **Hybrid Thermal Curve Estimation** (`FAN 01 CPU` & `FAN 02 GPU` estimated RPM + duty `%`) when OEM BIOS locks EC tachometers.
- **`# clock-overclock`**: Precision GPU MHz slider (`800–1850 MHz`, `15 MHz` step via `nvidia-smi --lock-gpu-clocks`), CPU GHz slider (`1.80–4.50 GHz`, `0.10 GHz` step via `powercfg PROCFREQMAX`), one-click **Safe Profiles** (`ECO`, `BALANCED`, `MAX SAFE OC`, `RESET TO STOCK`), and selectable **`82°C SAFE` / `86°C MAX` Thermal Watchdog** auto-cutoff.
- **`# system-boosters`**: 4 independent booster controls + `ACTIVATE ALL 4 BOOSTERS` — **RAM Boost** (`EmptyWorkingSet` + standby list purge), **GPU Boost** (P0 persistence + DirectX High-Perf + PCIe ASPM=0 + HAGS), **CPU Boost** (`CPMINCORES 100` + 100% AC min/max + Aggressive Boost), and **Windows OS Boost** (`SysMain`/`DiagTrack` pause + shader/temp cache trim + DNS flush + Game Mode).

### 7 New Engineering Modules in v3.0.0
1. **`# process-killer`**: Live top RAM/CPU process table with search filter, bloat tagging, 1-click `KILL TASK` (with Windows kernel/critical process guardrails), and `SET HIGH PRIORITY` / `SET NORMAL`.
2. **`# network-ping`**: Live concurrent ICMP ping monitor (`ms`, min/avg/max/loss%) to `1.1.1.1`, `1.0.0.1`, `8.8.8.8`, `9.9.9.9` + custom endpoints, 1-click DNS switcher (`Cloudflare Gaming DNS`, `Google 8.8.8.8`, `Automatic DHCP`), and `FLUSH DNS CACHE`.
3. **`# storage-health`**: Live NVMe/SSD temperature (`°C` via LHM/CIM), drive capacity & usage table, real-time per-disk Read/Write `MB/s` throughput, and 1-click Windows `Optimize-Volume -ReTrim`.
4. **`# startup-optimizer`**: Unified tabular view of Windows Startup Registry (`HKCU`/`HKLM`/`WOW6432Node`), Startup Folders, and curated background services with non-destructive `StartupApproved` & `sc` Enable/Disable toggles.
5. **`# display-hz`**: Enumerates active monitors, resolution, bit depth, and supported refresh rates via Win32 `EnumDisplaySettingsW` / `ChangeDisplaySettingsExW`, with quick `MAX REFRESH RATE` and `BATTERY 60Hz` toggles.
6. **`# benchmark-stress`**: Built-in **30-second multi-threaded CPU/GPU compute stability test** with live 1-second temperature, clock, throughput (`kOps/s`), and thermal guard abort table.
7. **`# session-logger`**: Start/Stop live CSV recorder logging timestamped CPU/GPU temps, MHz/GHz clocks, VRAM, RAM, power, disk throughput, and fan RPMs into `%USERPROFILE%\Documents\BANKO_Logs\`.

## Run From Source

```powershell
pip install -r requirements.txt
python main.py
```

Or extract `BANKO_Portable_Setup.zip` and run **`setup_and_run.bat`** (auto-elevates UAC, installs dependencies, creates Desktop shortcut, and launches cleanly).

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
  - [**BANKO Performance Monitor**](https://github.com/hankobanko391/BANKO-Performance-Monitor) ([Live Site](https://hankobanko391.github.io/BANKO-Performance-Monitor/)) — Universal Windows 10/11 3-column Discord-inspired hardware monitor, hybrid fan estimator, MHz/GHz overclocker, and 11-module system workstation.
  - [**BANKO Safe OC Tuners (BANKO Tuner)**](https://github.com/hankobanko391/BANKO-Safe-OC-Tuners) ([Live Site](https://hankobanko391.github.io/BANKO-Safe-OC-Tuners/)) — Brand-locked HP and Dell laptop GPU & performance tuners with an 82°C automatic thermal cutoff and zero-voltage safety lock.
