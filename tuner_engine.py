"""BANKO v3.0.0 overclocking, safe profiles, 82°C/86°C thermal watchdog, power mode, and system booster engine."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass

import psutil

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

GPU_CLOCK_MIN_MHZ = 800
GPU_CLOCK_MAX_MHZ = 1850
GPU_CLOCK_STEP_MHZ = 15

CPU_FREQ_MIN_GHZ = 1.80
CPU_FREQ_MAX_GHZ = 4.50
CPU_FREQ_STEP_GHZ = 0.10

THERMAL_WATCHDOG_SAFE_C = 82.0
THERMAL_WATCHDOG_MAX_C = 86.0
THERMAL_WATCHDOG_LIMIT_C = 86.0

SAFE_OC_PROFILES: dict[str, dict[str, object]] = {
    "ECO": {
        "gpu_mhz": 1055,
        "cpu_ghz": 2.40,
        "power_mode": "BALANCED",
        "watchdog_c": THERMAL_WATCHDOG_SAFE_C,
        "desc": "Cool & quiet efficiency cap (1055 MHz GPU / 2.40 GHz CPU / 82°C cutoff)",
    },
    "BALANCED": {
        "gpu_mhz": None,
        "cpu_ghz": None,
        "power_mode": "BALANCED",
        "watchdog_c": THERMAL_WATCHDOG_SAFE_C,
        "desc": "Stock dynamic clocks & Windows Balanced power (82°C cutoff)",
    },
    "MAX SAFE OC": {
        "gpu_mhz": 1700,
        "cpu_ghz": 4.20,
        "power_mode": "AC_TURBO",
        "watchdog_c": THERMAL_WATCHDOG_MAX_C,
        "desc": "Maximum safe AC gaming lock (up to 1700 MHz GPU / 4.20 GHz CPU / 86°C cutoff)",
    },
}

SUB_PROCESSOR = "SUB_PROCESSOR"
PROCFREQMAX = "PROCFREQMAX"
PROCTHROTTLEMIN = "PROCTHROTTLEMIN"
PROCTHROTTLEMAX = "PROCTHROTTLEMAX"
PERFBOOSTMODE = "PERFBOOSTMODE"
SYSCOOLPOL = "SYSCOOLPOL"
CPMINCORES = "0cc5b647-c1df-4637-891a-dec35c318583"

SUB_PCIEXPRESS = "501a4d13-42af-4429-9fd1-a8218c268e20"
ASPM_GUID = "ee12f906-d277-404b-b6da-e5fa1a576df5"

ULTIMATE_PERF_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"

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


def is_admin() -> bool:
    """Return True if process runs with Administrator UAC elevation."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def snap_gpu_clock_mhz(mhz: float | int) -> int:
    """Clamp GPU clock to [800, 1850] MHz and snap to 15 MHz increments."""
    clamped = max(float(GPU_CLOCK_MIN_MHZ), min(float(GPU_CLOCK_MAX_MHZ), float(mhz)))
    steps = round((clamped - GPU_CLOCK_MIN_MHZ) / GPU_CLOCK_STEP_MHZ)
    return int(max(GPU_CLOCK_MIN_MHZ, min(GPU_CLOCK_MAX_MHZ, GPU_CLOCK_MIN_MHZ + steps * GPU_CLOCK_STEP_MHZ)))


def snap_cpu_freq_ghz(ghz: float) -> float:
    """Clamp CPU max frequency to [1.80, 4.50] GHz and snap to 0.10 GHz increments."""
    clamped = max(CPU_FREQ_MIN_GHZ, min(CPU_FREQ_MAX_GHZ, float(ghz)))
    steps = round((clamped - CPU_FREQ_MIN_GHZ) / CPU_FREQ_STEP_GHZ)
    return max(CPU_FREQ_MIN_GHZ, min(CPU_FREQ_MAX_GHZ, round(CPU_FREQ_MIN_GHZ + steps * CPU_FREQ_STEP_GHZ, 2)))


def ghz_to_mhz(ghz: float) -> int:
    """Convert snapped GHz float into integer MHz for powercfg PROCFREQMAX."""
    return int(round(snap_cpu_freq_ghz(ghz) * 1000.0))


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for path in (
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ):
        if os.path.isfile(path):
            return path
    return None


def _run_hidden(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except Exception as exc:
        return -1, "", str(exc)


# --------------------------------------------------------------------------- #
# Windows Power Plan & AC Turbo Mode
# --------------------------------------------------------------------------- #
def read_active_power_plan() -> tuple[str, str]:
    """Return (scheme_guid, UPPERCASE_NAME) for active Windows power plan."""
    rc, out, _ = _run_hidden(["powercfg", "/getactivescheme"], timeout=4.0)
    if rc == 0 and out:
        m = re.search(r"Power Scheme GUID:\s*([0-9a-fA-F-]{36})\s*\(([^)]+)\)", out)
        if m:
            return m.group(1).strip().lower(), m.group(2).strip().upper()
    return "", "UNKNOWN"


def _ensure_and_activate_plan(target_guids: list[tuple[str, str]]) -> str | None:
    existing: dict[str, str] = {}
    rc, out, _ = _run_hidden(["powercfg", "/list"], timeout=5.0)
    if rc == 0 and out:
        for line in out.splitlines():
            m = re.search(r"([0-9a-fA-F-]{36})\s+\(([^)]+)\)", line)
            if m:
                existing[m.group(2).strip().lower()] = m.group(1).lower()

    for label, guid in target_guids:
        cand = existing.get(label.lower()) or guid
        if _run_hidden(["powercfg", "/setactive", cand], timeout=5.0)[0] == 0:
            return label

    for label, guid in target_guids:
        rc_d, out_d, _ = _run_hidden(["powercfg", "/duplicatescheme", guid], timeout=5.0)
        m = re.search(r"([0-9a-fA-F-]{36})", out_d or "")
        if rc_d == 0 and m:
            if _run_hidden(["powercfg", "/setactive", m.group(1)], timeout=5.0)[0] == 0:
                return label
    return None


def activate_ac_turbo_power_mode() -> tuple[bool, str]:
    """Force Ultimate/High Performance plan + 100% AC CPU state + Aggressive Boost."""
    plan = _ensure_and_activate_plan(
        [("Ultimate Performance", ULTIMATE_PERF_GUID), ("High Performance", HIGH_PERF_GUID)]
    )
    _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCTHROTTLEMIN, "100"])
    _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCTHROTTLEMAX, "100"])
    _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PERFBOOSTMODE, "2"])
    _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, SYSCOOLPOL, "1"])
    rc_act, _, err_act = _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
    if rc_act == 0:
        return True, f"AC TURBO ACTIVE ({(plan or 'HIGH PERFORMANCE').upper()} | 100% MIN/MAX)"
    return False, f"AC Turbo failed: {err_act or 'powercfg error'}"


def restore_balanced_power_mode() -> tuple[bool, str]:
    """Restore Windows Balanced power scheme."""
    plan = _ensure_and_activate_plan([("Balanced", BALANCED_GUID)])
    if plan:
        return True, "POWER PLAN RESTORED: BALANCED"
    return False, "Balanced scheme not found on system"


# --------------------------------------------------------------------------- #
# GPU MHz & CPU GHz Overclocking + 86C Watchdog
# --------------------------------------------------------------------------- #
def lock_gpu_clock(mhz: int | float) -> tuple[bool, str, int]:
    target = snap_gpu_clock_mhz(mhz)
    smi = find_nvidia_smi()
    if not smi:
        return False, "nvidia-smi not found on system", target
    rc, out, err = _run_hidden([smi, f"--lock-gpu-clocks={target},{target}"], timeout=6.0)
    if rc == 0:
        return True, f"GPU CLOCK LOCKED AT {target} MHz", target
    return False, f"GPU lock failed: {err or out or f'exit {rc}'}", target


def reset_gpu_clock() -> tuple[bool, str]:
    smi = find_nvidia_smi()
    if not smi:
        return False, "nvidia-smi not found on system"
    rc, out, err = _run_hidden([smi, "--reset-gpu-clocks"], timeout=6.0)
    if rc == 0:
        return True, "GPU CLOCK RESTORED TO STOCK"
    return False, f"GPU reset failed: {err or out or f'exit {rc}'}"


def set_cpu_max_freq_ghz(ghz: float) -> tuple[bool, str, float, int]:
    snapped = snap_cpu_freq_ghz(ghz)
    mhz = ghz_to_mhz(snapped)
    s_mhz = str(mhz)
    rc1, o1, e1 = _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCFREQMAX, s_mhz])
    rc2, o2, e2 = _run_hidden(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCFREQMAX, s_mhz])
    rc3, o3, e3 = _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
    if rc1 == 0 and rc2 == 0 and rc3 == 0:
        return True, f"CPU MAX FREQ SET TO {snapped:.2f} GHz ({mhz} MHz)", snapped, mhz
    return False, f"PROCFREQMAX failed: {e1 or o1 or e2 or o2 or e3 or o3}", snapped, mhz


def reset_cpu_max_freq() -> tuple[bool, str]:
    rc1, o1, e1 = _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCFREQMAX, "0"])
    rc2, o2, e2 = _run_hidden(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCFREQMAX, "0"])
    rc3, o3, e3 = _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
    if rc1 == 0 and rc2 == 0 and rc3 == 0:
        return True, "CPU FREQUENCY RESTORED TO TURBO AUTO (0 MHz)"
    return False, f"PROCFREQMAX reset failed: {e1 or o1 or e2 or o2 or e3 or o3}"


def read_cpu_target_freq_mhz() -> int | None:
    rc, out, _ = _run_hidden(["powercfg", "/qh", "SCHEME_CURRENT", SUB_PROCESSOR, PROCFREQMAX], timeout=5.0)
    if rc != 0 or not out:
        return None
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None


@dataclass
class ClockControlState:
    target_gpu_mhz: int | None
    target_cpu_mhz: int
    target_cpu_ghz: float | None
    nvidia_available: bool
    watchdog_tripped: bool
    watchdog_limit_c: float = THERMAL_WATCHDOG_MAX_C
    active_profile: str = "STOCK"


class ClockController:
    """Thread-safe GPU MHz / CPU GHz overclocking, safe profiles & 82°C/86°C watchdog guardian."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.nvidia_available = find_nvidia_smi() is not None
        self._target_gpu_mhz: int | None = None
        initial_mhz = read_cpu_target_freq_mhz()
        self._target_cpu_mhz = initial_mhz if initial_mhz is not None else 0
        self.watchdog_tripped = False
        self._watchdog_limit_c: float = THERMAL_WATCHDOG_MAX_C
        self._active_profile: str = "STOCK"

    def set_watchdog_limit(self, limit_c: float) -> tuple[bool, str]:
        """Set thermal auto-cutoff threshold to 82°C (SAFE) or 86°C (MAX)."""
        chosen = THERMAL_WATCHDOG_SAFE_C if float(limit_c) <= 84.0 else THERMAL_WATCHDOG_MAX_C
        with self._lock:
            self._watchdog_limit_c = chosen
            self.watchdog_tripped = False
        tag = "SAFE" if chosen == THERMAL_WATCHDOG_SAFE_C else "MAX"
        return True, f"THERMAL WATCHDOG CUTOFF SET TO {chosen:.0f}°C ({tag})"

    def apply_gpu_clock(self, mhz: int | float) -> tuple[bool, str]:
        ok, msg, applied = lock_gpu_clock(mhz)
        if ok:
            with self._lock:
                self._target_gpu_mhz = applied
                self.watchdog_tripped = False
                self._active_profile = "CUSTOM"
        return ok, msg

    def reset_gpu(self) -> tuple[bool, str]:
        ok, msg = reset_gpu_clock()
        if ok:
            with self._lock:
                self._target_gpu_mhz = None
                if self._target_cpu_mhz == 0:
                    self._active_profile = "STOCK"
        return ok, msg

    def apply_cpu_freq(self, ghz: float) -> tuple[bool, str]:
        ok, msg, _ghz, applied_mhz = set_cpu_max_freq_ghz(ghz)
        if ok:
            with self._lock:
                self._target_cpu_mhz = applied_mhz
                self._active_profile = "CUSTOM"
        return ok, msg

    def reset_cpu(self) -> tuple[bool, str]:
        ok, msg = reset_cpu_max_freq()
        if ok:
            with self._lock:
                self._target_cpu_mhz = 0
                if self._target_gpu_mhz is None:
                    self._active_profile = "STOCK"
        return ok, msg

    def apply_profile(
        self, profile_name: str, detected_max_gpu_mhz: float | None = None
    ) -> tuple[bool, str]:
        """Apply ECO, BALANCED, or MAX SAFE OC preset safely."""
        key = profile_name.strip().upper()
        spec = SAFE_OC_PROFILES.get(key)
        if not spec:
            return False, f"Unknown OC profile: {profile_name}"

        watchdog_c = float(spec["watchdog_c"])  # type: ignore[arg-type]
        self.set_watchdog_limit(watchdog_c)

        parts: list[str] = []
        overall_ok = True

        # 1) GPU Clock
        gpu_target = spec["gpu_mhz"]
        if self.nvidia_available:
            if gpu_target is None:
                ok_g, _ = reset_gpu_clock()
                with self._lock:
                    if ok_g:
                        self._target_gpu_mhz = None
                parts.append("GPU STOCK" if ok_g else "GPU RESET ERR")
                overall_ok = overall_ok and ok_g
            else:
                target_mhz = int(gpu_target)  # type: ignore[arg-type]
                if (
                    key == "MAX SAFE OC"
                    and detected_max_gpu_mhz is not None
                    and float(detected_max_gpu_mhz) >= GPU_CLOCK_MIN_MHZ
                ):
                    target_mhz = min(target_mhz, snap_gpu_clock_mhz(detected_max_gpu_mhz))
                ok_g, _, applied_g = lock_gpu_clock(target_mhz)
                with self._lock:
                    if ok_g:
                        self._target_gpu_mhz = applied_g
                        self.watchdog_tripped = False
                parts.append(f"GPU {applied_g} MHz" if ok_g else "GPU LOCK ERR")
                overall_ok = overall_ok and ok_g
        else:
            parts.append("GPU N/A")

        # 2) CPU Frequency
        cpu_target = spec["cpu_ghz"]
        if cpu_target is None:
            ok_c, _ = reset_cpu_max_freq()
            with self._lock:
                if ok_c:
                    self._target_cpu_mhz = 0
            parts.append("CPU TURBO AUTO" if ok_c else "CPU RESET ERR")
            overall_ok = overall_ok and ok_c
        else:
            ok_c, _, snapped_ghz, applied_cpu_mhz = set_cpu_max_freq_ghz(float(cpu_target))  # type: ignore[arg-type]
            with self._lock:
                if ok_c:
                    self._target_cpu_mhz = applied_cpu_mhz
            parts.append(f"CPU {snapped_ghz:.2f} GHz" if ok_c else "CPU CAP ERR")
            overall_ok = overall_ok and ok_c

        # 3) Power Mode
        if spec["power_mode"] == "AC_TURBO":
            ok_p, _ = activate_ac_turbo_power_mode()
            parts.append("AC TURBO" if ok_p else "PWR ERR")
        else:
            ok_p, _ = restore_balanced_power_mode()
            parts.append("BALANCED PWR" if ok_p else "PWR ERR")

        with self._lock:
            self._active_profile = key
        parts.append(f"WD {watchdog_c:.0f}°C")
        return overall_ok, f"PROFILE [{key}] APPLIED: " + " | ".join(parts)

    def reset_to_stock(self) -> tuple[bool, str]:
        """Reset GPU clocks, CPU PROCFREQMAX, and Windows power plan back to stock defaults."""
        notes: list[str] = []
        ok_all = True
        if self.nvidia_available:
            ok_g, msg_g = self.reset_gpu()
            notes.append("GPU STOCK" if ok_g else msg_g)
            ok_all = ok_all and ok_g
        ok_c, msg_c = self.reset_cpu()
        notes.append("CPU TURBO AUTO" if ok_c else msg_c)
        ok_all = ok_all and ok_c
        ok_p, msg_p = restore_balanced_power_mode()
        notes.append("BALANCED PLAN" if ok_p else msg_p)
        with self._lock:
            self._active_profile = "STOCK"
            self.watchdog_tripped = False
        return ok_all, "RESET TO STOCK COMPLETE (" + " | ".join(notes) + ")"

    def sync_cpu_target(self) -> int:
        mhz = read_cpu_target_freq_mhz()
        with self._lock:
            if mhz is not None:
                self._target_cpu_mhz = mhz
            return self._target_cpu_mhz

    def check_thermal_watchdog(self, gpu_temp_c: float | None) -> tuple[bool, str]:
        """Auto-reset locked GPU clocks to stock if GPU reaches >= active watchdog limit (82°C or 86°C)."""
        with self._lock:
            locked = self._target_gpu_mhz is not None
            limit = self._watchdog_limit_c
        if not locked or gpu_temp_c is None or gpu_temp_c < limit:
            return False, ""

        ok, reset_msg = reset_gpu_clock()
        with self._lock:
            if ok:
                self._target_gpu_mhz = None
                if self._target_cpu_mhz == 0:
                    self._active_profile = "STOCK"
            self.watchdog_tripped = True
        return True, f"THERMAL WATCHDOG TRIPPED AT {gpu_temp_c:.0f}°C (>={limit:.0f}°C) — {reset_msg}"

    def get_state(self) -> ClockControlState:
        with self._lock:
            t_gpu = self._target_gpu_mhz
            t_cpu = self._target_cpu_mhz
            tripped = self.watchdog_tripped
            wd_limit = self._watchdog_limit_c
            profile = self._active_profile
        return ClockControlState(
            target_gpu_mhz=t_gpu,
            target_cpu_mhz=t_cpu,
            target_cpu_ghz=round(t_cpu / 1000.0, 2) if t_cpu > 0 else None,
            nvidia_available=self.nvidia_available,
            watchdog_tripped=tripped,
            watchdog_limit_c=wd_limit,
            active_profile=profile,
        )


# --------------------------------------------------------------------------- #
# 4 Dedicated Individual System Boost Modules
# --------------------------------------------------------------------------- #
def _empty_working_sets() -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_LIMITED = 0x1000
    count = 0
    for pid in psutil.pids():
        if pid in (0, 4):
            continue
        handle = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED, False, pid)
        if handle:
            try:
                if psapi.EmptyWorkingSet(handle):
                    count += 1
            finally:
                k32.CloseHandle(handle)
    return count


def _purge_standby_list() -> bool:
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
            k32.CloseHandle(token)
            return False
        tp = TP(1, luid, 2)
        adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        err = ctypes.get_last_error()
        k32.CloseHandle(token)
        if err != 0:
            return False
        cmd = ctypes.c_int(4)  # MemoryPurgeStandbyList
        return ntdll.NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd)) == 0
    except Exception:
        return False


def _clean_directory_files(folder: str, min_age_s: int = 120) -> int:
    freed = 0
    if not folder or not os.path.isdir(folder):
        return 0
    cutoff = time.time() - min_age_s
    for root, dirs, files in os.walk(folder, topdown=False):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                st = os.stat(fpath)
                if st.st_mtime <= cutoff:
                    os.remove(fpath)
                    freed += st.st_size
            except OSError:
                pass
        for dname in dirs:
            try:
                os.rmdir(os.path.join(root, dname))
            except OSError:
                pass
    return freed


def _apply_directx_high_perf() -> int:
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
                depth0 = base.rstrip("\\/").count(os.sep)
                for root, dirs, files in os.walk(base):
                    if root.count(os.sep) - depth0 >= 3:
                        dirs[:] = []
                    for fname in files:
                        low = fname.lower()
                        if not low.endswith(".exe") or any(s in low for s in _SKIP_GAME_EXES):
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


class SystemBoosters:
    """Four dedicated individual system boost modules with live status tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ram_active = False
        self.gpu_active = False
        self.cpu_active = False
        self.os_active = False

        self.ram_summary = "STANDBY // WORKING SET & CACHE READY"
        self.gpu_summary = "STANDBY // P0 + DIRECTX SCHEDULING READY"
        self.cpu_summary = "STANDBY // CORE UNPARK + 100% AC READY"
        self.os_summary = "STANDBY // SERVICES + SHADER CACHE READY"
        self._stopped_services: list[str] = []

    def active_count(self) -> int:
        with self._lock:
            return sum([self.ram_active, self.gpu_active, self.cpu_active, self.os_active])

    def activate_ram_boost(self) -> tuple[bool, str]:
        before = psutil.virtual_memory().available
        trimmed = _empty_working_sets()
        standby_ok = _purge_standby_list()
        time.sleep(0.25)
        after = psutil.virtual_memory().available
        freed_mb = max(0.0, (after - before) / (1024 * 1024))
        standby_tag = "STANDBY FLUSHED" if standby_ok else "WORKING SETS TRIMMED"
        summary = f"FREED {freed_mb:.0f} MB | {trimmed} PROCS | {standby_tag}"
        with self._lock:
            self.ram_active = True
            self.ram_summary = summary
        return True, f"RAM BOOST: {summary}"

    def toggle_gpu_boost(self, enable: bool | None = None) -> tuple[bool, str]:
        with self._lock:
            target = (not self.gpu_active) if enable is None else enable

        if target:
            games = _apply_directx_high_perf()
            # Disable PCIe Link State Power Management (ASPM = 0 = Off) on AC
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PCIEXPRESS, ASPM_GUID, "0"])
            _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])

            # Enable Hardware-Accelerated GPU Scheduling (HwSchMode=2) when elevated
            hags_set = False
            try:
                with winreg.CreateKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                ) as k:
                    winreg.SetValueEx(k, "HwSchMode", 0, winreg.REG_DWORD, 2)
                    hags_set = True
            except OSError:
                pass

            # Attempt NVIDIA persistence / P0 check gracefully
            smi = find_nvidia_smi()
            nv_note = "DX HIGH-PERF"
            if smi:
                rc_pm, _, _ = _run_hidden([smi, "-pm", "1"], timeout=3.0)
                nv_note = "P0 + PM ENABLED" if rc_pm == 0 else "WDDM MAX PERF"

            extra = f"{games} GAMES" if games > 0 else ("HAGS ON" if hags_set else "ASPM OFF")
            summary = f"ACTIVE // {nv_note} | {extra}"
            with self._lock:
                self.gpu_active = True
                self.gpu_summary = summary
            return True, f"GPU BOOST: {summary}"

        _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PCIEXPRESS, ASPM_GUID, "1"])
        _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
        summary = "STANDBY // GPU POWER & ASPM RESTORED"
        with self._lock:
            self.gpu_active = False
            self.gpu_summary = summary
        return True, f"GPU BOOST OFF: {summary}"

    def toggle_cpu_boost(self, enable: bool | None = None) -> tuple[bool, str]:
        with self._lock:
            target = (not self.cpu_active) if enable is None else enable

        if target:
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, CPMINCORES, "100"])
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCTHROTTLEMIN, "100"])
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCTHROTTLEMAX, "100"])
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PERFBOOSTMODE, "2"])
            _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, SYSCOOLPOL, "1"])
            rc, _, err = _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
            if rc == 0:
                summary = "ACTIVE // CORES UNPARKED 100% | AGGRESSIVE BOOST"
                with self._lock:
                    self.cpu_active = True
                    self.cpu_summary = summary
                return True, f"CPU BOOST: {summary}"
            return False, f"CPU BOOST failed: {err or 'powercfg rejected settings'}"

        _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, CPMINCORES, "5"])
        _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PROCTHROTTLEMIN, "5"])
        _run_hidden(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, PERFBOOSTMODE, "3"])
        _run_hidden(["powercfg", "/setactive", "SCHEME_CURRENT"])
        summary = "STANDBY // BALANCED CORE PARKING RESTORED"
        with self._lock:
            self.cpu_active = False
            self.cpu_summary = summary
        return True, f"CPU BOOST OFF: {summary}"

    def toggle_windows_os_boost(self, enable: bool | None = None) -> tuple[bool, str]:
        with self._lock:
            target = (not self.os_active) if enable is None else enable

        if target:
            stopped_now: list[str] = []
            for svc in ("SysMain", "DiagTrack"):
                rc_q, out_q, _ = _run_hidden(["sc", "query", svc], timeout=4.0)
                if rc_q == 0 and "RUNNING" in out_q.upper():
                    rc_s, _, _ = _run_hidden(["sc", "stop", svc], timeout=6.0)
                    if rc_s == 0:
                        stopped_now.append(svc)

            # Clear stale temp & GPU shader caches
            freed_bytes = 0
            temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or ""
            freed_bytes += _clean_directory_files(temp_dir, min_age_s=1800)
            local_app = os.environ.get("LOCALAPPDATA") or ""
            if local_app:
                for rel in (
                    "D3DSCache",
                    os.path.join("NVIDIA", "DXCache"),
                    os.path.join("AMD", "DxCache"),
                ):
                    freed_bytes += _clean_directory_files(os.path.join(local_app, rel), min_age_s=3600)

            # Enable Windows Game Mode + Gaming Multimedia Priority
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar") as k:
                    winreg.SetValueEx(k, "AutoGameModeEnabled", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(k, "AllowAutoGameMode", 0, winreg.REG_DWORD, 1)
            except OSError:
                pass

            try:
                with winreg.CreateKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games",
                ) as k:
                    winreg.SetValueEx(k, "GPU Priority", 0, winreg.REG_DWORD, 8)
                    winreg.SetValueEx(k, "Priority", 0, winreg.REG_DWORD, 6)
                    winreg.SetValueEx(k, "Scheduling Category", 0, winreg.REG_SZ, "High")
            except OSError:
                pass

            _run_hidden(["ipconfig", "/flushdns"], timeout=6.0)
            freed_mb = freed_bytes / (1024 * 1024)
            svc_txt = f"PAUSED {','.join(stopped_now)}" if stopped_now else "GAME MODE + DNS"
            summary = f"ACTIVE // {svc_txt} | CACHE -{freed_mb:.1f} MB"
            with self._lock:
                self._stopped_services = stopped_now
                self.os_active = True
                self.os_summary = summary
            return True, f"WINDOWS OS BOOST: {summary}"

        with self._lock:
            to_restart = list(self._stopped_services)
            self._stopped_services = []
        for svc in to_restart:
            _run_hidden(["sc", "start", svc], timeout=6.0)
        summary = "STANDBY // OS SERVICES RESTORED"
        with self._lock:
            self.os_active = False
            self.os_summary = summary
        return True, f"WINDOWS OS BOOST OFF: {summary}"

    def activate_all(self) -> tuple[bool, str]:
        msgs: list[str] = []
        for fn in (
            self.activate_ram_boost,
            lambda: self.toggle_gpu_boost(True),
            lambda: self.toggle_cpu_boost(True),
            lambda: self.toggle_windows_os_boost(True),
        ):
            _, m = fn()
            msgs.append(m)
        return True, "ALL 4 BOOSTERS ACTIVE (" + "; ".join(m.split(": ", 1)[0] for m in msgs) + ")"


if __name__ == "__main__":
    assert snap_gpu_clock_mhz(700) == 800
    assert snap_gpu_clock_mhz(808) == 815
    assert snap_gpu_clock_mhz(1850) == 1850
    assert snap_cpu_freq_ghz(1.2) == 1.80
    assert snap_cpu_freq_ghz(3.16) == 3.20
    assert snap_cpu_freq_ghz(4.8) == 4.50
    assert ghz_to_mhz(3.20) == 3200
    assert set(SAFE_OC_PROFILES.keys()) == {"ECO", "BALANCED", "MAX SAFE OC"}

    ctrl = ClockController()
    ok82, _ = ctrl.set_watchdog_limit(82.0)
    assert ok82 and ctrl.get_state().watchdog_limit_c == 82.0
    ok86, _ = ctrl.set_watchdog_limit(86.0)
    assert ok86 and ctrl.get_state().watchdog_limit_c == 86.0

    guid, plan_name = read_active_power_plan()
    freq_cap = read_cpu_target_freq_mhz()
    print(f"Snapping assertions : OK")
    print(f"Safe OC Profiles    : {', '.join(SAFE_OC_PROFILES.keys())}")
    print(f"Watchdog 82/86 test : OK (current={ctrl.get_state().watchdog_limit_c:.0f}C)")
    print(f"UAC Admin           : {is_admin()}")
    print(f"nvidia-smi          : {find_nvidia_smi()}")
    print(f"Active Power Plan   : {plan_name} ({guid})")
    print(f"PROCFREQMAX (MHz)   : {freq_cap}")
