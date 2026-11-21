"""BANKO v3.0.0 — 7 Engineering System Modules.

Modules:
  1. ProcessManager  (# process-killer)
  2. NetworkTools    (# network-ping)
  3. StorageMonitor  (# storage-health)
  4. StartupManager  (# startup-optimizer)
  5. StressTester    (# benchmark-stress)
  6. DisplayManager  (# display-hz)
  7. SessionLogger   (# session-logger)
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import winreg
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
from PIL import Image, ImageFilter

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_STARTUPINFO = None
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0


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
            startupinfo=_STARTUPINFO,
        )
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except Exception as exc:
        return -1, "", str(exc)


# --------------------------------------------------------------------------- #
# 1. ProcessManager (# process-killer)
# --------------------------------------------------------------------------- #
_PRIORITY_LABELS: dict[int, str] = {
    getattr(psutil, "IDLE_PRIORITY_CLASS", 64): "IDLE",
    getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384): "BELOW NORMAL",
    getattr(psutil, "NORMAL_PRIORITY_CLASS", 32): "NORMAL",
    getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768): "ABOVE NORMAL",
    getattr(psutil, "HIGH_PRIORITY_CLASS", 128): "HIGH",
    getattr(psutil, "REALTIME_PRIORITY_CLASS", 256): "REALTIME",
}


class ProcessManager:
    """Top RAM/CPU process inspector with safe Kill Task and Set High Priority."""

    PROTECTED_PROCS: set[str] = {
        "system",
        "system idle process",
        "registry",
        "memory compression",
        "smss.exe",
        "csrss.exe",
        "csrss",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "dwm.exe",
        "explorer.exe",
        "audiodg.exe",
    }

    KNOWN_BLOAT: set[str] = {
        "gamebarpresencewriter.exe",
        "widgetservice.exe",
        "widgets.exe",
        "yourphone.exe",
        "phoneexperiencehost.exe",
        "msedgewebview2.exe",
        "cortana.exe",
        "onedrive.exe",
        "teams.exe",
        "ms-teams.exe",
        "spotify.exe",
        "discord.exe",
    }

    def __init__(self) -> None:
        self._logical_cpus = max(1, psutil.cpu_count(logical=True) or 1)
        self._self_pid = os.getpid()
        # Initial non-blocking CPU% prime
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    def list_top_processes(
        self, sort_by: str = "ram", limit: int = 30, query: str = ""
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        q = query.strip().lower()
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid <= 0:
                    continue
                name = str(proc.info.get("name") or f"pid-{pid}")
                name_low = name.lower()
                if q and q not in name_low and q not in str(pid):
                    continue
                mi = proc.info.get("memory_info")
                ram_mb = round((mi.rss / 1048576.0), 1) if mi else 0.0
                raw_cpu = proc.cpu_percent(interval=None)
                cpu_pct = round(min(100.0, max(0.0, raw_cpu / self._logical_cpus)), 1)
                try:
                    prio_val = proc.nice()
                    prio_lbl = _PRIORITY_LABELS.get(prio_val, "NORMAL")
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    prio_lbl = "LOCKED"

                protected = pid in (0, 4, self._self_pid) or name_low in self.PROTECTED_PROCS
                if protected:
                    tag = "PROTECTED"
                elif name_low in self.KNOWN_BLOAT:
                    tag = "BLOAT"
                else:
                    tag = "USER APP"

                rows.append(
                    {
                        "pid": pid,
                        "name": name,
                        "cpu_pct": cpu_pct,
                        "ram_mb": ram_mb,
                        "priority_label": prio_lbl,
                        "tag": tag,
                        "protected": protected,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if sort_by == "cpu":
            rows.sort(key=lambda r: (r["cpu_pct"], r["ram_mb"]), reverse=True)
        else:
            rows.sort(key=lambda r: (r["ram_mb"], r["cpu_pct"]), reverse=True)
        return rows[: max(1, int(limit))]

    def kill_process(self, pid: int) -> tuple[bool, str]:
        if pid in (0, 4, self._self_pid):
            return False, f"Refused: PID {pid} is protected"
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if name.lower() in self.PROTECTED_PROCS:
                return False, f"Refused: {name} (PID {pid}) is a protected Windows process"
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except psutil.TimeoutExpired:
                proc.kill()
            return True, f"KILLED TASK: {name} (PID {pid})"
        except psutil.NoSuchProcess:
            return False, f"Process PID {pid} no longer exists"
        except psutil.AccessDenied:
            return False, f"Access denied killing PID {pid} (kernel/protected)"
        except Exception as exc:
            return False, f"Kill failed for PID {pid}: {exc}"

    def set_priority(self, pid: int, high: bool = True) -> tuple[bool, str]:
        if pid in (0, 4):
            return False, f"Refused: cannot alter kernel PID {pid}"
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            target_cls = (
                psutil.HIGH_PRIORITY_CLASS if high else psutil.NORMAL_PRIORITY_CLASS
            )
            proc.nice(target_cls)
            mode = "HIGH" if high else "NORMAL"
            return True, f"PRIORITY -> {mode}: {name} (PID {pid})"
        except psutil.NoSuchProcess:
            return False, f"Process PID {pid} no longer exists"
        except psutil.AccessDenied:
            return False, f"Access denied setting priority on PID {pid}"
        except Exception as exc:
            return False, f"Priority change failed: {exc}"


# --------------------------------------------------------------------------- #
# 2. NetworkTools (# network-ping)
# --------------------------------------------------------------------------- #
_VALID_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]{1,253}$")
_PING_MS_RE = re.compile(r"(?:time|tiempo|zeit|temps)\s*([=<])\s*(\d+(?:\.\d+)?)\s*ms", re.I)
_PING_FALLBACK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ms\b", re.I)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class NetworkTools:
    """Live ICMP ping monitor, DNS profile switcher (Cloudflare / Google / DHCP), and flush."""

    DEFAULT_ENDPOINTS: list[tuple[str, str]] = [
        ("1.1.1.1", "Cloudflare Gaming Primary"),
        ("1.0.0.1", "Cloudflare Secondary"),
        ("8.8.8.8", "Google Public DNS"),
        ("9.9.9.9", "Quad9 Anycast DNS"),
    ]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, dict[str, Any]] = {}

    def _read_iface_dns(self, iface: str) -> tuple[str, str]:
        rc, out, _ = _run_hidden(
            ["netsh", "interface", "ip", "show", "dnsservers", f'name="{iface}"'],
            timeout=4.0,
        )
        # netsh does not want embedded quotes when passed as an argument list element
        if rc != 0:
            rc, out, _ = _run_hidden(
                ["netsh", "interface", "ip", "show", "dnsservers", f"name={iface}"],
                timeout=4.0,
            )
        if rc != 0 or not out:
            return "Unknown", "AUTO"
        low = out.lower()
        mode = "DHCP" if "dhcp" in low else ("STATIC" if "static" in low or "configured" in low else "AUTO")
        ips = _IPV4_RE.findall(out)
        if ips:
            return ", ".join(dict.fromkeys(ips)), mode
        return ("Automatic (DHCP)" if mode == "DHCP" else "None"), mode

    def list_active_interfaces(self) -> list[dict[str, Any]]:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        out: list[dict[str, Any]] = []
        for name, st in stats.items():
            low = name.lower()
            if not st.isup or "loopback" in low:
                continue
            ipv4 = "--"
            for addr in addrs.get(name, []):
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ipv4 = addr.address
                    break
            if ipv4 == "--" and "local area connection*" in low:
                continue
            dns_txt, dns_mode = self._read_iface_dns(name)
            out.append(
                {
                    "name": name,
                    "speed_mbps": int(st.speed or 0),
                    "ipv4": ipv4,
                    "dns_servers": dns_txt,
                    "dns_mode": dns_mode,
                }
            )
        out.sort(key=lambda item: (item["ipv4"] == "--", -item["speed_mbps"], item["name"]))
        return out

    def ping_host(self, host: str, timeout_ms: int = 1000) -> float | None:
        target = host.strip()
        if not target or not _VALID_HOST_RE.match(target):
            return None
        rc, out, _ = _run_hidden(
            ["ping", "-n", "1", "-w", str(max(250, int(timeout_ms))), target],
            timeout=max(2.0, timeout_ms / 1000.0 + 1.5),
        )
        if rc != 0 or not out:
            return None
        m = _PING_MS_RE.search(out)
        if m:
            val = float(m.group(2))
            return 0.5 if m.group(1) == "<" and val <= 1.0 else round(val, 1)
        for line in out.splitlines():
            if "ttl=" in line.lower() or target in line:
                mf = _PING_FALLBACK_RE.search(line)
                if mf:
                    return round(float(mf.group(1)), 1)
        return None

    def ping_all(self, extra_host: str = "") -> list[dict[str, Any]]:
        targets = list(self.DEFAULT_ENDPOINTS)
        extra = extra_host.strip()
        if extra and _VALID_HOST_RE.match(extra) and not any(h == extra for h, _ in targets):
            targets.append((extra, "Custom Endpoint"))

        with ThreadPoolExecutor(max_workers=min(6, len(targets))) as pool:
            latencies = list(pool.map(lambda t: self.ping_host(t[0], 1000), targets))

        results: list[dict[str, Any]] = []
        with self._lock:
            for (host, label), rtt in zip(targets, latencies):
                rec = self._history.setdefault(
                    host,
                    {"sent": 0, "lost": 0, "samples": deque(maxlen=30)},
                )
                rec["sent"] += 1
                if rtt is None:
                    rec["lost"] += 1
                    status = "TIMEOUT"
                else:
                    rec["samples"].append(rtt)
                    status = "LOW" if rtt < 35.0 else ("MODERATE" if rtt < 80.0 else "HIGH")

                samples = list(rec["samples"])
                avg_ms = round(sum(samples) / len(samples), 1) if samples else None
                min_ms = round(min(samples), 1) if samples else None
                max_ms = round(max(samples), 1) if samples else None
                loss_pct = round((rec["lost"] / rec["sent"]) * 100.0, 1) if rec["sent"] > 0 else 0.0
                results.append(
                    {
                        "host": host,
                        "label": label,
                        "last_ms": rtt,
                        "min_ms": min_ms,
                        "avg_ms": avg_ms,
                        "max_ms": max_ms,
                        "loss_pct": loss_pct,
                        "status": status,
                    }
                )
        return results

    def set_dns_cloudflare(self, iface: str) -> tuple[bool, str]:
        return self._set_static_dns(iface, "1.1.1.1", "1.0.0.1", "Cloudflare Gaming DNS")

    def set_dns_google(self, iface: str) -> tuple[bool, str]:
        return self._set_static_dns(iface, "8.8.8.8", "8.8.4.4", "Google Public DNS")

    def _set_static_dns(
        self, iface: str, primary: str, secondary: str, label: str
    ) -> tuple[bool, str]:
        if not iface:
            return False, "No active network adapter selected"
        rc1, out1, err1 = _run_hidden(
            ["netsh", "interface", "ip", "set", "dns", f"name={iface}", "static", primary],
            timeout=6.0,
        )
        if rc1 != 0:
            return False, f"DNS update failed on {iface}: {err1 or out1 or f'exit {rc1}'}"
        _run_hidden(
            [
                "netsh",
                "interface",
                "ip",
                "add",
                "dns",
                f"name={iface}",
                secondary,
                "index=2",
            ],
            timeout=6.0,
        )
        self.flush_dns()
        return True, f"DNS -> {label} ({primary}, {secondary}) on {iface}"

    def set_dns_dhcp(self, iface: str) -> tuple[bool, str]:
        if not iface:
            return False, "No active network adapter selected"
        rc, out, err = _run_hidden(
            ["netsh", "interface", "ip", "set", "dns", f"name={iface}", "dhcp"],
            timeout=6.0,
        )
        if rc != 0:
            return False, f"DHCP DNS restore failed on {iface}: {err or out or f'exit {rc}'}"
        self.flush_dns()
        return True, f"DNS RESTORED TO AUTOMATIC DHCP on {iface}"

    def flush_dns(self) -> tuple[bool, str]:
        rc, out, err = _run_hidden(["ipconfig", "/flushdns"], timeout=5.0)
        if rc == 0:
            return True, "DNS RESOLVER CACHE FLUSHED"
        return False, f"DNS flush failed: {err or out}"


# --------------------------------------------------------------------------- #
# 3. StorageMonitor (# storage-health)
# --------------------------------------------------------------------------- #
class StorageMonitor:
    """NVMe/SSD volume space, LHM/CIM health & temperature, I/O throughput, and ReTrim."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_io: dict[str, Any] = {}
        self._prev_t = time.monotonic()
        self._cached_cim_disks: list[dict[str, Any]] = []
        self._last_cim_poll = 0.0
        try:
            self._prev_io = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            self._prev_io = {}

    def _poll_cim_disks(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._last_cim_poll > 0.0 and (now - self._last_cim_poll) < 15.0:
            return list(self._cached_cim_disks)

        disks: list[dict[str, Any]] = []
        ps_cmd = (
            "$ErrorActionPreference='SilentlyContinue';"
            "Get-PhysicalDisk | ForEach-Object {"
            "$rc = $_ | Get-StorageReliabilityCounter;"
            "$t = if($rc -and $rc.Temperature -gt 0){$rc.Temperature}else{''};"
            "$w = if($rc -and $rc.Wear -ne $null){$rc.Wear}else{''};"
            "Write-Output \"$($_.FriendlyName)|$($_.MediaType)|$($_.BusType)|$($_.HealthStatus)|$t|$w\""
            "}"
        )
        rc, out, _ = _run_hidden(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                ps_cmd,
            ],
            timeout=6.0,
        )
        if rc == 0 and out:
            for line in out.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 6 or not parts[0]:
                    continue
                temp_c: float | None = None
                wear_pct: float | None = None
                try:
                    if parts[4]:
                        temp_c = float(parts[4])
                except ValueError:
                    pass
                try:
                    if parts[5]:
                        wear_pct = float(parts[5])
                except ValueError:
                    pass
                disks.append(
                    {
                        "name": parts[0],
                        "media": parts[1] or "SSD",
                        "bus": parts[2] or "NVMe",
                        "health": parts[3] or "Healthy",
                        "temp_c": temp_c,
                        "wear_pct": wear_pct,
                    }
                )

        if not disks:
            # Fallback via registry hardware disk enumeration when CIM is blocked
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Services\disk\Enum",
                ) as k:
                    count = int(winreg.QueryValueEx(k, "Count")[0])
                    for idx in range(count):
                        raw = str(winreg.QueryValueEx(k, str(idx))[0])
                        friendly = raw.split("\\")[-1].split("&")[0].replace("_", " ").strip()
                        if friendly:
                            bus = "NVMe" if "nvme" in raw.lower() else "SATA/PCIe"
                            disks.append(
                                {
                                    "name": friendly,
                                    "media": "SSD" if "ssd" in friendly.lower() or bus == "NVMe" else "Disk",
                                    "bus": bus,
                                    "health": "Healthy",
                                    "temp_c": None,
                                    "wear_pct": None,
                                }
                            )
            except OSError:
                pass

        self._cached_cim_disks = disks
        self._last_cim_poll = now
        return list(disks)

    def get_volumes_and_health(
        self, lhm_sensors: list[dict[str, Any]] | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cim_disks = self._poll_cim_disks()
        lhm_list = list(lhm_sensors or [])

        # Merge LHM temperature/life sensors with CIM/registry disk descriptors
        merged_hw: list[dict[str, Any]] = []
        max_len = max(len(cim_disks), len(lhm_list))
        for idx in range(max_len):
            cim = cim_disks[idx] if idx < len(cim_disks) else {}
            lhm = lhm_list[idx] if idx < len(lhm_list) else {}
            name = str(lhm.get("name") or cim.get("name") or f"PhysicalDrive{idx}")
            temp_c = lhm.get("temp_c") if lhm.get("temp_c") is not None else cim.get("temp_c")
            life_pct = lhm.get("life_pct")
            if life_pct is None and cim.get("wear_pct") is not None:
                life_pct = round(max(0.0, 100.0 - float(cim["wear_pct"])), 1)
            merged_hw.append(
                {
                    "name": name,
                    "bus": str(cim.get("bus") or ("NVMe" if "nvme" in name.lower() else "SMART")),
                    "health": str(cim.get("health") or "Healthy"),
                    "temp_c": temp_c,
                    "life_pct": life_pct,
                }
            )

        volume_rows: list[dict[str, Any]] = []
        for idx, part in enumerate(psutil.disk_partitions(all=False)):
            if "cdrom" in (part.opts or "").lower():
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            hw = merged_hw[idx] if idx < len(merged_hw) else (merged_hw[0] if merged_hw else {})
            letter = part.mountpoint.rstrip("\\")
            volume_rows.append(
                {
                    "drive": letter,
                    "fstype": part.fstype or "NTFS",
                    "model": str(hw.get("name") or part.device),
                    "bus": str(hw.get("bus") or "--"),
                    "total_gb": round(usage.total / (2**30), 1),
                    "used_gb": round(usage.used / (2**30), 1),
                    "free_gb": round(usage.free / (2**30), 1),
                    "used_pct": round(usage.percent, 1),
                    "temp_c": hw.get("temp_c"),
                    "life_pct": hw.get("life_pct"),
                    "health": str(hw.get("health") or "Healthy"),
                }
            )

        # Per-disk live Read/Write MB/s
        io_rows: list[dict[str, Any]] = []
        now = time.monotonic()
        with self._lock:
            dt = max(0.05, now - self._prev_t)
            try:
                cur_io = psutil.disk_io_counters(perdisk=True) or {}
            except Exception:
                cur_io = {}
            for dev_name, counters in sorted(cur_io.items()):
                prev = self._prev_io.get(dev_name)
                if prev is not None:
                    r_mbs = round(max(0.0, (counters.read_bytes - prev.read_bytes) / (1048576.0 * dt)), 2)
                    w_mbs = round(max(0.0, (counters.write_bytes - prev.write_bytes) / (1048576.0 * dt)), 2)
                else:
                    r_mbs = 0.0
                    w_mbs = 0.0
                hw_idx = 0
                m_idx = re.search(r"(\d+)$", dev_name)
                if m_idx:
                    hw_idx = int(m_idx.group(1))
                hw = merged_hw[hw_idx] if hw_idx < len(merged_hw) else {}
                io_rows.append(
                    {
                        "disk": dev_name,
                        "model": str(hw.get("name") or dev_name),
                        "read_mb_s": r_mbs,
                        "write_mb_s": w_mbs,
                        "read_count": int(counters.read_count),
                        "write_count": int(counters.write_count),
                        "temp_c": hw.get("temp_c"),
                    }
                )
            self._prev_io = cur_io
            self._prev_t = now

        return volume_rows, io_rows

    def optimize_retrim(self, drive_letter: str = "C") -> tuple[bool, str]:
        cleaned = re.sub(r"[^A-Za-z]", "", drive_letter or "C")[:1].upper()
        if not cleaned:
            return False, "Invalid drive letter"
        rc, out, err = _run_hidden(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                f"Optimize-Volume -DriveLetter {cleaned} -ReTrim -Verbose",
            ],
            timeout=25.0,
        )
        if rc == 0:
            return True, f"OPTIMIZE / RE-TRIM COMPLETED ON {cleaned}:"
        msg = err or out or f"exit {rc}"
        return False, f"ReTrim on {cleaned}: failed ({msg[:120]})"


# --------------------------------------------------------------------------- #
# 4. StartupManager (# startup-optimizer)
# --------------------------------------------------------------------------- #
_HEAVY_STARTUP_TOKENS = (
    "steam",
    "epic",
    "discord",
    "spotify",
    "teams",
    "onedrive",
    "adobe",
    "edge",
    "chrome",
    "armoury",
    "razer",
    "icue",
    "overwolf",
    "riot",
    "battle.net",
)
_LOW_STARTUP_TOKENS = ("security", "defender", "realtek", "audio", "nvidia", "intel", "amd", "synaptics")

_CURATED_SERVICES: list[tuple[str, str, str]] = [
    ("SysMain", "SysMain (Superfetch)", "MEDIUM"),
    ("DiagTrack", "Connected User Experiences & Telemetry", "HIGH"),
    ("WSearch", "Windows Search Indexer", "HIGH"),
    ("MapsBroker", "Downloaded Maps Manager", "LOW"),
    ("XblGameSave", "Xbox Live Game Save", "LOW"),
    ("XboxNetApiSvc", "Xbox Live Networking Service", "LOW"),
    ("Fax", "Windows Fax Service", "LOW"),
    ("WerSvc", "Windows Error Reporting", "LOW"),
]


class StartupManager:
    """Windows Startup registry/folder entries and optional service optimizer."""

    _APPROVED_RUN = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    _APPROVED_RUN32 = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"
    _APPROVED_FOLDER = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"

    @staticmethod
    def _classify_impact(name: str, cmd: str) -> str:
        low = f"{name} {cmd}".lower()
        if any(t in low for t in _HEAVY_STARTUP_TOKENS):
            return "HIGH"
        if any(t in low for t in _LOW_STARTUP_TOKENS):
            return "LOW"
        return "MEDIUM"

    @staticmethod
    def _is_approved_enabled(hive: int, subkey: str, entry_name: str) -> bool:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                raw, _ = winreg.QueryValueEx(k, entry_name)
                if isinstance(raw, (bytes, bytearray)) and len(raw) >= 1:
                    return raw[0] in (0x02, 0x06)
        except OSError:
            pass
        return True

    def list_startup_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        run_sources = [
            (
                winreg.HKEY_CURRENT_USER,
                "HKCU",
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                self._APPROVED_RUN,
                "HKCU Run",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                "HKLM",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                self._APPROVED_RUN,
                "HKLM Run",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                "HKLM",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
                self._APPROVED_RUN32,
                "HKLM Run32",
            ),
        ]

        for hive, hive_name, run_path, approved_path, source_lbl in run_sources:
            try:
                with winreg.OpenKey(hive, run_path) as k:
                    idx = 0
                    while True:
                        try:
                            val_name, val_data, _ = winreg.EnumValue(k, idx)
                            idx += 1
                            enabled = self._is_approved_enabled(hive, approved_path, val_name)
                            cmd_str = str(val_data)
                            items.append(
                                {
                                    "id": f"reg|{hive_name}|{approved_path}|{val_name}",
                                    "name": val_name,
                                    "category": "STARTUP APP",
                                    "source": source_lbl,
                                    "command": cmd_str,
                                    "impact": self._classify_impact(val_name, cmd_str),
                                    "enabled": enabled,
                                    "status": "ENABLED" if enabled else "DISABLED",
                                    "hive": hive,
                                    "approved_subkey": approved_path,
                                    "entry_name": val_name,
                                    "service_name": "",
                                }
                            )
                        except OSError:
                            break
            except OSError:
                continue

        # Startup folders (User + Common)
        folder_specs = [
            (
                winreg.HKEY_CURRENT_USER,
                "HKCU",
                Path(os.environ.get("APPDATA", ""))
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup",
                "User Startup",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                "HKLM",
                Path(os.environ.get("PROGRAMDATA", ""))
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup",
                "Common Startup",
            ),
        ]
        for hive, hive_name, folder, source_lbl in folder_specs:
            try:
                if not folder.is_dir():
                    continue
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.name.lower() == "desktop.ini" or not entry.is_file():
                        continue
                except OSError:
                    continue
                enabled = self._is_approved_enabled(hive, self._APPROVED_FOLDER, entry.name)
                items.append(
                    {
                        "id": f"fld|{hive_name}|{self._APPROVED_FOLDER}|{entry.name}",
                        "name": entry.stem,
                        "category": "STARTUP APP",
                        "source": source_lbl,
                        "command": str(entry),
                        "impact": self._classify_impact(entry.stem, str(entry)),
                        "enabled": enabled,
                        "status": "ENABLED" if enabled else "DISABLED",
                        "hive": hive,
                        "approved_subkey": self._APPROVED_FOLDER,
                        "entry_name": entry.name,
                        "service_name": "",
                    }
                )

        # Curated Windows Services
        for svc_name, display_name, impact in _CURATED_SERVICES:
            enabled = True
            state_txt = "UNKNOWN"
            try:
                svc = psutil.win_service_get(svc_name)
                info = svc.as_dict()
                start_type = str(info.get("start_type") or "").lower()
                svc_status = str(info.get("status") or "").upper()
                enabled = start_type != "disabled"
                state_txt = f"{'DISABLED' if not enabled else 'ENABLED'} ({svc_status})"
            except Exception:
                rc, out, _ = _run_hidden(["sc", "qc", svc_name], timeout=3.0)
                if rc != 0:
                    continue
                enabled = "DISABLED" not in out.upper()
                state_txt = "ENABLED" if enabled else "DISABLED"

            items.append(
                {
                    "id": f"svc|{svc_name}",
                    "name": display_name,
                    "category": "SERVICE",
                    "source": f"sc:{svc_name}",
                    "command": f"Windows Service [{svc_name}]",
                    "impact": impact,
                    "enabled": enabled,
                    "status": state_txt,
                    "hive": 0,
                    "approved_subkey": "",
                    "entry_name": svc_name,
                    "service_name": svc_name,
                }
            )

        return items

    def toggle_item(self, item: dict[str, Any]) -> tuple[bool, str]:
        cat = str(item.get("category") or "")
        currently_enabled = bool(item.get("enabled", True))
        target_enable = not currently_enabled

        if cat == "SERVICE":
            svc_name = str(item.get("service_name") or "")
            if not svc_name:
                return False, "Missing service identifier"
            if target_enable:
                rc1, out1, err1 = _run_hidden(
                    ["sc", "config", svc_name, "start=", "demand"], timeout=5.0
                )
                _run_hidden(["sc", "start", svc_name], timeout=5.0)
                if rc1 == 0:
                    return True, f"SERVICE ENABLED: {svc_name} (manual/demand)"
                return False, f"Failed to enable {svc_name}: {err1 or out1}"
            rc1, out1, err1 = _run_hidden(
                ["sc", "config", svc_name, "start=", "disabled"], timeout=5.0
            )
            _run_hidden(["sc", "stop", svc_name], timeout=5.0)
            if rc1 == 0:
                return True, f"SERVICE DISABLED: {svc_name}"
            return False, f"Failed to disable {svc_name}: {err1 or out1}"

        # Registry / StartupFolder StartupApproved binary toggle
        hive = int(item.get("hive") or winreg.HKEY_CURRENT_USER)
        subkey = str(item.get("approved_subkey") or "")
        entry_name = str(item.get("entry_name") or "")
        if not subkey or not entry_name:
            return False, "Invalid startup registry descriptor"

        blob = (b"\x02" if target_enable else b"\x03") + (b"\x00" * 11)
        try:
            with winreg.CreateKey(hive, subkey) as k:
                winreg.SetValueEx(k, entry_name, 0, winreg.REG_BINARY, blob)
            action = "ENABLED" if target_enable else "DISABLED"
            return True, f"STARTUP {action}: {item.get('name', entry_name)}"
        except PermissionError:
            return False, f"Permission denied toggling {entry_name} (Admin required for HKLM)"
        except OSError as exc:
            return False, f"Registry error toggling {entry_name}: {exc}"


# --------------------------------------------------------------------------- #
# 5. StressTester (# benchmark-stress)
# --------------------------------------------------------------------------- #
class StressTester:
    """Built-in 30-second multi-threaded CPU/GPU compute stability test with thermal guard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None
        self._ops_lock = threading.Lock()
        self._ops_count = 0

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _cpu_worker(self, stop_evt: threading.Event) -> None:
        payload = os.urandom(4096)
        salt = b"BANKO_STRESS_V3"
        while not stop_evt.is_set():
            _ = hashlib.pbkdf2_hmac("sha256", payload, salt, 900)
            _ = zlib.compress(payload * 2, level=3)
            with self._ops_lock:
                self._ops_count += 1

    def _gpu_matrix_worker(self, stop_evt: threading.Event) -> None:
        img = Image.new("RGB", (640, 360), (88, 101, 242))
        while not stop_evt.is_set():
            blurred = img.filter(ImageFilter.GaussianBlur(radius=2))
            _ = blurred.resize((320, 180), Image.Resampling.BICUBIC)
            with self._ops_lock:
                self._ops_count += 1

    def start(
        self,
        mode: str = "CPU_ONLY",
        monitor_fn: Callable[[], Any] | None = None,
        watchdog_limit_c: float = 86.0,
        duration_s: int = 30,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[bool, str]:
        with self._lock:
            if self._running:
                return False, "Stress test is already running"
            self._running = True
            self._stop_evt.clear()
        with self._ops_lock:
            self._ops_count = 0

        def _runner() -> None:
            workers: list[threading.Thread] = []
            threads_n = max(2, psutil.cpu_count(logical=True) or 4)
            for idx in range(threads_n):
                t = threading.Thread(
                    target=self._cpu_worker,
                    args=(self._stop_evt,),
                    name=f"banko-stress-cpu-{idx}",
                    daemon=True,
                )
                t.start()
                workers.append(t)

            if mode == "CPU_GPU":
                t_gpu = threading.Thread(
                    target=self._gpu_matrix_worker,
                    args=(self._stop_evt,),
                    name="banko-stress-matrix",
                    daemon=True,
                )
                t_gpu.start()
                workers.append(t_gpu)

            samples: list[dict[str, Any]] = []
            aborted_reason = ""
            start_t = time.monotonic()
            prev_ops = 0

            try:
                for sec in range(1, max(1, int(duration_s)) + 1):
                    if self._stop_evt.wait(1.0):
                        aborted_reason = "USER STOPPED"
                        break
                    with self._ops_lock:
                        cur_ops = self._ops_count
                    delta_ops = max(0, cur_ops - prev_ops)
                    prev_ops = cur_ops
                    kops_s = round(delta_ops * 0.9, 1)

                    snap = monitor_fn() if monitor_fn else None
                    cpu_t = getattr(snap, "cpu_temp", None) if snap else None
                    cpu_ghz = getattr(snap, "cpu_freq_ghz", 0.0) if snap else 0.0
                    cpu_load = getattr(snap, "cpu_load", 0.0) if snap else 0.0
                    gpu_t = getattr(snap, "gpu_temp", None) if snap else None
                    gpu_mhz = getattr(snap, "gpu_clock_mhz", None) if snap else None
                    gpu_load = getattr(snap, "gpu_load", None) if snap else None

                    status = "STABLE"
                    if cpu_t is not None and cpu_t >= 95.0:
                        status = "THERMAL ABORT"
                        aborted_reason = f"CPU THERMAL GUARD ({cpu_t:.0f}°C >= 95°C)"
                    elif gpu_t is not None and gpu_t >= watchdog_limit_c:
                        status = "THERMAL ABORT"
                        aborted_reason = f"GPU WATCHDOG GUARD ({gpu_t:.0f}°C >= {watchdog_limit_c:.0f}°C)"
                    elif (cpu_t is not None and cpu_t >= 85.0) or (gpu_t is not None and gpu_t >= 80.0):
                        status = "WARM"

                    row = {
                        "sec": sec,
                        "duration_s": duration_s,
                        "elapsed_s": round(time.monotonic() - start_t, 1),
                        "cpu_temp": cpu_t,
                        "cpu_ghz": cpu_ghz,
                        "cpu_load": cpu_load,
                        "gpu_temp": gpu_t,
                        "gpu_mhz": gpu_mhz,
                        "gpu_load": gpu_load,
                        "kops_s": kops_s,
                        "status": status,
                    }
                    samples.append(row)
                    if on_sample is not None:
                        on_sample(row)

                    if aborted_reason:
                        self._stop_evt.set()
                        break
            finally:
                self._stop_evt.set()
                for t in workers:
                    t.join(timeout=0.5)
                with self._lock:
                    self._running = False

            cpu_temps = [r["cpu_temp"] for r in samples if r["cpu_temp"] is not None]
            gpu_temps = [r["gpu_temp"] for r in samples if r["gpu_temp"] is not None]
            cpu_clks = [r["cpu_ghz"] for r in samples if r["cpu_ghz"] and r["cpu_ghz"] > 0]
            gpu_clks = [r["gpu_mhz"] for r in samples if r["gpu_mhz"] is not None]

            if aborted_reason and aborted_reason != "USER STOPPED":
                verdict = f"ABORTED — {aborted_reason}"
            elif aborted_reason == "USER STOPPED":
                verdict = "STOPPED BY USER"
            elif (cpu_temps and max(cpu_temps) >= 90.0) or (
                len(cpu_clks) >= 4 and (max(cpu_clks) - min(cpu_clks)) > 1.2
            ):
                verdict = "WARN — THERMAL THROTTLING DETECTED"
            else:
                verdict = "PASS — STABLE"

            summary = {
                "verdict": verdict,
                "mode": mode,
                "samples": len(samples),
                "peak_cpu_c": max(cpu_temps) if cpu_temps else None,
                "peak_gpu_c": max(gpu_temps) if gpu_temps else None,
                "avg_cpu_ghz": round(sum(cpu_clks) / len(cpu_clks), 2) if cpu_clks else 0.0,
                "min_gpu_mhz": round(min(gpu_clks), 0) if gpu_clks else None,
                "avg_gpu_mhz": round(sum(gpu_clks) / len(gpu_clks), 0) if gpu_clks else None,
            }
            if on_done is not None:
                on_done(summary)

        self._thread = threading.Thread(target=_runner, name="banko-stress-master", daemon=True)
        self._thread.start()
        return True, f"STARTED {duration_s}s STABILITY TEST ({mode})"

    def stop(self) -> tuple[bool, str]:
        self._stop_evt.set()
        return True, "STOPPING STRESS TEST..."


# --------------------------------------------------------------------------- #
# 6. DisplayManager (# display-hz)
# --------------------------------------------------------------------------- #
class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


_DISPLAY_DEVICE_ATTACHED = 0x1
_DISPLAY_DEVICE_PRIMARY = 0x4
_ENUM_CURRENT_SETTINGS = -1
_DM_DISPLAYFREQUENCY = 0x00400000
_CDS_UPDATEREGISTRY = 0x00000001
_CDS_TEST = 0x00000002
_DISP_CHANGE_SUCCESSFUL = 0


class DisplayManager:
    """Win32 Display & Refresh Rate Deck (MAX REFRESH RATE / BATTERY 60Hz)."""

    def list_displays(self) -> list[dict[str, Any]]:
        displays: list[dict[str, Any]] = []
        u32 = ctypes.windll.user32
        idx = 0
        while True:
            dd = _DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not u32.EnumDisplayDevicesW(None, idx, ctypes.byref(dd), 0):
                break
            idx += 1
            if not (dd.StateFlags & _DISPLAY_DEVICE_ATTACHED):
                continue

            cur = _DEVMODEW()
            cur.dmSize = ctypes.sizeof(_DEVMODEW)
            if not u32.EnumDisplaySettingsW(dd.DeviceName, _ENUM_CURRENT_SETTINGS, ctypes.byref(cur)):
                continue

            rates: set[int] = set()
            mode_i = 0
            probe = _DEVMODEW()
            probe.dmSize = ctypes.sizeof(_DEVMODEW)
            while u32.EnumDisplaySettingsW(dd.DeviceName, mode_i, ctypes.byref(probe)):
                mode_i += 1
                if (
                    probe.dmPelsWidth == cur.dmPelsWidth
                    and probe.dmPelsHeight == cur.dmPelsHeight
                    and probe.dmBitsPerPel == cur.dmBitsPerPel
                    and probe.dmDisplayFrequency > 0
                ):
                    rates.add(int(probe.dmDisplayFrequency))

            sorted_rates = sorted(rates)
            if not sorted_rates:
                sorted_rates = [int(cur.dmDisplayFrequency)]

            displays.append(
                {
                    "device_name": dd.DeviceName,
                    "adapter_name": dd.DeviceString or "Display Output",
                    "is_primary": bool(dd.StateFlags & _DISPLAY_DEVICE_PRIMARY),
                    "width": int(cur.dmPelsWidth),
                    "height": int(cur.dmPelsHeight),
                    "bit_depth": int(cur.dmBitsPerPel),
                    "current_hz": int(cur.dmDisplayFrequency),
                    "refresh_rates": sorted_rates,
                    "max_hz": max(sorted_rates),
                    "min_hz": min(sorted_rates),
                }
            )
        return displays

    def set_refresh_rate(self, device_name: str, target_hz: int) -> tuple[bool, str]:
        u32 = ctypes.windll.user32
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not u32.EnumDisplaySettingsW(device_name, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            return False, f"Failed to read display settings for {device_name}"
        if int(dm.dmDisplayFrequency) == int(target_hz):
            return True, f"{device_name} ALREADY RUNNING AT {target_hz} Hz"

        dm.dmDisplayFrequency = int(target_hz)
        dm.dmFields = _DM_DISPLAYFREQUENCY
        rc_test = u32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None, _CDS_TEST, None)
        if rc_test != _DISP_CHANGE_SUCCESSFUL:
            return False, f"Display mode {dm.dmPelsWidth}x{dm.dmPelsHeight} @ {target_hz} Hz rejected (code {rc_test})"

        rc_apply = u32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None, _CDS_UPDATEREGISTRY, None)
        if rc_apply == _DISP_CHANGE_SUCCESSFUL:
            return True, f"DISPLAY {device_name} SET TO {target_hz} Hz ({dm.dmPelsWidth}x{dm.dmPelsHeight})"
        return False, f"Display switch failed with code {rc_apply}"

    def set_max_refresh_rate(self, device_name: str = "") -> tuple[bool, str]:
        displays = self.list_displays()
        if not displays:
            return False, "No active displays detected"
        target = next((d for d in displays if d["device_name"] == device_name), displays[0])
        return self.set_refresh_rate(target["device_name"], int(target["max_hz"]))

    def set_battery_60hz(self, device_name: str = "") -> tuple[bool, str]:
        displays = self.list_displays()
        if not displays:
            return False, "No active displays detected"
        target = next((d for d in displays if d["device_name"] == device_name), displays[0])
        rates: list[int] = list(target["refresh_rates"])
        if 60 in rates:
            chosen = 60
        else:
            le60 = [r for r in rates if r <= 60]
            chosen = max(le60) if le60 else min(rates)
        return self.set_refresh_rate(target["device_name"], chosen)


# --------------------------------------------------------------------------- #
# 7. SessionLogger (# session-logger)
# --------------------------------------------------------------------------- #
class SessionLogger:
    """Live timestamped telemetry CSV recorder."""

    CSV_HEADERS = [
        "timestamp",
        "elapsed_s",
        "cpu_temp_c",
        "cpu_freq_ghz",
        "cpu_load_pct",
        "cpu_peak_pct",
        "gpu_temp_c",
        "gpu_core_mhz",
        "gpu_mem_mhz",
        "gpu_load_pct",
        "gpu_pstate",
        "gpu_power_w",
        "vram_used_mb",
        "vram_total_mb",
        "vram_pct",
        "ram_used_gb",
        "ram_os_gb",
        "ram_apps_gb",
        "ram_free_gb",
        "ram_pct",
        "battery_pct",
        "power_status",
        "power_rate_w",
        "power_plan",
        "fan01_rpm",
        "fan01_pct",
        "fan02_rpm",
        "fan02_pct",
        "disk_read_mbs",
        "disk_write_mbs",
    ]

    def __init__(self, base_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self.log_dir = base_dir or (Path.home() / "Documents" / "BANKO_Logs")
        self._recording = False
        self._file: Any = None
        self._writer: Any = None
        self._file_path: Path | None = None
        self._start_mono = 0.0
        self._sample_count = 0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = round(time.monotonic() - self._start_mono, 1) if self._recording else 0.0
            size_kb = 0.0
            if self._file_path and self._file_path.exists():
                try:
                    size_kb = round(self._file_path.stat().st_size / 1024.0, 1)
                except OSError:
                    pass
            return {
                "recording": self._recording,
                "path": str(self._file_path) if self._file_path else "",
                "samples": self._sample_count,
                "elapsed_s": elapsed,
                "size_kb": size_kb,
                "log_dir": str(self.log_dir),
            }

    def start(self, custom_path: Path | None = None) -> tuple[bool, str]:
        with self._lock:
            if self._recording:
                return False, f"Already recording to {self._file_path}"
            target = Path(custom_path) if custom_path else (
                self.log_dir / f"BANKO_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                f = open(target, "w", newline="", encoding="utf-8")
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)
                f.flush()
                self._file = f
                self._writer = writer
                self._file_path = target
                self._start_mono = time.monotonic()
                self._sample_count = 0
                self._recording = True
                return True, f"CSV RECORDING STARTED -> {target}"
            except OSError as exc:
                return False, f"Failed to open CSV log: {exc}"

    def record_snapshot(self, snap: Any) -> dict[str, Any] | None:
        with self._lock:
            if not self._recording or self._writer is None or self._file is None:
                return None

            elapsed = round(time.monotonic() - self._start_mono, 1)
            fans = list(getattr(getattr(snap, "fan_state", None), "fans", []) or [])
            f1_rpm = fans[0].rpm if len(fans) >= 1 else ""
            f1_pct = fans[0].duty_pct if len(fans) >= 1 else ""
            f2_rpm = fans[1].rpm if len(fans) >= 2 else ""
            f2_pct = fans[1].duty_pct if len(fans) >= 2 else ""

            ts = datetime.now().strftime("%H:%M:%S")
            row = [
                datetime.now().isoformat(timespec="seconds"),
                elapsed,
                snap.cpu_temp if snap.cpu_temp is not None else "",
                snap.cpu_freq_ghz,
                snap.cpu_load,
                snap.cpu_peak_core,
                snap.gpu_temp if snap.gpu_temp is not None else "",
                snap.gpu_clock_mhz if snap.gpu_clock_mhz is not None else "",
                getattr(snap, "gpu_mem_clock_mhz", None)
                if getattr(snap, "gpu_mem_clock_mhz", None) is not None
                else "",
                snap.gpu_load if snap.gpu_load is not None else "",
                snap.gpu_pstate or "",
                snap.gpu_power_w if snap.gpu_power_w is not None else "",
                snap.gpu_vram_used_mb if snap.gpu_vram_used_mb is not None else "",
                snap.gpu_vram_total_mb if snap.gpu_vram_total_mb is not None else "",
                snap.gpu_vram_pct if snap.gpu_vram_pct is not None else "",
                snap.ram_used_gb,
                snap.ram_os_kernel_gb,
                snap.ram_apps_gb,
                snap.ram_free_gb,
                snap.ram_percent,
                snap.battery_percent if snap.battery_percent is not None else "",
                snap.power_status,
                getattr(snap, "est_system_power_w", snap.power_rate_w) or "",
                snap.power_plan_name,
                f1_rpm,
                f1_pct,
                f2_rpm,
                f2_pct,
                getattr(snap, "disk_read_mb_s", 0.0),
                getattr(snap, "disk_write_mb_s", 0.0),
            ]
            try:
                self._writer.writerow(row)
                self._file.flush()
                self._sample_count += 1
            except OSError:
                return None

            return {
                "time": ts,
                "elapsed_s": elapsed,
                "cpu_c": snap.cpu_temp,
                "cpu_ghz": snap.cpu_freq_ghz,
                "gpu_c": snap.gpu_temp,
                "gpu_mhz": snap.gpu_clock_mhz,
                "vram_mb": snap.gpu_vram_used_mb,
                "ram_pct": snap.ram_percent,
                "fan01_rpm": f1_rpm if f1_rpm != "" else None,
            }

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self._recording:
                return False, "CSV recorder is not active"
            saved_path = self._file_path
            count = self._sample_count
            try:
                if self._file is not None:
                    self._file.flush()
                    self._file.close()
            except OSError:
                pass
            self._file = None
            self._writer = None
            self._recording = False
            return True, f"SAVED {count} TELEMETRY ROWS -> {saved_path}"

    def list_saved_logs(self) -> list[dict[str, Any]]:
        try:
            if not self.log_dir.is_dir():
                return []
            files = sorted(
                self.log_dir.glob("*.csv"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for p in files[:30]:
            try:
                st = p.stat()
                out.append(
                    {
                        "name": p.name,
                        "path": str(p),
                        "size_kb": round(st.st_size / 1024.0, 1),
                        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            except OSError:
                continue
        return out


if __name__ == "__main__":
    # Safe self-tests for all 7 modules
    pm = ProcessManager()
    procs = pm.list_top_processes(limit=5)
    assert len(procs) >= 1
    ok_kill, _ = pm.kill_process(4)
    assert not ok_kill
    print(f"[1/7] ProcessManager   : OK ({len(procs)} top procs, top={procs[0]['name']})")

    nt = NetworkTools()
    ifaces = nt.list_active_interfaces()
    pings = nt.ping_all()
    assert len(pings) == 4
    p0_ms = pings[0]["last_ms"]
    p0_txt = f"{p0_ms}ms" if p0_ms is not None else "TIMEOUT(sandbox/offline)"
    print(f"[2/7] NetworkTools     : OK ({len(ifaces)} up ifaces, 1.1.1.1={p0_txt})")

    sm = StorageMonitor()
    vols, ios = sm.get_volumes_and_health()
    assert len(vols) >= 1
    print(f"[3/7] StorageMonitor   : OK ({len(vols)} volumes, {len(ios)} disk io counters)")

    stm = StartupManager()
    start_items = stm.list_startup_items()
    assert len(start_items) >= 1
    print(f"[4/7] StartupManager   : OK ({len(start_items)} startup/service items)")

    st = StressTester()
    done_ev = threading.Event()
    done_summary: dict[str, Any] = {}
    samples_seen: list[dict[str, Any]] = []

    def _on_done(summary: dict[str, Any]) -> None:
        done_summary.update(summary)
        done_ev.set()

    ok_s, _ = st.start(mode="CPU_ONLY", duration_s=2, on_sample=samples_seen.append, on_done=_on_done)
    assert ok_s and done_ev.wait(timeout=8.0)
    assert done_summary.get("samples", 0) >= 1
    print(f"[5/7] StressTester     : OK (2s verdict={done_summary.get('verdict')})")

    dm = DisplayManager()
    disps = dm.list_displays()
    assert len(disps) >= 1
    print(f"[6/7] DisplayManager   : OK ({disps[0]['width']}x{disps[0]['height']} @ {disps[0]['current_hz']}Hz, rates={disps[0]['refresh_rates']})")

    with tempfile.TemporaryDirectory() as tmp:
        sl = SessionLogger(base_dir=Path(tmp))
        csv_path = Path(tmp) / "test_session.csv"
        ok_log, _ = sl.start(custom_path=csv_path)
        assert ok_log
        from types import SimpleNamespace

        dummy_snap = SimpleNamespace(
            cpu_temp=55.0,
            cpu_freq_ghz=3.40,
            cpu_load=22.0,
            cpu_peak_core=48.0,
            gpu_temp=46.0,
            gpu_clock_mhz=1366.0,
            gpu_mem_clock_mhz=3504.0,
            gpu_load=12.0,
            gpu_pstate="P0",
            gpu_power_w=None,
            gpu_vram_used_mb=120.0,
            gpu_vram_total_mb=3072.0,
            gpu_vram_pct=3.9,
            ram_used_gb=8.0,
            ram_os_gb=1.4,
            ram_os_kernel_gb=1.4,
            ram_apps_gb=6.6,
            ram_free_gb=6.0,
            ram_percent=57.0,
            battery_percent=100.0,
            power_status="AC PLUGGED IN",
            est_system_power_w=32.5,
            power_rate_w=32.5,
            power_plan_name="BALANCED",
            disk_read_mb_s=0.4,
            disk_write_mb_s=0.2,
            fan_state=SimpleNamespace(fans=[]),
        )
        rec = sl.record_snapshot(dummy_snap)
        assert rec is not None
        ok_stop, _ = sl.stop()
        assert ok_stop and csv_path.exists()
        assert len(sl.list_saved_logs()) == 1
    print("[7/7] SessionLogger    : OK (CSV write, flush, close, cleanup)")
