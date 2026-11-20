"""BANKO telemetry + universal Windows system tuning.

Designed for ultra-low background overhead on any Windows 10/11 PC or laptop:
  * one background thread, one sample per second
  * NVIDIA GPUs stream via a single persistent `nvidia-smi -lms 1000` pipe
  * AMD / Intel / non-NVIDIA GPUs fall back gracefully to WMI / registry
    identification and Windows GPU Engine performance counters
  * CPU load, core/thread counts, frequency, and RAM come from `psutil`
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

CREATE_NO_WINDOW = 0x08000000
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE

DEFAULT_GPU_NAME = "Windows Display Adapter"
ULTIMATE_PERF_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"

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
_SKIP_GAME_EXES = (
    "unins",
    "unitycrashhandler",
    "crashreport",
    "vc_redist",
    "vcredist",
    "dxsetup",
    "setup",
    "redist",
    "ue4prereq",
    "ueprereq",
    "easyanticheat_setup",
    "battleye",
    "launcher_helper",
)


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
# static hardware detection (brand-agnostic)
# --------------------------------------------------------------------------- #
def get_system_name() -> str:
    """Detect PC manufacturer and model across HP, Dell, Lenovo, ASUS, Acer, MSI, and custom builds."""
    mfg = ""
    model = ""

    # 1) Fast registry lookup (instant, no subprocess)
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

    # 2) WMI fallback via wmic or PowerShell CIM
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
        if model.lower().startswith(mfg.lower()):
            return model
        return f"{mfg} {model}"
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


def get_gpu_name_fallback() -> str:
    """Identify the primary physical GPU via Windows registry or Win32_VideoController."""
    candidates: list[str] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    for idx in range(10):
        sub = f"{base}\\{idx:04d}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                for val in ("DriverDesc", "HardwareInformation.AdapterString"):
                    try:
                        raw = winreg.QueryValueEx(k, val)[0]
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
                        name = _clean_text(str(raw))
                        if name and name not in candidates:
                            candidates.append(name)
                            break
                    except OSError:
                        pass
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
                    "(Get-CimInstance Win32_VideoController).Name",
                ],
                timeout=8,
            )
            if r.returncode == 0:
                for line in (r.stdout or "").splitlines():
                    name = _clean_text(line)
                    if name and name not in candidates:
                        candidates.append(name)
        except Exception:
            pass

    valid = [c for c in candidates if _gpu_rank(c) >= 0]
    if not valid:
        return DEFAULT_GPU_NAME
    valid.sort(key=_gpu_rank, reverse=True)
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
# telemetry
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    system_name: str = "Windows PC"
    gpu_name: str = DEFAULT_GPU_NAME
    gpu_temp: float | None = None
    gpu_load: float | None = None
    cpu_name: str = "CPU"
    cpu_load: float = 0.0
    cpu_temp: float | None = None
    cpu_cores: int = 1
    cpu_threads: int = 1
    cpu_freq_ghz: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_percent: float = 0.0
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
  $ctS=if($ct -ne $null -and $ct -gt 0 -and $ct -lt 125){[math]::Round($ct,1)}else{'NA'}
  $glS=if($gl -ne $null){$gl}else{'NA'}
  $gtS=if($gt -ne $null -and $gt -gt 0 -and $gt -lt 125){[math]::Round($gt,1)}else{'NA'}
  [Console]::Out.WriteLine("$ctS|$glS|$gtS")
  [Console]::Out.Flush()
  Start-Sleep -Seconds 5
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
        self._ps_gpu_load: float | None = None
        self._ps_gpu_temp: float | None = None
        self._smi: subprocess.Popen | None = None
        self._ps: subprocess.Popen | None = None
        self._fps = DwmFps()
        self.system_name = get_system_name()
        self.cpu_name = get_cpu_name()
        self.cpu_cores = psutil.cpu_count(logical=False) or 1
        self.cpu_threads = psutil.cpu_count(logical=True) or 1
        self.fallback_gpu_name = get_gpu_name_fallback()
        self.snapshot = Snapshot(
            system_name=self.system_name,
            gpu_name=self.fallback_gpu_name,
            cpu_name=self.cpu_name,
            cpu_cores=self.cpu_cores,
            cpu_threads=self.cpu_threads,
        )
        self.sample_fps = False

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        psutil.cpu_percent(None)
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

    def get(self) -> Snapshot:
        with self._lock:
            return self.snapshot

    # -- nvidia-smi (optional, single streaming process) -------------------- #
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
        if not self._smi or not self._smi.stdout:
            return
        try:
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
                    self._gpu = {
                        "name": parts[0] or self._gpu["name"],
                        "temp": temp if temp is not None else self._gpu["temp"],
                        "load": load if load is not None else self._gpu["load"],
                    }
        except Exception:
            pass

    # -- Universal PowerShell sensor/counter loop --------------------------- #
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

    @staticmethod
    def _parse_opt_float(token: str) -> float | None:
        try:
            return float(token.strip())
        except ValueError:
            return None

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
                with self._lock:
                    self._cpu_temp = ct
                    if gl is not None:
                        self._ps_gpu_load = gl
                    if gt is not None:
                        self._ps_gpu_temp = gt
        except Exception:
            pass

    # -- main loop ----------------------------------------------------------- #
    def _run(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
            vm = psutil.virtual_memory()
            fps = self._fps.sample() if self.sample_fps else None
            freq_ghz = 0.0
            try:
                cf = psutil.cpu_freq()
                if cf and cf.current:
                    freq_ghz = cf.current / 1000.0 if cf.current > 10 else cf.current
            except Exception:
                pass
            with self._lock:
                gpu_load = self._gpu["load"] if self._gpu["load"] is not None else self._ps_gpu_load
                gpu_temp = (
                    self._gpu["temp"]
                    if self._gpu["temp"] is not None
                    else (self._ps_gpu_temp if self._ps_gpu_temp is not None else self._cpu_temp)
                )
                snap = Snapshot(
                    system_name=self.system_name,
                    gpu_name=self._gpu["name"] or self.fallback_gpu_name,
                    gpu_temp=gpu_temp,
                    gpu_load=gpu_load,
                    cpu_name=self.cpu_name,
                    cpu_load=psutil.cpu_percent(None),
                    cpu_temp=self._cpu_temp,
                    cpu_cores=self.cpu_cores,
                    cpu_threads=self.cpu_threads,
                    cpu_freq_ghz=freq_ghz,
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
# UNIVERSAL BOOST SYSTEM
# --------------------------------------------------------------------------- #
def _close_background_bloat() -> int:
    """Safely terminate known idle background bloat processes without touching user apps."""
    closed = 0
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in _BLOAT_PROCESSES:
                proc.terminate()
                closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return closed


def _empty_working_sets() -> int:
    """Trim the working set of accessible processes to reclaim standby RAM."""
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
    """Flush the Windows standby memory list (requires Administrator elevation)."""
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
        if ctypes.get_last_error() != 0:
            return False
        cmd = ctypes.c_int(4)  # MemoryPurgeStandbyList
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
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    return freed


def _set_high_performance() -> str:
    """Switch Windows Power Plan to Ultimate or High Performance without creating duplicates."""
    existing: dict[str, str] = {}
    try:
        listed = _run(["powercfg", "/list"])
        for line in (listed.stdout or "").splitlines():
            m = re.search(r"([0-9a-fA-F-]{36})\s+\(([^)]+)\)", line)
            if m:
                existing[m.group(2).strip().lower()] = m.group(1).lower()
    except Exception:
        pass

    for label, guid in (
        ("Ultimate Perf", existing.get("ultimate performance") or ULTIMATE_PERF_GUID),
        ("High Perf", existing.get("high performance") or HIGH_PERF_GUID),
    ):
        try:
            if _run(["powercfg", "/setactive", guid]).returncode == 0:
                return label
        except Exception:
            pass

    for label, guid in (("Ultimate Perf", ULTIMATE_PERF_GUID), ("High Perf", HIGH_PERF_GUID)):
        try:
            dup = _run(["powercfg", "/duplicatescheme", guid])
            m = re.search(r"([0-9a-fA-F-]{36})", dup.stdout or "")
            if dup.returncode == 0 and m:
                if _run(["powercfg", "/setactive", m.group(1)]).returncode == 0:
                    return label
        except Exception:
            pass
    return "Power unchanged"


def _optimize_gpu_preferences() -> int:
    """Enable DirectX windowed optimizations and High Performance GPU for detected games."""
    configured = 0
    try:
        key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            try:
                cur_global = str(winreg.QueryValueEx(k, "DirectXUserGlobalSettings")[0])
            except OSError:
                cur_global = ""
            if "SwapEffectUpgradeEnable=" not in cur_global:
                new_global = (cur_global.rstrip(";") + ";SwapEffectUpgradeEnable=1;").lstrip(";")
                winreg.SetValueEx(k, "DirectXUserGlobalSettings", 0, winreg.REG_SZ, new_global)

            roots = [
                r"C:\games",
                r"C:\Games",
                r"C:\Program Files (x86)\Steam\steamapps\common",
                r"C:\Program Files\Epic Games",
                r"D:\Games",
                r"D:\SteamLibrary\steamapps\common",
            ]
            seen: set[str] = set()
            for base in roots:
                if not os.path.isdir(base):
                    continue
                base_depth = base.rstrip("\\/").count(os.sep)
                for root, dirs, files in os.walk(base):
                    if root.count(os.sep) - base_depth >= 3:
                        dirs[:] = []
                    for fname in files:
                        low = fname.lower()
                        if not low.endswith(".exe"):
                            continue
                        if any(skip in low for skip in _SKIP_GAME_EXES):
                            continue
                        full = os.path.join(root, fname)
                        if full.lower() in seen:
                            continue
                        seen.add(full.lower())
                        winreg.SetValueEx(k, full, 0, winreg.REG_SZ, "GpuPreference=2;")
                        configured += 1
                        if configured >= 25:
                            return configured
    except OSError:
        pass
    return configured


def _optimize_network() -> bool:
    """Flush DNS cache and apply standard TCP auto-tuning."""
    flushed = False
    try:
        flushed = _run(["ipconfig", "/flushdns"], timeout=10).returncode == 0
    except Exception:
        pass
    try:
        _run(["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"], timeout=10)
    except Exception:
        pass
    return flushed


def boost_system() -> str:
    """Run all universal Windows optimization steps and return a compact summary."""
    before = psutil.virtual_memory().available
    closed = _close_background_bloat()
    _empty_working_sets()
    _purge_standby_list()
    _clean_temp()
    plan_label = _set_high_performance()
    _optimize_gpu_preferences()
    dns_ok = _optimize_network()
    time.sleep(0.3)
    gained = max(0, psutil.virtual_memory().available - before)

    parts = [f"RAM +{gained / 2**20:.0f} MB"]
    if closed:
        parts.append(f"Bloat -{closed}")
    parts.append(plan_label)
    parts.append("GPU + DNS Tuned" if dns_ok else "GPU Pref Tuned")
    return " | ".join(parts)
