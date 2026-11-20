"""BANKO telemetry + system actions.

Everything here is designed to be extremely cheap:
  * one background thread, one sample per second
  * nvidia-smi is started ONCE in loop mode (-lms 1000) and its stdout is
    streamed, instead of spawning a new process every second
  * CPU temperature comes from one long-lived hidden PowerShell loop (5 s period)
  * the console window is always suppressed (CREATE_NO_WINDOW)
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

import psutil

CREATE_NO_WINDOW = 0x08000000
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE

DEFAULT_GPU_NAME = "NVIDIA GeForce RTX 2050"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


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


# --------------------------------------------------------------------------- #
# static info
# --------------------------------------------------------------------------- #
def get_cpu_name() -> str:
    raw = ""
    try:
        import winreg

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
    name = re.sub(r"\s+with\s+.*$", "", name, flags=re.I)  # "... with Radeon Graphics"
    name = re.sub(r"\s+\d+-Core.*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip() or "CPU"


# --------------------------------------------------------------------------- #
# FPS (DWM compositor frame counter)
# --------------------------------------------------------------------------- #
class DwmFps:
    """Frames-per-second of the Windows compositor (DWM).

    This reflects windowed / borderless games and anything else composed by
    DWM. Exclusive-fullscreen games bypass DWM and cannot be measured without
    ETW/PresentMon.
    """

    _CFRAME_OFFSET = 56  # offset of DWM_TIMING_INFO.cFrame (x64 & x86 identical)

    def __init__(self):
        self._ok = False
        self._size = 0
        self._last = None
        try:
            self._dwm = ctypes.windll.dwmapi
            self._buf = ctypes.create_string_buffer(1024)
            # DWM_TIMING_INFO size differs between Windows builds -> probe it.
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
        """Return FPS since last call (or None)."""
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
# telemetry
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    gpu_name: str = DEFAULT_GPU_NAME
    gpu_temp: float | None = None
    gpu_load: float | None = None
    cpu_name: str = "CPU"
    cpu_load: float = 0.0
    cpu_temp: float | None = None
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_percent: float = 0.0
    fps: float | None = None
    stamp: float = field(default_factory=time.time)


_PS_TEMP_LOOP = r"""
$ErrorActionPreference='SilentlyContinue'
# Probe once which sensor source works on this PC, then only poll that one (cheap).
function Get-Lhm($ns){
  $s=Get-CimInstance -Namespace $ns -ClassName Sensor | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU (Package|Core)|Core Max|Tctl|Tdie'} | Sort-Object Value -Descending | Select-Object -First 1
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
$modes=@(
  {Get-Lhm 'root/LibreHardwareMonitor'},
  {Get-Lhm 'root/OpenHardwareMonitor'},
  {Get-Acpi},
  {Get-Tz}
)
$pick=$null
foreach($m in $modes){ $v=& $m; if($v -ne $null -and $v -gt 0 -and $v -lt 125){ $pick=$m; break } }
while($true){
  $t=$null
  if($pick){ $t=& $pick }
  if($t -ne $null -and $t -gt 0 -and $t -lt 125){ [Console]::Out.WriteLine([math]::Round($t,1)) } else { [Console]::Out.WriteLine('NA') }
  [Console]::Out.Flush()
  Start-Sleep -Seconds 10
}
"""


class Monitor:
    """Samples CPU/RAM/GPU every `interval` seconds on a daemon thread."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu = {"name": None, "temp": None, "load": None}
        self._cpu_temp: float | None = None
        self._smi: subprocess.Popen | None = None
        self._ps: subprocess.Popen | None = None
        self._fps = DwmFps()
        self.cpu_name = get_cpu_name()
        self.snapshot = Snapshot(cpu_name=self.cpu_name)
        self.sample_fps = False  # only sample DWM when the FPS overlay is on

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        psutil.cpu_percent(None)  # prime
        self._start_nvidia_smi()
        self._start_cpu_temp()
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

    def get(self) -> Snapshot:
        with self._lock:
            return self.snapshot

    # -- nvidia-smi (single streaming process) ------------------------------ #
    def _start_nvidia_smi(self):
        try:
            self._smi = _popen(
                [
                    "nvidia-smi",
                    "--query-gpu=name,temperature.gpu,utilization.gpu",
                    "--format=csv,noheader,nounits",
                    "-lms",
                    str(int(self.interval * 1000)),
                ]
            )
            threading.Thread(target=self._read_smi, name="banko-smi", daemon=True).start()
        except Exception:
            self._smi = None

    def _read_smi(self):
        assert self._smi and self._smi.stdout
        for line in self._smi.stdout:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                temp = float(parts[1])
            except ValueError:
                temp = None
            try:
                load = float(parts[2])
            except ValueError:
                load = None
            with self._lock:
                # keep the last good reading if a sample is transiently "[N/A]"
                self._gpu = {
                    "name": parts[0],
                    "temp": temp if temp is not None else self._gpu["temp"],
                    "load": load if load is not None else self._gpu["load"],
                }

    # -- CPU temperature (single streaming PowerShell) ---------------------- #
    def _start_cpu_temp(self):
        try:
            self._ps = _popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-Command", _PS_TEMP_LOOP]
            )
            threading.Thread(target=self._read_ps, name="banko-cputemp", daemon=True).start()
        except Exception:
            self._ps = None

    def _read_ps(self):
        assert self._ps and self._ps.stdout
        for line in self._ps.stdout:
            line = line.strip()
            try:
                v = float(line)
            except ValueError:
                v = None
            with self._lock:
                self._cpu_temp = v

    # -- main loop ----------------------------------------------------------- #
    def _run(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
            vm = psutil.virtual_memory()
            fps = self._fps.sample() if self.sample_fps else None
            with self._lock:
                snap = Snapshot(
                    gpu_name=self._gpu["name"] or DEFAULT_GPU_NAME,
                    gpu_temp=self._gpu["temp"],
                    gpu_load=self._gpu["load"],
                    cpu_name=self.cpu_name,
                    cpu_load=psutil.cpu_percent(None),
                    cpu_temp=self._cpu_temp,
                    ram_used_gb=vm.used / 2**30,
                    ram_total_gb=vm.total / 2**30,
                    ram_percent=vm.percent,
                    fps=fps,
                )
                self.snapshot = snap
            next_t += self.interval
            delay = next_t - time.perf_counter()
            if delay < 0:
                next_t = time.perf_counter()
                delay = self.interval
            self._stop.wait(delay)


# --------------------------------------------------------------------------- #
# BOOST SYSTEM
# --------------------------------------------------------------------------- #
def _empty_working_sets() -> int:
    """Trim the working set of every process we may touch (frees RAM to standby)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_LIMITED = 0x1000
    count = 0
    for pid in psutil.pids():
        if pid in (0, 4):
            continue
        h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED, False, pid)
        if h:
            try:
                if psapi.EmptyWorkingSet(h):
                    count += 1
            finally:
                k32.CloseHandle(h)
    return count


def _purge_standby_list() -> bool:
    """Flush the standby list. Needs admin (SeProfileSingleProcessPrivilege)."""
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")

        class LUID(ctypes.Structure):
            _fields_ = [("Low", wintypes.DWORD), ("High", wintypes.LONG)]

        class TP(ctypes.Structure):
            _fields_ = [("Count", wintypes.DWORD), ("Luid", LUID), ("Attr", wintypes.DWORD)]

        token = wintypes.HANDLE()
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x28, ctypes.byref(token)):
            return False
        luid = LUID()
        if not adv.LookupPrivilegeValueW(None, "SeProfileSingleProcessPrivilege", ctypes.byref(luid)):
            return False
        tp = TP(1, luid, 2)
        adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        if ctypes.get_last_error() != 0:  # ERROR_NOT_ALL_ASSIGNED -> not elevated
            return False
        cmd = ctypes.c_int(4)  # MemoryPurgeStandbyList
        # SystemMemoryListInformation = 80
        return ntdll.NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd)) == 0
    except Exception:
        return False


def _clean_temp(min_age_s: int = 3600) -> int:
    """Delete user temp files older than an hour. Returns bytes freed."""
    freed = 0
    cutoff = time.time() - min_age_s
    folder = os.environ.get("TEMP") or os.environ.get("TMP")
    if not folder or not os.path.isdir(folder):
        return 0
    for root, dirs, files in os.walk(folder, topdown=False):
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
                if st.st_mtime < cutoff:
                    os.remove(p)
                    freed += st.st_size
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))  # only succeeds when empty
            except OSError:
                pass
    return freed


def _set_high_performance() -> bool:
    r = _run(["powercfg", "/setactive", HIGH_PERF_GUID])
    if r.returncode == 0:
        return True
    # High Performance plan hidden (modern standby laptops) -> recreate it.
    r = _run(["powercfg", "/duplicatescheme", HIGH_PERF_GUID])
    m = re.search(r"([0-9a-fA-F-]{36})", r.stdout or "")
    if r.returncode == 0 and m:
        return _run(["powercfg", "/setactive", m.group(1)]).returncode == 0
    return False


def boost_system() -> str:
    """Run all boost steps; returns a short human readable summary."""
    before = psutil.virtual_memory().available
    trimmed = _empty_working_sets()
    standby = _purge_standby_list()
    temp_freed = _clean_temp()
    plan = _set_high_performance()
    time.sleep(0.4)
    gained = max(0, psutil.virtual_memory().available - before)
    parts = [f"RAM +{gained / 2**20:.0f} MB"]
    if temp_freed:
        parts.append(f"temp -{temp_freed / 2**20:.0f} MB")
    parts.append("High Performance ON" if plan else "power plan unchanged")
    if not standby:
        parts.append("(run as admin to flush standby list)")
    return " | ".join(parts)
