"""Hybrid multi-fan & LHM storage telemetry engine for BANKO v3.0.0.

Scans Motherboard, SuperIO, Embedded Controller (EC), Controller, GPU, and
Storage nodes via LibreHardwareMonitorLib.dll (PawnIO + pythonnet) for real
SensorType.Fan channels and NVMe/SSD temperature & life sensors. When an OEM
BIOS locks EC tachometers (0 real sensors exposed), falls back automatically
to HYBRID THERMAL CURVE ESTIMATION driven by live CPU and GPU temperatures and
clearly labeled as estimated.
"""

from __future__ import annotations

import math
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")

try:
    import clr  # type: ignore

    _CLR_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    clr = None  # type: ignore
    _CLR_IMPORT_ERROR = str(exc)

LAPTOP_CURVE_POINTS: list[tuple[float, float]] = [
    (35.0, 22.0),
    (50.0, 36.0),
    (65.0, 56.0),
    (75.0, 74.0),
    (85.0, 92.0),
    (92.0, 100.0),
]

LAPTOP_RPM_MIN = 1400.0
LAPTOP_RPM_MAX = 5500.0
DEFAULT_MAX_RPM_CHASSIS = 3000.0
DEFAULT_MAX_RPM_LAPTOP = 5500.0


@dataclass
class FanChannel:
    """Single cooling fan telemetry channel (real hardware or hybrid estimated)."""

    index: int
    label: str
    name: str
    node_type: str
    rpm: float
    duty_pct: float
    is_estimated: bool
    status_tag: str
    identifier: str


@dataclass
class FanArrayState:
    """Full fan telemetry snapshot including mode banner and channels."""

    mode: str  # "HARDWARE_SENSORS" | "HYBRID_ESTIMATED"
    mode_banner: str  # "HARDWARE TACHOMETERS ACTIVE" | "HYBRID THERMAL CURVE ESTIMATION"
    fan_count: int
    fans: list[FanChannel]
    dll_loaded: bool
    backend: str


def _resolve_lhm_dll() -> Path | None:
    """Locate LibreHardwareMonitorLib.dll across PyInstaller and source paths."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "LibreHardwareMonitorLib.dll")
    candidates.append(Path(sys.executable).resolve().parent / "LibreHardwareMonitorLib.dll")
    candidates.append(Path(__file__).resolve().parent / "LibreHardwareMonitorLib.dll")
    candidates.append(Path.cwd() / "LibreHardwareMonitorLib.dll")

    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _classify_node_type(hw_type_str: str) -> str:
    low = hw_type_str.lower()
    if "embeddedcontroller" in low or low == "ec":
        return "EC"
    if "superio" in low:
        return "SuperIO"
    if "motherboard" in low:
        return "Motherboard"
    if "gpu" in low:
        return "GPU"
    if "cooler" in low or "controller" in low:
        return "Controller"
    return "Controller"


def _normalize_sensor_key(name: str) -> str:
    text = name.lower().strip()
    for token in ("control", "fan", "#", "-", "_"):
        text = text.replace(token, " ")
    return " ".join(text.split())


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _eval_thermal_curve(temp_c: float, points: list[tuple[float, float]] = LAPTOP_CURVE_POINTS) -> float:
    if temp_c <= points[0][0]:
        return points[0][1]
    if temp_c >= points[-1][0]:
        return points[-1][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t0 <= temp_c <= t1:
            if t1 <= t0:
                return d1
            ratio = (temp_c - t0) / (t1 - t0)
            return d0 + ratio * (d1 - d0)
    return points[-1][1]


class FanEngine:
    """Thread-safe LibreHardwareMonitorLib.dll fan scanner with intelligent fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._computer: Any = None
        self._sensor_type_enum: Any = None
        self._dll_loaded = False
        self._opened = False
        self._last_error = _CLR_IMPORT_ERROR
        self._peak_rpm: dict[str, float] = {}
        self._ema_duty: dict[str, float] = {}

    def open(self) -> bool:
        with self._lock:
            return self._open_locked()

    def _open_locked(self) -> bool:
        if self._opened and self._computer is not None:
            return True

        if clr is None:
            self._last_error = _CLR_IMPORT_ERROR or "pythonnet (clr) is not installed"
            self._dll_loaded = False
            return False

        dll_path = _resolve_lhm_dll()
        if dll_path is None:
            self._last_error = "LibreHardwareMonitorLib.dll not found"
            self._dll_loaded = False
            return False

        dll_dir = str(dll_path.parent)
        if dll_dir not in sys.path:
            sys.path.insert(0, dll_dir)

        try:
            try:
                clr.AddReference(str(dll_path))
            except Exception:
                clr.AddReference("LibreHardwareMonitorLib")

            from LibreHardwareMonitor.Hardware import Computer, SensorType  # type: ignore

            self._sensor_type_enum = SensorType
            self._dll_loaded = True

            computer = Computer()
            computer.IsMotherboardEnabled = True
            computer.IsControllerEnabled = True
            computer.IsGpuEnabled = True
            computer.IsBatteryEnabled = True
            computer.IsCpuEnabled = False
            computer.IsMemoryEnabled = False
            computer.IsNetworkEnabled = False
            computer.IsStorageEnabled = True
            computer.IsPsuEnabled = False

            try:
                computer.Open()
            except Exception:
                try:
                    computer.Close()
                except Exception:
                    pass
                computer = Computer()
                computer.IsMotherboardEnabled = True
                computer.IsControllerEnabled = True
                computer.IsGpuEnabled = True
                computer.IsBatteryEnabled = True
                computer.IsCpuEnabled = False
                computer.IsMemoryEnabled = False
                computer.IsNetworkEnabled = False
                computer.IsStorageEnabled = False
                computer.IsPsuEnabled = False
                computer.Open()

            self._computer = computer
            self._opened = True
            self._last_error = ""
            return True
        except Exception as exc:
            self._last_error = str(exc)
            self._computer = None
            self._opened = False
            return False

    def _collect_node(
        self,
        hw: Any,
        seen_ids: set[str],
        raw_fans: list[tuple[str, str, float, float, str]],
        battery_powers: list[float],
    ) -> None:
        if hw is None or self._sensor_type_enum is None:
            return

        try:
            hw.Update()
        except Exception:
            pass

        hw_name = str(getattr(hw, "Name", "") or "Hardware").strip()
        hw_type_str = str(getattr(hw, "HardwareType", "") or "")
        node_type = _classify_node_type(hw_type_str)

        fan_type = self._sensor_type_enum.Fan
        ctrl_type = self._sensor_type_enum.Control
        power_type = self._sensor_type_enum.Power

        controls_by_index: dict[int, float] = {}
        controls_by_name: dict[str, float] = {}
        controls_by_norm: dict[str, float] = {}
        all_controls: list[float] = []
        fan_sensors: list[Any] = []

        try:
            sensors = list(hw.Sensors)
        except Exception:
            sensors = []

        for sensor in sensors:
            try:
                stype = sensor.SensorType
                if stype == ctrl_type:
                    val = _safe_float(sensor.Value)
                    if val is None:
                        continue
                    clamped = max(0.0, min(100.0, val))
                    idx = int(getattr(sensor, "Index", -1))
                    sname = str(getattr(sensor, "Name", "") or "").strip()
                    if idx >= 0:
                        controls_by_index[idx] = clamped
                    if sname:
                        controls_by_name[sname.lower()] = clamped
                        norm = _normalize_sensor_key(sname)
                        if norm:
                            controls_by_norm[norm] = clamped
                    all_controls.append(clamped)
                elif stype == fan_type:
                    fan_sensors.append(sensor)
                elif stype == power_type and "battery" in hw_type_str.lower():
                    pval = _safe_float(sensor.Value)
                    if pval is not None:
                        battery_powers.append(abs(pval))
            except Exception:
                continue

        for sensor in fan_sensors:
            try:
                rpm_val = _safe_float(sensor.Value)
                if rpm_val is None or rpm_val < 0.0:
                    continue

                ident = str(getattr(sensor, "Identifier", "") or "")
                sname = str(getattr(sensor, "Name", "") or "Fan").strip()
                if not ident:
                    ident = f"/{node_type.lower()}/{hw_name}/{sname}"
                if ident in seen_ids:
                    continue
                seen_ids.add(ident)

                rpm_clean = round(rpm_val, 0)
                idx = int(getattr(sensor, "Index", -1))
                sname_low = sname.lower()
                snorm = _normalize_sensor_key(sname)

                ctrl_pct: float | None = None
                if idx in controls_by_index:
                    ctrl_pct = controls_by_index[idx]
                elif sname_low in controls_by_name:
                    ctrl_pct = controls_by_name[sname_low]
                elif snorm and snorm in controls_by_norm:
                    ctrl_pct = controls_by_norm[snorm]
                elif len(all_controls) == 1 and len(fan_sensors) == 1:
                    ctrl_pct = all_controls[0]

                default_max = (
                    DEFAULT_MAX_RPM_CHASSIS
                    if node_type in ("Motherboard", "SuperIO")
                    else DEFAULT_MAX_RPM_LAPTOP
                )
                peak = max(self._peak_rpm.get(ident, 0.0), rpm_clean)
                self._peak_rpm[ident] = peak
                max_ref = max(peak, default_max)

                if ctrl_pct is not None:
                    duty_pct = round(max(0.0, min(100.0, ctrl_pct)), 1)
                elif rpm_clean <= 0.0:
                    duty_pct = 0.0
                else:
                    duty_pct = round(min(100.0, (rpm_clean / max_ref) * 100.0), 1)

                display_name = f"[{node_type}] {hw_name} - {sname}" if hw_name.lower() not in sname_low else f"[{node_type}] {sname}"
                raw_fans.append((display_name, node_type, rpm_clean, duty_pct, ident))
            except Exception:
                continue

        try:
            subs = list(hw.SubHardware)
        except Exception:
            subs = []
        for sub in subs:
            self._collect_node(sub, seen_ids, raw_fans, battery_powers)

    def _estimate_fan(self, key: str, temp_c: float | None, load_pct: float, fallback_base: float, fallback_scale: float) -> tuple[float, float]:
        effective_temp = temp_c if (temp_c is not None and temp_c > 0.0) else (fallback_base + max(0.0, min(100.0, load_pct)) * fallback_scale)
        raw_duty = _eval_thermal_curve(effective_temp) + (max(0.0, min(100.0, load_pct)) * 0.05)
        raw_duty = max(20.0, min(100.0, raw_duty))

        prev = self._ema_duty.get(key)
        smoothed = raw_duty if prev is None else (0.35 * raw_duty + 0.65 * prev)
        self._ema_duty[key] = smoothed

        duty_pct = round(smoothed, 1)
        rpm = round(LAPTOP_RPM_MIN + (duty_pct / 100.0) * (LAPTOP_RPM_MAX - LAPTOP_RPM_MIN), 0)
        return rpm, duty_pct

    def scan_fans(
        self,
        cpu_temp_c: float | None = None,
        gpu_temp_c: float | None = None,
        cpu_load_pct: float = 0.0,
        gpu_load_pct: float = 0.0,
    ) -> FanArrayState:
        """Scan real LHM fan sensors or fall back to hybrid thermal curve estimation."""
        with self._lock:
            raw_fans: list[tuple[str, str, float, float, str]] = []
            battery_powers: list[float] = []

            if self._open_locked() and self._computer is not None:
                seen_ids: set[str] = set()
                try:
                    hw_nodes = list(self._computer.Hardware)
                except Exception as exc:
                    self._last_error = str(exc)
                    hw_nodes = []
                for hw in hw_nodes:
                    self._collect_node(hw, seen_ids, raw_fans, battery_powers)

            if raw_fans:
                channels = [
                    FanChannel(
                        index=idx,
                        label=f"FAN {idx:02d}",
                        name=name,
                        node_type=node_type,
                        rpm=rpm,
                        duty_pct=duty_pct,
                        is_estimated=False,
                        status_tag="LIVE RPM (LHM)",
                        identifier=ident,
                    )
                    for idx, (name, node_type, rpm, duty_pct, ident) in enumerate(raw_fans, start=1)
                ]
                return FanArrayState(
                    mode="HARDWARE_SENSORS",
                    mode_banner="HARDWARE TACHOMETERS ACTIVE",
                    fan_count=len(channels),
                    fans=channels,
                    dll_loaded=self._dll_loaded,
                    backend="LibreHardwareMonitorLib (PawnIO)",
                )

            # Smart Laptop Fallback Mode when OEM BIOS locks raw EC tachometers
            cpu_rpm, cpu_duty = self._estimate_fan("cpu_fan", cpu_temp_c, cpu_load_pct, 38.0, 0.42)
            gpu_rpm, gpu_duty = self._estimate_fan("gpu_fan", gpu_temp_c, gpu_load_pct, 36.0, 0.44)
            estimated_channels = [
                FanChannel(
                    index=1,
                    label="FAN 01",
                    name="CPU COOLING FAN",
                    node_type="HYBRID",
                    rpm=cpu_rpm,
                    duty_pct=cpu_duty,
                    is_estimated=True,
                    status_tag="ESTIMATED RPM (EC LOCKED)",
                    identifier="/hybrid/cpu/fan01",
                ),
                FanChannel(
                    index=2,
                    label="FAN 02",
                    name="GPU COOLING FAN",
                    node_type="HYBRID",
                    rpm=gpu_rpm,
                    duty_pct=gpu_duty,
                    is_estimated=True,
                    status_tag="ESTIMATED RPM (EC LOCKED)",
                    identifier="/hybrid/gpu/fan02",
                ),
            ]
            return FanArrayState(
                mode="HYBRID_ESTIMATED",
                mode_banner="HYBRID THERMAL CURVE ESTIMATION",
                fan_count=len(estimated_channels),
                fans=estimated_channels,
                dll_loaded=self._dll_loaded,
                backend="LibreHardwareMonitorLib (PawnIO)" if self._dll_loaded else "Hybrid Estimator",
            )

    def read_battery_power_w(self) -> float | None:
        """Query LHM battery node Power sensor in Watts if exposed by firmware."""
        with self._lock:
            if not self._open_locked() or self._computer is None or self._sensor_type_enum is None:
                return None
            power_type = self._sensor_type_enum.Power
            try:
                for hw in self._computer.Hardware:
                    if "battery" not in str(getattr(hw, "HardwareType", "")).lower():
                        continue
                    try:
                        hw.Update()
                    except Exception:
                        pass
                    for s in hw.Sensors:
                        if s.SensorType == power_type:
                            val = _safe_float(s.Value)
                            if val is not None and val > 0.0:
                                return round(val, 1)
            except Exception:
                pass
            return None

    def read_storage_sensors(self) -> list[dict[str, Any]]:
        """Query LHM storage nodes for NVMe/SSD temperature (°C) and remaining life (%)."""
        results: list[dict[str, Any]] = []
        with self._lock:
            if not self._open_locked() or self._computer is None or self._sensor_type_enum is None:
                return results
            temp_type = self._sensor_type_enum.Temperature
            level_type = self._sensor_type_enum.Level
            try:
                hw_nodes = list(self._computer.Hardware)
            except Exception:
                return results

            for hw in hw_nodes:
                if "storage" not in str(getattr(hw, "HardwareType", "")).lower():
                    continue
                try:
                    hw.Update()
                except Exception:
                    pass
                name = str(getattr(hw, "Name", "") or "Storage Drive").strip()
                temp_c: float | None = None
                primary_temp: float | None = None
                life_pct: float | None = None
                try:
                    sensors = list(hw.Sensors)
                except Exception:
                    sensors = []
                for s in sensors:
                    try:
                        stype = s.SensorType
                        sname = str(getattr(s, "Name", "") or "").strip().lower()
                        val = _safe_float(s.Value)
                        if val is None:
                            continue
                        if stype == temp_type and 0.0 < val < 120.0:
                            if sname == "temperature" and primary_temp is None:
                                primary_temp = round(val, 1)
                            elif temp_c is None:
                                temp_c = round(val, 1)
                        elif stype == level_type and ("life" in sname or "remaining" in sname):
                            life_pct = round(max(0.0, min(100.0, val)), 1)
                    except Exception:
                        continue
                chosen_temp = primary_temp if primary_temp is not None else temp_c
                results.append({"name": name, "temp_c": chosen_temp, "life_pct": life_pct})
        return results

    def close(self) -> None:
        with self._lock:
            if self._computer is not None:
                try:
                    self._computer.Close()
                except Exception:
                    pass
                self._computer = None
            self._opened = False


if __name__ == "__main__":
    fe = FanEngine()
    try:
        state = fe.scan_fans(cpu_temp_c=58.0, gpu_temp_c=45.0, cpu_load_pct=25.0, gpu_load_pct=10.0)
        print(f"Backend    : {state.backend}")
        print(f"DLL Loaded : {state.dll_loaded}")
        print(f"Mode       : {state.mode} ({state.mode_banner})")
        print(f"Fan Count  : {state.fan_count}")
        for ch in state.fans:
            print(f"  {ch.label} | {ch.name:<22} | {ch.rpm:5.0f} RPM | {ch.duty_pct:5.1f}% | {ch.status_tag}")
        drives = fe.read_storage_sensors()
        print(f"Storage Sensors: {len(drives)}")
        for drv in drives:
            print(f"  {drv['name']} | Temp: {drv['temp_c']} C | Life: {drv['life_pct']}%")
    finally:
        fe.close()
