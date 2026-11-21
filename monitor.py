"""BANKO v2.2.0 telemetry + hardware breakdown + hybrid fan & power sampling.

Designed for ultra-low background overhead on any Windows 10/11 PC or laptop:
  * one background thread, one sample per second
  * NVIDIA GPUs stream via a single persistent `nvidia-smi -lms 1000` pipe
    (temp, load, MHz clock, VRAM used/total MB, P-State, power draw)
  * AMD / Intel / non-NVIDIA GPUs fall back cleanly to WMI / registry
    identification and Windows GPU Engine performance counters
  * CPU load, peak core, dynamic Turbo GHz, detailed kernel/OS RAM breakdown,
    and Windows AC/battery telemetry via native kernel32/psapi/powrprof + psutil
  * Hybrid multi-fan speed engine via PawnIO LibreHardwareMonitorLib.dll
  * console windows are always suppressed (CREATE_NO_WINDOW)
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field

import psutil

from fan_engine import FanArrayState, FanEngine
from tuner_engine import (
    _empty_working_sets,
    _purge_standby_list,
    activate_ac_turbo_power_mode,
    read_active_power_plan,
)

CREATE_NO_WINDOW = 0x08000000
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE

DEFAULT_GPU_NAME = "Windows Display Adapter"

_GENERIC_STRINGS = {
    "",
    "system manufacturer",
    "system product name",
    "system version",
    "to be filled by o.e.m.",
    "default string",
    "not applicable",
    "none",
    "o.e.m.",
    "sku",
}
_VIRTUAL_GPU_PATTERNS = (
    "microsoft basic",
    "remote display",
    "parsec",
    "virtual",
    "citrix",
    "luminon",
    "meta virtual",
    "oculus",
    "vnc",
)
_BLOAT_PROCESSES = {
    "gamebarpresencewriter.exe",
    "widgetservice.exe",
    "widgets.exe",
    "yourphone.exe",
    "phoneexperiencehost.exe",
}
_OS_PROCESS_NAMES = {
    "system",
    "system idle process",
    "registry",
    "memory compression",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "winlogon.exe",
    "sihost.exe",
    "taskhostw.exe",
    "explorer.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "searchindexer.exe",
    "runtimebroker.exe",
    "ctfmon.exe",
    "msmpeng.exe",
    "securityhealthservice.exe",
}
_NA_TOKENS = {"", "n/a", "[n/a]", "not supported", "[not supported]", "none", "null"}


class _PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


class _SYSTEM_BATTERY_STATE(ctypes.Structure):
    _fields_ = [
        ("AcOnLine", ctypes.c_ubyte),
        ("BatteryPresent", ctypes.c_ubyte),
        ("Charging", ctypes.c_ubyte),
        ("Discharging", ctypes.c_ubyte),
        ("Spare1", ctypes.c_ubyte * 3),
        ("Tag", ctypes.c_ubyte),
        ("MaxCapacity", wintypes.DWORD),
        ("RemainingCapacity", wintypes.DWORD),
        ("Rate", wintypes.LONG),
        ("EstimatedTime", wintypes.DWORD),
        ("DefaultAlert1", wintypes.DWORD),
        ("DefaultAlert2", wintypes.DWORD),
    ]


def _popen(args, **kw):
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        startupinfo=_STARTUPINFO,
        text=True,
        **kw,
    )


def _run(args, timeout=15):
    return subprocess.run(
        args,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        startupinfo=_STARTUPINFO,
    )


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _is_valid_hw(value: str | None) -> bool:
    return _clean_text(value).lower() not in _GENERIC_STRINGS


# --------------------------------------------------------------------------- #
# Static hardware detection (brand-agnostic)
# --------------------------------------------------------------------------- #
def get_system_name() -> str:
    """Detect PC manufacturer and model across HP, Dell, Lenovo, ASUS, Acer, MSI, and custom builds."""
    mfg = ""
    model = ""

    for subkey in (
        r"HARDWARE\DESCRIPTION\System\BIOS",
        r"SYSTEM\CurrentControlSet\Control\SystemInformation",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as k:
                if not _is_valid_hw(mfg):
                    try:
                        mfg = _clean_text(str(winreg.QueryValueEx(k, "SystemManufacturer")[0]))
                    except OSError:
                        pass
                if not _is_valid_hw(model):
                    for val_name in ("SystemProductName", "SystemFamily"):
                        try:
                            cand = _clean_text(str(winreg.QueryValueEx(k, val_name)[0]))
                            if _is_valid_hw(cand):
                                model = cand
                                break
                        except OSError:
                            pass
        except OSError:
            pass

    if not _is_valid_hw(mfg) or not _is_valid_hw(model):
        try:
            r = _run(["wmic", "computersystem", "get", "manufacturer,model", "/format:value"], timeout=6)
            if r.returncode == 0:
                for line in (r.stdout or "").splitlines():
                    if line.startswith("Manufacturer=") and not _is_valid_hw(mfg):
                        mfg = _clean_text(line.split("=", 1)[1])
                    elif line.startswith("Model=") and not _is_valid_hw(model):
                        model = _clean_text(line.split("=", 1)[1])
        except Exception:
            pass

    if not _is_valid_hw(mfg) or not _is_valid_hw(model):
        try:
            r = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    "(Get-CimInstance Win32_ComputerSystem | Select-Object -First 1) | ForEach-Object { \"$($_.Manufacturer)|$($_.Model)\" }",
                ],
                timeout=8,
            )
            if r.returncode == 0 and "|" in (r.stdout or ""):
                p_mfg, p_mod = (r.stdout or "").strip().split("|", 1)
                if not _is_valid_hw(mfg):
                    mfg = _clean_text(p_mfg)
                if not _is_valid_hw(model):
                    model = _clean_text(p_mod)
        except Exception:
            pass

    mfg_ok = _is_valid_hw(mfg)
    mod_ok = _is_valid_hw(model)
    if mfg_ok and mod_ok:
        return model if model.lower().startswith(mfg.lower()) else f"{mfg} {model}"
    if mod_ok:
        return model
    if mfg_ok:
        return f"{mfg} PC"
    return "Windows PC"


def _gpu_rank(name: str) -> int:
    low = name.lower()
    if any(v in low for v in _VIRTUAL_GPU_PATTERNS):
        return -1
    if any(k in low for k in ("rtx", "gtx", "quadro", "nvidia", "radeon rx", "radeon pro", "intel arc", "arc ")):
        return 3
    if any(k in low for k in ("radeon", "amd", "iris", "uhd", "hd graphics", "intel")):
        return 2
    return 1


def get_gpu_name_fallback() -> tuple[str, float | None]:
    """Identify primary physical GPU and fallback VRAM total MB via registry / WMI."""
    candidates: list[tuple[str, float | None]] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    for idx in range(10):
        sub = f"{base}\\{idx:04d}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                name = ""
                for val in ("DriverDesc", "HardwareInformation.AdapterString"):
                    try:
                        raw = winreg.QueryValueEx(k, val)[0]
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
                        cand = _clean_text(str(raw))
                        if cand:
                            name = cand
                            break
                    except OSError:
                        pass
                if not name:
                    continue
                vram_mb: float | None = None
                for mem_val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                    try:
                        mem_raw = winreg.QueryValueEx(k, mem_val)[0]
                        if isinstance(mem_raw, bytes) and len(mem_raw) >= 4:
                            mem_bytes = int.from_bytes(mem_raw, "little", signed=False)
                        else:
                            mem_bytes = int(mem_raw)
                        if mem_bytes > 0:
                            vram_mb = round(mem_bytes / (1024 * 1024), 0)
                            break
                    except (OSError, ValueError, TypeError):
                        pass
                if not any(c[0] == name for c in candidates):
                    candidates.append((name, vram_mb))
        except OSError:
            pass

    if not candidates:
        try:
            r = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { \"$($_.Name)|$($_.AdapterRAM)\" }",
                ],
                timeout=8,
            )
            if r.returncode == 0:
                for line in (r.stdout or "").splitlines():
                    if "|" not in line:
                        continue
                    g_name, g_ram = line.split("|", 1)
                    name = _clean_text(g_name)
                    if not name:
                        continue
                    try:
                        ram_mb = round(int(g_ram.strip()) / (1024 * 1024), 0) if g_ram.strip() else None
                    except ValueError:
                        ram_mb = None
                    candidates.append((name, ram_mb))
        except Exception:
            pass

    valid = [c for c in candidates if _gpu_rank(c[0]) >= 0]
    if not valid:
        return DEFAULT_GPU_NAME, None
    valid.sort(key=lambda item: _gpu_rank(item[0]), reverse=True)
    return valid[0]


def get_cpu_name() -> str:
    raw = ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as k:
            raw = winreg.QueryValueEx(k, "ProcessorNameString")[0]
    except Exception:
        import platform

        raw = platform.processor() or "CPU"
    name = re.sub(r"\((R|TM|tm|r)\)", "", raw)
    name = re.sub(r"\s+(CPU|Processor)\b", "", name, flags=re.I)
    name = re.sub(r"\s*@.*$", "", name)
    name = re.sub(r"\s+with\s+.*$", "", name, flags=re.I)
    name = re.sub(r"\s+\d+-Core.*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip() or "CPU"


# --------------------------------------------------------------------------- #
# FPS (DWM compositor frame counter)
# --------------------------------------------------------------------------- #
class DwmFps:
    """Frames-per-second of the Windows compositor (DWM)."""

    _CFRAME_OFFSET = 56  # offset of DWM_TIMING_INFO.cFrame (x64 & x86 identical)

    def __init__(self):
        self._ok = False
        self._size = 0
        self._last = None
        try:
            self._dwm = ctypes.windll.dwmapi
            self._buf = ctypes.create_string_buffer(1024)
            for size in range(100, 400, 4):
                ctypes.memset(self._buf, 0, 1024)
                ctypes.c_uint32.from_buffer(self._buf).value = size
                if self._dwm.DwmGetCompositionTimingInfo(None, self._buf) == 0:
                    self._size = size
                    self._ok = True
                    break
        except Exception:
            self._ok = False

    def _frame_count(self):
        ctypes.memset(self._buf, 0, 1024)
        ctypes.c_uint32.from_buffer(self._buf).value = self._size
        if self._dwm.DwmGetCompositionTimingInfo(None, self._buf) != 0:
            return None
        return ctypes.c_uint64.from_buffer(self._buf, self._CFRAME_OFFSET).value

    def sample(self):
        if not self._ok:
            return None
        now = time.perf_counter()
        count = self._frame_count()
        if count is None:
            return None
        prev, self._last = self._last, (now, count)
        if prev is None or now <= prev[0]:
            return None
        return max(0.0, (count - prev[1]) / (now - prev[0]))


# --------------------------------------------------------------------------- #
# Native kernel RAM & AC/Battery telemetry
# --------------------------------------------------------------------------- #
def _get_installed_ram_gb(fallback_total_gb: float) -> float:
    try:
        kb = ctypes.c_uint64(0)
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
            if kb.value > 0:
                return round(kb.value / (1024 * 1024), 2)
    except Exception:
        pass
    return round(fallback_total_gb, 2)


def _get_kernel_and_cache_gb() -> tuple[float, float]:
    try:
        pi = _PERFORMANCE_INFORMATION()
        pi.cb = ctypes.sizeof(pi)
        if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
            ps = pi.PageSize
            kernel_gb = (pi.KernelTotal * ps) / (2**30)
            cache_gb = (pi.SystemCache * ps) / (2**30)
            return kernel_gb, cache_gb
    except Exception:
        pass
    return 0.0, 0.0


def _sample_os_process_footprint_gb() -> float:
    """Estimate Windows core system/service process RSS footprint in GB."""
    total_bytes = 0
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in _OS_PROCESS_NAMES:
                mi = proc.info.get("memory_info")
                if mi:
                    total_bytes += int(mi.rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return total_bytes / (2**30)


def _read_battery_power(lhm_power_w: float | None) -> tuple[bool, float | None, str, float | None, int | None]:
    """Read battery percentage, AC/charging status, rate (W), and remaining minutes."""
    bat_ps = None
    try:
        bat_ps = psutil.sensors_battery()
    except Exception:
        bat_ps = None

    s = _SYSTEM_BATTERY_STATE()
    try:
        rc = ctypes.windll.powrprof.CallNtPowerInformation(5, None, 0, ctypes.byref(s), ctypes.sizeof(s))
    except Exception:
        rc = -1

    if rc == 0:
        if not s.BatteryPresent:
            return False, None, "AC MAINS", lhm_power_w, None

        pct: float | None = None
        if s.MaxCapacity > 0:
            pct = round(max(0.0, min(100.0, (s.RemainingCapacity / s.MaxCapacity) * 100.0)), 1)
        elif bat_ps is not None:
            pct = round(float(bat_ps.percent), 1)

        rate_w: float | None = None
        # Windows returns 0x80000000 (-2147483648) for BATTERY_UNKNOWN_RATE
        if s.Rate not in (0, -2147483648) and abs(s.Rate) < 250000:
            rate_w = round(abs(s.Rate) / 1000.0, 1)
        elif lhm_power_w is not None and lhm_power_w > 0:
            rate_w = lhm_power_w

        if s.Charging:
            status = "CHARGING"
        elif s.Discharging:
            status = "DISCHARGING"
        elif s.AcOnLine:
            status = "AC PLUGGED IN"
        elif bat_ps is not None and bat_ps.power_plugged:
            status = "AC PLUGGED IN"
        else:
            status = "DISCHARGING"

        mins: int | None = None
        if s.Discharging and 0 < s.EstimatedTime < 86400:
            mins = int(s.EstimatedTime // 60)
        elif bat_ps is not None and bat_ps.secsleft and bat_ps.secsleft > 0:
            mins = int(bat_ps.secsleft // 60)

        return True, pct, status, rate_w, mins

    if bat_ps is None:
        return False, None, "AC MAINS", lhm_power_w, None

    pct = round(float(bat_ps.percent), 1)
    if bat_ps.power_plugged:
        status = "CHARGING" if pct < 99.0 else "AC PLUGGED IN"
    else:
        status = "DISCHARGING"
    mins = int(bat_ps.secsleft // 60) if (not bat_ps.power_plugged and bat_ps.secsleft > 0) else None
    return True, pct, status, lhm_power_w, mins


# --------------------------------------------------------------------------- #
# Snapshot Dataclass & Background PowerShell Telemetry Loop
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    system_name: str = "Windows PC"
    gpu_name: str = DEFAULT_GPU_NAME
    gpu_temp: float | None = None
    gpu_load: float | None = None
    gpu_clock_mhz: float | None = None
    gpu_max_clock_mhz: float | None = None
    gpu_vram_used_mb: float | None = None
    gpu_vram_total_mb: float | None = None
    gpu_vram_pct: float | None = None
    gpu_pstate: str | None = None
    gpu_power_w: float | None = None
    cpu_name: str = "CPU"
    cpu_load: float = 0.0
    cpu_peak_core: float = 0.0
    cpu_temp: float | None = None
    cpu_cores: int = 1
    cpu_threads: int = 1
    cpu_freq_ghz: float = 0.0
    ram_installed_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_os_kernel_gb: float = 0.0
    ram_apps_gb: float = 0.0
    ram_sys_cache_gb: float = 0.0
    ram_free_gb: float = 0.0
    ram_percent: float = 0.0
    battery_present: bool = False
    battery_percent: float | None = None
    power_status: str = "AC MAINS"
    power_rate_w: float | None = None
    battery_time_left_min: int | None = None
    power_plan_name: str = "BALANCED"
    power_plan_guid: str = ""
    fan_state: FanArrayState = field(
        default_factory=lambda: FanArrayState(
            mode="HYBRID_ESTIMATED",
            mode_banner="HYBRID THERMAL CURVE ESTIMATION",
            fan_count=0,
            fans=[],
            dll_loaded=False,
            backend="Initializing",
        )
    )
    fps: float | None = None
    stamp: float = field(default_factory=time.time)


_PS_TELEMETRY_LOOP = r"""
$ErrorActionPreference='SilentlyContinue'
function Get-Lhm($ns,$pat){
  $s=Get-CimInstance -Namespace $ns -ClassName Sensor | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -match $pat} | Sort-Object Value -Descending | Select-Object -First 1
  if($s){[double]$s.Value}
}
function Get-Acpi(){
  $z=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature | Select-Object -First 1
  if($z){($z.CurrentTemperature/10)-273.15}
}
function Get-Tz(){
  $c=(Get-Counter '\Thermal Zone Information(*)\Temperature').CounterSamples | Sort-Object CookedValue -Descending | Select-Object -First 1
  if($c){$c.CookedValue-273.15}
}
function Get-GpuLoad(){
  $samples=(Get-Counter '\GPU Engine(*engtype_3D*)\Utilization Percentage' -ErrorAction Stop).CounterSamples
  if($samples){
    $sum=($samples | Measure-Object -Property CookedValue -Sum).Sum
    [math]::Min(100,[math]::Max(0,[math]::Round($sum,1)))
  }
}
$cpuBase=(Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
function Get-CpuFreq(){
  $p=Get-CimInstance -Namespace root\CIMV2 -ClassName Win32_PerfFormattedData_Counters_ProcessorInformation -Filter "Name='_Total'" | Select-Object -First 1 PercentProcessorPerformance, ProcessorFrequency
  if($p -and $p.PercentProcessorPerformance -gt 0 -and $cpuBase -gt 0){
    [math]::Round(($cpuBase * ($p.PercentProcessorPerformance / 100.0)) / 1000.0, 2)
  } elseif($p -and $p.ProcessorFrequency -gt 0){
    [math]::Round($p.ProcessorFrequency / 1000.0, 2)
  }
}
$cpuModes=@(
  {Get-Lhm 'root/LibreHardwareMonitor' 'CPU (Package|Core)|Core Max|Tctl|Tdie'},
  {Get-Lhm 'root/OpenHardwareMonitor' 'CPU (Package|Core)|Core Max|Tctl|Tdie'},
  {Get-Acpi},
  {Get-Tz}
)
$cpuPick=$null
foreach($m in $cpuModes){ $v=& $m; if($v -ne $null -and $v -gt 0 -and $v -lt 125){ $cpuPick=$m; break } }
$gpuTempModes=@(
  {Get-Lhm 'root/LibreHardwareMonitor' 'GPU'},
  {Get-Lhm 'root/OpenHardwareMonitor' 'GPU'}
)
$gpuTempPick=$null
foreach($m in $gpuTempModes){ $v=& $m; if($v -ne $null -and $v -gt 0 -and $v -lt 125){ $gpuTempPick=$m; break } }
$gpuLoadOk=$true
while($true){
  $ct=$null; if($cpuPick){ $ct=& $cpuPick }
  $gt=$null; if($gpuTempPick){ $gt=& $gpuTempPick }
  $gl=$null
  if($gpuLoadOk){
    try { $gl=Get-GpuLoad } catch { $gpuLoadOk=$false }
  }
  $cf=$null; try { $cf=Get-CpuFreq } catch {}
  $ctS=if($ct -ne $null -and $ct -gt 0 -and $ct -lt 125){[math]::Round($ct,1)}else{'NA'}
  $glS=if($gl -ne $null){$gl}else{'NA'}
  $gtS=if($gt -ne $null -and $gt -gt 0 -and $gt -lt 125){[math]::Round($gt,1)}else{'NA'}
  $cfS=if($cf -ne $null -and $cf -gt 0){$cf}else{'NA'}
  [Console]::Out.WriteLine("$ctS|$glS|$gtS|$cfS")
  [Console]::Out.Flush()
  Start-Sleep -Seconds 2
}
"""


class Monitor:
    """Samples GPU/VRAM, CPU, Detailed RAM, Charger/Battery, and Hybrid Fans every `interval` seconds."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu: dict[str, object] = {
            "name": None,
            "temp": None,
            "load": None,
            "clock_mhz": None,
            "max_clock_mhz": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
            "pstate": None,
            "power_w": None,
        }
        self._cpu_temp: float | None = None
        self._ps_gpu_load: float | None = None
        self._ps_gpu_temp: float | None = None
        self._ps_cpu_freq: float | None = None
        self._smi: subprocess.Popen | None = None
        self._ps: subprocess.Popen | None = None
        self._fps = DwmFps()
        self._fan_engine = FanEngine()

        self.system_name = get_system_name()
        self.cpu_name = get_cpu_name()
        self.cpu_cores = psutil.cpu_count(logical=False) or 1
        self.cpu_threads = psutil.cpu_count(logical=True) or 1
        self.fallback_gpu_name, self.fallback_vram_mb = get_gpu_name_fallback()
        self._installed_ram_gb = _get_installed_ram_gb(psutil.virtual_memory().total / (2**30))
        self._os_proc_gb = 0.0
        self._last_os_ram_poll = 0.0
        self._plan_guid, self._plan_name = read_active_power_plan()
        self._last_plan_poll = time.monotonic()

        self.snapshot = Snapshot(
            system_name=self.system_name,
            gpu_name=self.fallback_gpu_name,
            gpu_vram_total_mb=self.fallback_vram_mb,
            cpu_name=self.cpu_name,
            cpu_cores=self.cpu_cores,
            cpu_threads=self.cpu_threads,
            ram_installed_gb=self._installed_ram_gb,
            power_plan_name=self._plan_name,
            power_plan_guid=self._plan_guid,
        )
        self.sample_fps = False

    def start(self):
        psutil.cpu_percent(interval=None, percpu=True)
        self._start_nvidia_smi()
        self._start_ps_telemetry()
        self._thread = threading.Thread(target=self._run, name="banko-telemetry", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for p in (self._smi, self._ps):
            try:
                if p and p.poll() is None:
                    p.kill()
            except Exception:
                pass
        self._fan_engine.close()

    def refresh_power_plan(self) -> tuple[str, str]:
        guid, name = read_active_power_plan()
        with self._lock:
            self._plan_guid = guid
            self._plan_name = name
            self._last_plan_poll = time.monotonic()
        return guid, name

    def get(self) -> Snapshot:
        with self._lock:
            return self.snapshot

    @staticmethod
    def _parse_opt_float(token: str) -> float | None:
        cleaned = token.strip()
        if cleaned.lower() in _NA_TOKENS:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _start_nvidia_smi(self):
        try:
            self._smi = _popen(
                [
                    "nvidia-smi",
                    "--query-gpu=name,temperature.gpu,utilization.gpu,clocks.current.graphics,"
                    "clocks.max.graphics,memory.used,memory.total,pstate,power.draw",
                    "--format=csv,noheader,nounits",
                    "-lms",
                    str(int(self.interval * 1000)),
                ]
            )
            threading.Thread(target=self._read_smi, name="banko-smi", daemon=True).start()
        except Exception:
            self._smi = None

    def _read_smi(self):
        if not self._smi or not self._smi.stdout:
            return
        try:
            for line in self._smi.stdout:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 9:
                    continue
                temp = self._parse_opt_float(parts[1])
                load = self._parse_opt_float(parts[2])
                clk = self._parse_opt_float(parts[3])
                max_clk = self._parse_opt_float(parts[4])
                v_used = self._parse_opt_float(parts[5])
                v_total = self._parse_opt_float(parts[6])
                pstate = parts[7] if parts[7].lower() not in _NA_TOKENS else None
                power_w = self._parse_opt_float(parts[8])

                with self._lock:
                    self._gpu["name"] = parts[0] or self._gpu["name"]
                    if temp is not None:
                        self._gpu["temp"] = temp
                    if load is not None:
                        self._gpu["load"] = load
                    if clk is not None:
                        self._gpu["clock_mhz"] = clk
                    if max_clk is not None:
                        self._gpu["max_clock_mhz"] = max_clk
                    if v_used is not None:
                        self._gpu["vram_used_mb"] = v_used
                    if v_total is not None:
                        self._gpu["vram_total_mb"] = v_total
                    if pstate is not None:
                        self._gpu["pstate"] = pstate
                    if power_w is not None:
                        self._gpu["power_w"] = power_w
        except Exception:
            pass

    def _start_ps_telemetry(self):
        try:
            self._ps = _popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _PS_TELEMETRY_LOOP,
                ]
            )
            threading.Thread(target=self._read_ps, name="banko-ps-telemetry", daemon=True).start()
        except Exception:
            self._ps = None

    def _read_ps(self):
        if not self._ps or not self._ps.stdout:
            return
        try:
            for line in self._ps.stdout:
                parts = line.strip().split("|")
                if len(parts) < 3:
                    continue
                ct = self._parse_opt_float(parts[0])
                gl = self._parse_opt_float(parts[1])
                gt = self._parse_opt_float(parts[2])
                cf = self._parse_opt_float(parts[3]) if len(parts) >= 4 else None
                with self._lock:
                    self._cpu_temp = ct
                    if gl is not None:
                        self._ps_gpu_load = gl
                    if gt is not None:
                        self._ps_gpu_temp = gt
                    if cf is not None and cf > 0:
                        self._ps_cpu_freq = cf
        except Exception:
            pass

    def _run(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
            now_mono = time.monotonic()

            # CPU load & per-core peak
            per_core = psutil.cpu_percent(interval=None, percpu=True)
            if per_core:
                cpu_load = round(sum(per_core) / len(per_core), 1)
                cpu_peak = round(max(per_core), 1)
            else:
                cpu_load = round(psutil.cpu_percent(interval=None), 1)
                cpu_peak = cpu_load

            # Detailed RAM breakdown
            vm = psutil.virtual_memory()
            ram_total_gb = vm.total / (2**30)
            ram_used_gb = vm.used / (2**30)
            ram_free_gb = vm.available / (2**30)
            kernel_gb, sys_cache_gb = _get_kernel_and_cache_gb()
            if now_mono - self._last_os_ram_poll >= 5.0:
                self._os_proc_gb = _sample_os_process_footprint_gb()
                self._last_os_ram_poll = now_mono
            os_kernel_gb = min(ram_used_gb, max(kernel_gb, kernel_gb + self._os_proc_gb * 0.55))
            apps_gb = max(0.0, ram_used_gb - os_kernel_gb)

            # Active Windows power plan refresh every 5s
            if now_mono - self._last_plan_poll >= 5.0:
                p_guid, p_name = read_active_power_plan()
                with self._lock:
                    self._plan_guid = p_guid
                    self._plan_name = p_name
                    self._last_plan_poll = now_mono

            fps = self._fps.sample() if self.sample_fps else None

            # Dynamic CPU GHz (PerfCounter -> psutil fallback)
            with self._lock:
                ps_freq = self._ps_cpu_freq
                cpu_temp = self._cpu_temp
                gpu_load = self._gpu["load"] if self._gpu["load"] is not None else self._ps_gpu_load
                gpu_temp = (
                    self._gpu["temp"]
                    if self._gpu["temp"] is not None
                    else (self._ps_gpu_temp if self._ps_gpu_temp is not None else self._cpu_temp)
                )
                gpu_name = str(self._gpu["name"] or self.fallback_gpu_name)
                gpu_clock = self._gpu["clock_mhz"]
                gpu_max_clock = self._gpu["max_clock_mhz"]
                vram_used = self._gpu["vram_used_mb"]
                vram_total = self._gpu["vram_total_mb"] if self._gpu["vram_total_mb"] is not None else self.fallback_vram_mb
                pstate = self._gpu["pstate"]
                gpu_power = self._gpu["power_w"]
                plan_name = self._plan_name
                plan_guid = self._plan_guid

            freq_ghz = ps_freq if ps_freq else 0.0
            if freq_ghz <= 0.0:
                try:
                    cf = psutil.cpu_freq()
                    if cf and cf.current:
                        freq_ghz = cf.current / 1000.0 if cf.current > 10 else cf.current
                except Exception:
                    pass

            vram_pct: float | None = None
            if vram_used is not None and vram_total is not None and float(vram_total) > 0:
                vram_pct = round(max(0.0, min(100.0, (float(vram_used) / float(vram_total)) * 100.0)), 1)

            # Hybrid Fan Scan & LHM Battery power
            fan_state = self._fan_engine.scan_fans(
                cpu_temp_c=cpu_temp,
                gpu_temp_c=gpu_temp if isinstance(gpu_temp, (int, float)) else None,
                cpu_load_pct=cpu_load,
                gpu_load_pct=float(gpu_load) if isinstance(gpu_load, (int, float)) else 0.0,
            )
            lhm_bat_w = self._fan_engine.read_battery_power_w()
            bat_present, bat_pct, pwr_status, pwr_rate_w, bat_mins = _read_battery_power(lhm_bat_w)

            snap = Snapshot(
                system_name=self.system_name,
                gpu_name=gpu_name,
                gpu_temp=float(gpu_temp) if isinstance(gpu_temp, (int, float)) else None,
                gpu_load=float(gpu_load) if isinstance(gpu_load, (int, float)) else None,
                gpu_clock_mhz=float(gpu_clock) if isinstance(gpu_clock, (int, float)) else None,
                gpu_max_clock_mhz=float(gpu_max_clock) if isinstance(gpu_max_clock, (int, float)) else None,
                gpu_vram_used_mb=float(vram_used) if isinstance(vram_used, (int, float)) else None,
                gpu_vram_total_mb=float(vram_total) if isinstance(vram_total, (int, float)) else None,
                gpu_vram_pct=vram_pct,
                gpu_pstate=str(pstate) if pstate else None,
                gpu_power_w=float(gpu_power) if isinstance(gpu_power, (int, float)) else None,
                cpu_name=self.cpu_name,
                cpu_load=cpu_load,
                cpu_peak_core=cpu_peak,
                cpu_temp=cpu_temp,
                cpu_cores=self.cpu_cores,
                cpu_threads=self.cpu_threads,
                cpu_freq_ghz=round(freq_ghz, 2),
                ram_installed_gb=self._installed_ram_gb,
                ram_total_gb=round(ram_total_gb, 2),
                ram_used_gb=round(ram_used_gb, 2),
                ram_os_kernel_gb=round(os_kernel_gb, 2),
                ram_apps_gb=round(apps_gb, 2),
                ram_sys_cache_gb=round(sys_cache_gb, 2),
                ram_free_gb=round(ram_free_gb, 2),
                ram_percent=round(vm.percent, 1),
                battery_present=bat_present,
                battery_percent=bat_pct,
                power_status=pwr_status,
                power_rate_w=pwr_rate_w,
                battery_time_left_min=bat_mins,
                power_plan_name=plan_name,
                power_plan_guid=plan_guid,
                fan_state=fan_state,
                fps=fps,
            )
            with self._lock:
                self.snapshot = snap

            next_t += self.interval
            delay = next_t - time.perf_counter()
            if delay < 0:
                next_t = time.perf_counter()
                delay = self.interval
            self._stop.wait(delay)


def boost_system() -> str:
    """Legacy full-system boost helper delegating to core optimizers."""
    before = psutil.virtual_memory().available
    _empty_working_sets()
    _purge_standby_list()
    _, plan_msg = activate_ac_turbo_power_mode()
    time.sleep(0.25)
    gained = max(0, psutil.virtual_memory().available - before)
    return f"RAM +{gained / 2**20:.0f} MB | {plan_msg}"
