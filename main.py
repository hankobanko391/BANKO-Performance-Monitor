"""BANKO Performance Monitor v3.0.0 — Discord 3-Column Tabular Hardware Workspace."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk
from PIL import ImageTk

from fan_engine import FanArrayState, LAPTOP_CURVE_POINTS, LAPTOP_RPM_MAX, LAPTOP_RPM_MIN
from generate_icon import make_emblem
from monitor import Monitor, Snapshot
from system_modules import (
    DisplayManager,
    NetworkTools,
    ProcessManager,
    SessionLogger,
    StartupManager,
    StorageMonitor,
    StressTester,
)
from tuner_engine import (
    CPU_FREQ_MAX_GHZ,
    CPU_FREQ_MIN_GHZ,
    GPU_CLOCK_MAX_MHZ,
    GPU_CLOCK_MIN_MHZ,
    SAFE_OC_PROFILES,
    THERMAL_WATCHDOG_MAX_C,
    THERMAL_WATCHDOG_SAFE_C,
    ClockController,
    SystemBoosters,
    activate_ac_turbo_power_mode,
    ghz_to_mhz,
    is_admin,
    restore_balanced_power_mode,
    snap_cpu_freq_ghz,
    snap_gpu_clock_mhz,
)
from ui_widgets import (
    ACCENT_BLURPLE,
    AMBER_DIM,
    BG_HEADER,
    BG_HOVER,
    BG_MAIN,
    BG_RAIL,
    BG_ROW_ALT,
    BG_SIDEBAR,
    BLURPLE_DIM,
    BORDER_GRID,
    DANGER_RED,
    FONT_BTN,
    FONT_CELL,
    FONT_CELL_BOLD,
    FONT_HDR,
    FONT_KPI,
    FONT_SECTION,
    FONT_TITLE,
    FONT_XS,
    FONT_XS_BOLD,
    GREEN_DIM,
    RED_DIM,
    STATUS_GREEN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    THERMAL_AMBER,
    DataTreeTable,
    FlatMeterBar,
    HWiNFOTable,
    SectionHeaderBar,
    SharpButton,
    configure_treeview_style,
)

APP_VERSION = "3.0.0"

CHANNEL_META: dict[str, tuple[str, str, str]] = {
    "hw-telemetry": ("SYS", "# hw-telemetry", "Real-time CPU, GPU, Clock, VRAM, RAM & Power tabular sensors"),
    "fan-cooling": ("SYS", "# fan-cooling", "LibreHardwareMonitorLib.dll tachometers & Hybrid Thermal Curve"),
    "clock-overclock": ("OC", "# clock-overclock", "GPU MHz & CPU GHz overclocking, Safe Profiles & 82/86°C Watchdog"),
    "system-boosters": ("OC", "# system-boosters", "4 independent RAM, GPU, CPU & Windows OS performance boosters"),
    "process-killer": ("SYS", "# process-killer", "Live process RAM/CPU inspector, priority booster & bloat killer"),
    "network-ping": ("NET", "# network-ping", "Live ICMP ping monitor, Cloudflare/DHCP DNS switcher & cache flush"),
    "storage-health": ("DISK", "# storage-health", "NVMe/SSD temperatures, volume utilization, I/O MB/s & ReTrim"),
    "startup-optimizer": ("DISK", "# startup-optimizer", "Windows Startup Registry/Folder entries & service optimizer"),
    "display-hz": ("SYS", "# display-hz", "Active monitor resolution, bit depth & Max Hz / Battery 60Hz toggles"),
    "benchmark-stress": ("TEST", "# benchmark-stress", "30-second multi-threaded CPU/GPU compute stability & thermal test"),
    "session-logger": ("TEST", "# session-logger", "Live timestamped hardware telemetry CSV recorder & session logs"),
}

RAIL_MODES: list[tuple[str, str, str]] = [
    ("SYS", "SYS", "hw-telemetry"),
    ("OC", "OC", "clock-overclock"),
    ("NET", "NET", "network-ping"),
    ("DISK", "DISK", "storage-health"),
    ("TEST", "TEST", "benchmark-stress"),
]

SIDEBAR_CATEGORIES: list[tuple[str, list[str]]] = [
    ("TELEMETRY", ["hw-telemetry", "fan-cooling"]),
    ("TUNING", ["clock-overclock", "system-boosters"]),
    (
        "SYSTEM",
        [
            "process-killer",
            "network-ping",
            "storage-health",
            "startup-optimizer",
            "display-hz",
        ],
    ),
    ("DIAGNOSTICS", ["benchmark-stress", "session-logger"]),
]


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def _temp_color(temp_c: float | None, limit_c: float = THERMAL_WATCHDOG_MAX_C) -> str:
    if temp_c is None:
        return TEXT_MUTED
    if temp_c >= limit_c:
        return DANGER_RED
    if temp_c >= 75.0:
        return THERMAL_AMBER
    return STATUS_GREEN


def _fmt_gpu_clocks(core_mhz: float | None, mem_mhz: float | None) -> str:
    c_s = f"{core_mhz:.0f}" if core_mhz is not None else "----"
    m_s = f"{mem_mhz:.0f}" if mem_mhz is not None else "----"
    return f"{c_s} / {m_s} MHz"


class BankoWorkspaceApp(ctk.CTk):
    """BANKO v3.0.0 — Discord 3-Column Tabular Hardware & System Workspace."""

    def __init__(self, smoke_test: bool = False) -> None:
        super().__init__()
        self._smoke_test = smoke_test
        self._closing = False
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._photo_refs: list[Any] = []

        self._active_channel = "hw-telemetry"
        self._channels: dict[str, tk.Frame] = {}
        self._channel_btns: dict[str, tuple[tk.Frame, tk.Frame, tk.Button]] = {}
        self._rail_btns: dict[str, tuple[tk.Frame, tk.Frame, tk.Button]] = {}

        self.mini_on = False
        self.fps_on = False
        self.mini_win: tk.Toplevel | None = None
        self.fps_win: tk.Toplevel | None = None
        self._heartbeat = False
        self._tick_count = 0

        # Min/Max sensor tracking for HWiNFO64 table
        self._minmax: dict[str, tuple[float | None, float | None]] = {}

        # Channel refresh cadence flags
        self._proc_sort = "ram"
        self._proc_busy = False
        self._ping_busy = False
        self._auto_ping = True
        self._storage_busy = False
        self._startup_filter = "ALL"
        self._startup_items: list[dict[str, Any]] = []
        self._displays_cache: list[dict[str, Any]] = []
        self._selected_display_dev = ""
        self._stress_mode = "CPU_ONLY"
        self._last_stress_summary: dict[str, Any] | None = None

        ctk.set_appearance_mode("dark")
        self.title(f"BANKO v{APP_VERSION} — Hardware Telemetry & System Workspace")
        self.geometry("1320x800")
        self.minsize(1280, 780)
        self.resizable(True, True)
        self.configure(fg_color=BG_MAIN)
        configure_treeview_style(self)

        ico_path = resource_path("banko_icon.ico")
        if ico_path.exists():
            try:
                self.iconbitmap(str(ico_path))
            except Exception:
                pass

        # Core & v3.0.0 backend engines
        self.mon = Monitor(interval=1.0)
        self.clocks = ClockController()
        self.boosters = SystemBoosters()
        self.proc_mgr = ProcessManager()
        self.net_tools = NetworkTools()
        self.storage_mon = StorageMonitor()
        self.startup_mgr = StartupManager()
        self.stress_tester = StressTester()
        self.display_mgr = DisplayManager()
        self.session_logger = SessionLogger()

        self._gpu_slider_mhz = 1355
        self._cpu_slider_ghz = 3.20

        self._build_3col_shell()
        self._build_all_channels()

        self._switch_channel("hw-telemetry")
        self._sync_clock_ui()
        self._sync_booster_ui()

        self.mon.start()
        self._ui_tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._smoke_test:
            order = list(CHANNEL_META.keys())
            for i, ch in enumerate(order):
                self.after(220 + i * 130, lambda c=ch: self._switch_channel(c))
            self.after(220 + len(order) * 130 + 250, self._finish_smoke_test)

    # ------------------------------------------------------------------ 3-Column Shell
    def _build_3col_shell(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=64)
        self.grid_columnconfigure(1, weight=0, minsize=230)
        self.grid_columnconfigure(2, weight=1)

        # === COLUMN 0: FAR-LEFT WORKSPACE MODE RAIL (64px, #111214) ===
        rail = tk.Frame(self, bg=BG_RAIL, width=64, bd=0, highlightthickness=0)
        rail.grid(row=0, column=0, sticky="nsew")
        rail.grid_propagate(False)
        tk.Frame(rail, bg=BORDER_GRID, width=1).place(
            relx=1.0, rely=0, anchor="ne", relheight=1.0
        )

        emblem_img = ImageTk.PhotoImage(
            make_emblem(32, color=(35, 165, 90), with_tile=False)
        )
        self._photo_refs.append(emblem_img)
        tk.Label(rail, image=emblem_img, bg=BG_RAIL, bd=0).pack(pady=(12, 8))
        tk.Frame(rail, bg=BORDER_GRID, height=1).pack(fill="x", padx=12, pady=(0, 10))

        for mode_id, code_txt, target_ch in RAIL_MODES:
            wrap = tk.Frame(rail, bg=BG_RAIL, height=44)
            wrap.pack(fill="x", pady=(0, 6))
            wrap.pack_propagate(False)

            pill = tk.Frame(wrap, bg=BG_RAIL, width=3)
            pill.pack(side="left", fill="y", pady=6)

            btn = tk.Button(
                wrap,
                text=f"[{code_txt}]",
                font=FONT_XS_BOLD,
                fg=TEXT_SECONDARY,
                bg=BG_SIDEBAR,
                activebackground=BG_HOVER,
                activeforeground=TEXT_PRIMARY,
                bd=0,
                relief="flat",
                cursor="hand2",
                command=lambda ch=target_ch: self._switch_channel(ch),
            )
            btn.pack(side="left", fill="both", expand=True, padx=(5, 8), pady=2)
            self._rail_btns[mode_id] = (wrap, pill, btn)

        # Bottom Rail Admin + Heartbeat
        rail_bot = tk.Frame(rail, bg=BG_RAIL)
        rail_bot.pack(side="bottom", fill="x", pady=10)
        uac_ok = is_admin()
        tk.Label(
            rail_bot,
            text="ADM" if uac_ok else "STD",
            font=FONT_XS_BOLD,
            fg=STATUS_GREEN if uac_ok else THERMAL_AMBER,
            bg=BG_SIDEBAR,
            highlightbackground=STATUS_GREEN if uac_ok else THERMAL_AMBER,
            highlightthickness=1,
            padx=6,
            pady=2,
        ).pack(pady=(0, 6))
        self.hb_canvas = tk.Canvas(
            rail_bot, width=10, height=10, bg=BG_RAIL, highlightthickness=0, bd=0
        )
        self.hb_canvas.pack()
        self._hb_rect = self.hb_canvas.create_rectangle(
            1, 1, 9, 9, fill=STATUS_GREEN, outline=""
        )

        # === COLUMN 1: CHANNEL SIDEBAR (230px, #18191C) ===
        sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=230, bd=0, highlightthickness=0)
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        tk.Frame(sidebar, bg=BORDER_GRID, width=1).place(
            relx=1.0, rely=0, anchor="ne", relheight=1.0
        )

        # Sidebar Header (46px)
        sb_hdr = tk.Frame(sidebar, bg=BG_SIDEBAR, height=46)
        sb_hdr.grid(row=0, column=0, sticky="ew")
        sb_hdr.pack_propagate(False)
        tk.Frame(sb_hdr, bg=BORDER_GRID, height=1).pack(fill="x", side="bottom")
        tk.Label(
            sb_hdr,
            text=f"BANKO v{APP_VERSION}",
            font=FONT_TITLE,
            fg=TEXT_PRIMARY,
            bg=BG_SIDEBAR,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(5, 0))
        tk.Label(
            sb_hdr,
            text=self.mon.system_name[:28],
            font=FONT_XS,
            fg=TEXT_MUTED,
            bg=BG_SIDEBAR,
            anchor="w",
        ).pack(fill="x", padx=12)

        # Categorized Channel List
        ch_list = tk.Frame(sidebar, bg=BG_SIDEBAR)
        ch_list.grid(row=1, column=0, sticky="nsew", padx=(0, 1), pady=6)

        for cat_name, ch_keys in SIDEBAR_CATEGORIES:
            tk.Label(
                ch_list,
                text=cat_name,
                font=("Consolas", 8, "bold"),
                fg=TEXT_MUTED,
                bg=BG_SIDEBAR,
                anchor="w",
                padx=12,
            ).pack(fill="x", pady=(8, 2))
            for ch_key in ch_keys:
                row_f = tk.Frame(ch_list, bg=BG_SIDEBAR, height=28)
                row_f.pack(fill="x", padx=6, pady=1)
                row_f.pack_propagate(False)

                accent = tk.Frame(row_f, bg=BG_SIDEBAR, width=3)
                accent.pack(side="left", fill="y")

                btn = tk.Button(
                    row_f,
                    text=f"# {ch_key}",
                    font=FONT_CELL_BOLD,
                    fg=TEXT_SECONDARY,
                    bg=BG_SIDEBAR,
                    activebackground=BG_HOVER,
                    activeforeground=TEXT_PRIMARY,
                    bd=0,
                    relief="flat",
                    anchor="w",
                    padx=8,
                    cursor="hand2",
                    command=lambda c=ch_key: self._switch_channel(c),
                )
                btn.pack(side="left", fill="both", expand=True)
                self._channel_btns[ch_key] = (row_f, accent, btn)

        # Sidebar Bottom Dock (#111214)
        dock = tk.Frame(
            sidebar,
            bg=BG_RAIL,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        dock.grid(row=2, column=0, sticky="ew", padx=8, pady=8)

        self.lbl_dock_watchdog = tk.Label(
            dock,
            text="WATCHDOG: 86°C ARMED",
            font=FONT_XS_BOLD,
            fg=STATUS_GREEN,
            bg=BG_RAIL,
            anchor="w",
            padx=8,
            pady=3,
        )
        self.lbl_dock_watchdog.pack(fill="x", pady=(4, 0))

        self.lbl_dock_rec = tk.Label(
            dock,
            text="CSV REC: STANDBY",
            font=FONT_XS_BOLD,
            fg=TEXT_MUTED,
            bg=BG_RAIL,
            anchor="w",
            padx=8,
            pady=2,
        )
        self.lbl_dock_rec.pack(fill="x", pady=(0, 4))

        hud_row = tk.Frame(dock, bg=BG_RAIL)
        hud_row.pack(fill="x", padx=6, pady=(0, 6))
        hud_row.grid_columnconfigure((0, 1), weight=1, uniform="hud")
        self.btn_mini = SharpButton(
            hud_row, "MINI HUD", command=self._toggle_mini, style="muted", height=24, font=FONT_XS_BOLD
        )
        self.btn_mini.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_fps = SharpButton(
            hud_row, "FPS HUD", command=self._toggle_fps, style="muted", height=24, font=FONT_XS_BOLD
        )
        self.btn_fps.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # === COLUMN 2: MAIN TABULAR WORKSPACE (#1E1F22) ===
        workspace = tk.Frame(self, bg=BG_MAIN, bd=0, highlightthickness=0)
        workspace.grid(row=0, column=2, sticky="nsew")
        workspace.grid_rowconfigure(1, weight=1)
        workspace.grid_columnconfigure(0, weight=1)

        # Top Channel Header (46px)
        top_hdr = tk.Frame(workspace, bg=BG_MAIN, height=46)
        top_hdr.grid(row=0, column=0, sticky="ew")
        top_hdr.grid_propagate(False)
        top_hdr.grid_columnconfigure(0, weight=1)
        tk.Frame(top_hdr, bg=BORDER_GRID, height=1).place(
            relx=0, rely=1.0, anchor="sw", relwidth=1.0
        )

        hdr_left = tk.Frame(top_hdr, bg=BG_MAIN)
        hdr_left.grid(row=0, column=0, sticky="w", padx=14)
        self.lbl_ch_title = tk.Label(
            hdr_left, text="# hw-telemetry", font=FONT_TITLE, fg=TEXT_PRIMARY, bg=BG_MAIN
        )
        self.lbl_ch_title.pack(side="left", padx=(0, 12))
        self.lbl_ch_desc = tk.Label(
            hdr_left, text="", font=FONT_XS, fg=TEXT_MUTED, bg=BG_MAIN
        )
        self.lbl_ch_desc.pack(side="left")

        ticker_box = tk.Frame(
            top_hdr,
            bg=BG_RAIL,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        ticker_box.grid(row=0, column=1, sticky="e", padx=14, pady=8)
        self.lbl_header_ticker = tk.Label(
            ticker_box,
            text="GPU --°C ----/---- MHz | CPU --°C -.--GHz | RAM --.-% | BAT ---%",
            font=FONT_XS_BOLD,
            fg=STATUS_GREEN,
            bg=BG_RAIL,
            padx=10,
            pady=4,
        )
        self.lbl_header_ticker.pack()

        # Stage
        self.stage = tk.Frame(workspace, bg=BG_MAIN)
        self.stage.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        self.stage.grid_rowconfigure(0, weight=1)
        self.stage.grid_columnconfigure(0, weight=1)

        # Bottom Diagnostic Console (28px, #18191C)
        footer = tk.Frame(workspace, bg=BG_SIDEBAR, height=28)
        footer.grid(row=2, column=0, sticky="ew")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=BORDER_GRID, height=1).pack(fill="x", side="top")

        self.lbl_console = tk.Label(
            footer,
            text=self._fmt_console(f"BANKO v{APP_VERSION} 3-column hardware workspace ready"),
            font=FONT_XS,
            fg=TEXT_SECONDARY,
            bg=BG_SIDEBAR,
            anchor="w",
            padx=12,
        )
        self.lbl_console.pack(side="left", fill="x", expand=True)

        self.lbl_footer_right = tk.Label(
            footer,
            text=f"v{APP_VERSION} | {'UAC ADMIN' if uac_ok else 'UAC STD'} | LHM PAWNIO",
            font=FONT_XS_BOLD,
            fg=TEXT_MUTED,
            bg=BG_SIDEBAR,
            padx=12,
        )
        self.lbl_footer_right.pack(side="right")

    def _switch_channel(self, channel_id: str) -> None:
        if channel_id not in self._channels:
            return
        self._active_channel = channel_id
        mode_id, ch_title, ch_desc = CHANNEL_META[channel_id]

        self.lbl_ch_title.configure(text=ch_title)
        self.lbl_ch_desc.configure(text=ch_desc)

        for cid, frame in self._channels.items():
            if cid == channel_id:
                frame.grid()
            else:
                frame.grid_remove()

        for cid, (row_f, accent, btn) in self._channel_btns.items():
            if cid == channel_id:
                row_f.configure(bg=BG_HEADER)
                accent.configure(bg=ACCENT_BLURPLE)
                btn.configure(bg=BG_HEADER, fg=TEXT_PRIMARY)
            else:
                row_f.configure(bg=BG_SIDEBAR)
                accent.configure(bg=BG_SIDEBAR)
                btn.configure(bg=BG_SIDEBAR, fg=TEXT_SECONDARY)

        for r_id, (wrap, pill, btn) in self._rail_btns.items():
            if r_id == mode_id:
                pill.configure(bg=ACCENT_BLURPLE)
                btn.configure(bg=ACCENT_BLURPLE, fg=TEXT_PRIMARY)
            else:
                pill.configure(bg=BG_RAIL)
                btn.configure(bg=BG_SIDEBAR, fg=TEXT_SECONDARY)

        # Trigger immediate one-shot refresh when opening on-demand modules
        if not self._smoke_test:
            if channel_id == "process-killer":
                self._refresh_processes()
            elif channel_id == "network-ping":
                self._refresh_net_interfaces()
                self._refresh_pings()
            elif channel_id == "storage-health":
                self._refresh_storage()
            elif channel_id == "startup-optimizer":
                self._refresh_startup()
            elif channel_id == "display-hz":
                self._refresh_displays()
            elif channel_id == "session-logger":
                self._refresh_saved_logs()

    # ------------------------------------------------------------------ Build All 11 Channels
    def _build_all_channels(self) -> None:
        self._build_ch_hw_telemetry()
        self._build_ch_fan_cooling()
        self._build_ch_clock_overclock()
        self._build_ch_system_boosters()
        self._build_ch_process_killer()
        self._build_ch_network_ping()
        self._build_ch_storage_health()
        self._build_ch_startup_optimizer()
        self._build_ch_display_hz()
        self._build_ch_benchmark_stress()
        self._build_ch_session_logger()

    def _new_channel_frame(self, key: str) -> tk.Frame:
        f = tk.Frame(self.stage, bg=BG_MAIN)
        f.grid(row=0, column=0, sticky="nsew")
        f.grid_remove()
        self._channels[key] = f
        return f

    # ------------------------------------------------- [1] # hw-telemetry
    def _build_ch_hw_telemetry(self) -> None:
        page = self._new_channel_frame("hw-telemetry")
        page.grid_columnconfigure((0, 1), weight=1, uniform="hwt")
        page.grid_rowconfigure(2, weight=1)

        # Top Action Bar
        bar = tk.Frame(
            page,
            bg=BG_SIDEBAR,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        SharpButton(
            bar, "FORCE AC TURBO", command=self._on_ac_turbo_mode, style="green", width=150
        ).pack(side="left", padx=(8, 6), pady=6)
        SharpButton(
            bar, "RESTORE BALANCED", command=self._on_balanced_mode, style="neutral", width=155
        ).pack(side="left", padx=(0, 6), pady=6)
        SharpButton(
            bar, "RESET MIN/MAX", command=self._reset_minmax, style="muted", width=130
        ).pack(side="left", pady=6)

        self.lbl_hw_identity = tk.Label(
            bar,
            text=f"{self.mon.cpu_name}  |  {self.mon.cpu_cores}C/{self.mon.cpu_threads}T",
            font=FONT_XS_BOLD,
            fg=TEXT_SECONDARY,
            bg=BG_SIDEBAR,
            padx=12,
        )
        self.lbl_hw_identity.pack(side="right")

        # KPI Tabular Strip (5 cells, generous width for unclipped Core/Mem clock)
        kpi_strip = tk.Frame(page, bg=BORDER_GRID, bd=0, highlightthickness=1, highlightbackground=BORDER_GRID)
        kpi_strip.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        kpi_widths = [225, 165, 165, 185, 180]
        for idx, mw in enumerate(kpi_widths):
            kpi_strip.grid_columnconfigure(idx, weight=1, minsize=mw)

        self.kpi_gpu_clk = self._kpi_cell(kpi_strip, 0, "GPU CORE / MEM CLOCK", "---- / ---- MHz", TEXT_PRIMARY)
        self.kpi_gpu_temp = self._kpi_cell(kpi_strip, 1, "GPU TEMP / LOAD", "-- °C | -- %", STATUS_GREEN)
        self.kpi_cpu_stat = self._kpi_cell(kpi_strip, 2, "CPU TEMP / CLOCK", "-- °C | -.-- GHz", STATUS_GREEN)
        self.kpi_vram = self._kpi_cell(kpi_strip, 3, "VRAM ALLOCATION", "0 / 0 MB", ACCENT_BLURPLE)
        self.kpi_ram_pwr = self._kpi_cell(kpi_strip, 4, "RAM / EST. POWER", "--.-% | --.- W", THERMAL_AMBER)

        # Left Table: Compute & Graphics Sensors (HWiNFO64 layout)
        self.tbl_compute = HWiNFOTable(
            page,
            title="COMPUTE & GRAPHICS SENSORS (HWiNFO64 GRID)",
            columns=[
                ("SENSOR", 180, "w"),
                ("CURRENT", 150, "e"),
                ("MIN", 90, "e"),
                ("MAX", 90, "e"),
                ("STATUS", 95, "center"),
            ],
            badge_text="1000ms POLL",
            badge_color=STATUS_GREEN,
        )
        self.tbl_compute.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        for rk, label in (
            ("cpu_temp", "CPU Package Temp"),
            ("cpu_clk", "CPU Effective Clock"),
            ("cpu_load", "CPU All-Core Load"),
            ("cpu_peak", "CPU Peak Logical Core"),
            ("gpu_temp", "GPU Core Temperature"),
            ("gpu_clk", "GPU Core / Mem Clock"),
            ("gpu_load", "GPU 3D Compute Load"),
            ("gpu_vram", "GPU VRAM Used / Total"),
            ("gpu_pstate", "GPU P-State / Power"),
        ):
            self.tbl_compute.add_row(rk, [label, "--", "--", "--", "WAIT"])

        # Right Table: Memory, Power & Cooling
        self.tbl_sys_pwr = HWiNFOTable(
            page,
            title="MEMORY, POWER & HYBRID COOLING",
            columns=[
                ("SUBSYSTEM", 180, "w"),
                ("READING", 155, "e"),
                ("DETAIL", 150, "e"),
                ("STATE", 95, "center"),
            ],
            badge_text="KERNEL + EC",
            badge_color=ACCENT_BLURPLE,
        )
        self.tbl_sys_pwr.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
        for rk, label in (
            ("ram_total", "Installed / Usable RAM"),
            ("ram_os", "Windows OS Footprint"),
            ("ram_apps", "Apps & User Memory"),
            ("ram_free", "Available Free RAM"),
            ("bat_state", "AC Charger / Battery"),
            ("pwr_plan", "Active Power Plan"),
            ("pwr_est", "System Wattage Est."),
            ("fan_01", "FAN 01 (CPU)"),
            ("fan_02", "FAN 02 (GPU)"),
        ):
            self.tbl_sys_pwr.add_row(rk, [label, "--", "--", "WAIT"])

    def _kpi_cell(
        self, parent: tk.Frame, col: int, title: str, init_val: str, color: str
    ) -> tk.Label:
        cell = tk.Frame(parent, bg=BG_SIDEBAR)
        cell.grid(row=0, column=col, sticky="nsew", padx=(0, 1 if col < 4 else 0))
        tk.Label(
            cell, text=title, font=("Consolas", 8, "bold"), fg=TEXT_MUTED, bg=BG_SIDEBAR, anchor="w", padx=12
        ).pack(fill="x", pady=(6, 1))
        lbl = tk.Label(
            cell, text=init_val, font=FONT_KPI, fg=color, bg=BG_SIDEBAR, anchor="w", padx=12
        )
        lbl.pack(fill="x", pady=(0, 7))
        return lbl

    # ------------------------------------------------- [2] # fan-cooling
    def _build_ch_fan_cooling(self) -> None:
        page = self._new_channel_frame("fan-cooling")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=3)
        page.grid_rowconfigure(2, weight=2)

        hdr = tk.Frame(
            page,
            bg=BG_SIDEBAR,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_fan_backend = tk.Label(
            hdr,
            text="BACKEND: LibreHardwareMonitorLib.dll (PawnIO) // INITIALIZING",
            font=FONT_CELL_BOLD,
            fg=TEXT_PRIMARY,
            bg=BG_SIDEBAR,
            padx=10,
            pady=8,
        )
        self.lbl_fan_backend.pack(side="left")

        self.lbl_fan_banner = tk.Label(
            hdr,
            text="HYBRID THERMAL CURVE ESTIMATION",
            font=FONT_XS_BOLD,
            fg=THERMAL_AMBER,
            bg=BG_RAIL,
            padx=10,
            pady=4,
        )
        self.lbl_fan_banner.pack(side="right", padx=8)

        self.fan_tree = DataTreeTable(
            page,
            columns=[
                ("label", "CHANNEL", 90, "center"),
                ("name", "COOLING FAN / SENSOR", 280, "w"),
                ("node", "NODE TYPE", 110, "center"),
                ("rpm", "SPEED (RPM)", 130, "e"),
                ("duty", "DUTY (%)", 100, "e"),
                ("source", "TELEMETRY SOURCE", 230, "w"),
            ],
            height=8,
        )
        self.fan_tree.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        self.tbl_fan_curve = HWiNFOTable(
            page,
            title=f"HYBRID THERMAL CURVE PROFILE (FALLBACK RANGE: {LAPTOP_RPM_MIN:.0f}–{LAPTOP_RPM_MAX:.0f} RPM)",
            columns=[
                ("TEMP POINT", 160, "w"),
                ("DUTY TARGET", 140, "e"),
                ("ESTIMATED RPM", 150, "e"),
                ("THERMAL BAND", 160, "center"),
            ],
            badge_text="EC-LOCKED AUTO FALLBACK",
            badge_color=THERMAL_AMBER,
        )
        self.tbl_fan_curve.grid(row=2, column=0, sticky="nsew")
        for idx, (temp_c, duty_pct) in enumerate(LAPTOP_CURVE_POINTS):
            est_rpm = LAPTOP_RPM_MIN + (duty_pct / 100.0) * (LAPTOP_RPM_MAX - LAPTOP_RPM_MIN)
            band = "IDLE" if temp_c <= 50 else ("LOAD" if temp_c <= 75 else "HIGH THERMAL")
            col = STATUS_GREEN if temp_c <= 65 else (THERMAL_AMBER if temp_c < 85 else DANGER_RED)
            self.tbl_fan_curve.add_row(
                f"pt_{idx}",
                [f"{temp_c:.0f} °C", f"{duty_pct:.0f} %", f"{est_rpm:.0f} RPM", band],
                colors=[TEXT_PRIMARY, col, col, col],
            )

    # ------------------------------------------------- [3] # clock-overclock
    def _build_ch_clock_overclock(self) -> None:
        page = self._new_channel_frame("clock-overclock")
        page.grid_columnconfigure((0, 1), weight=1, uniform="oc")
        page.grid_rowconfigure(2, weight=1)

        # Safe Profiles & 82/86C Watchdog Bar
        prof_bar = tk.Frame(
            page,
            bg=BG_SIDEBAR,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        prof_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        tk.Label(
            prof_bar, text="SAFE PROFILES:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=10
        ).pack(side="left")
        SharpButton(
            prof_bar, "ECO", command=lambda: self._on_apply_profile("ECO"), style="green", width=90
        ).pack(side="left", padx=4, pady=6)
        SharpButton(
            prof_bar, "BALANCED", command=lambda: self._on_apply_profile("BALANCED"), style="neutral", width=105
        ).pack(side="left", padx=4, pady=6)
        SharpButton(
            prof_bar, "MAX SAFE OC", command=lambda: self._on_apply_profile("MAX SAFE OC"), style="amber", width=125
        ).pack(side="left", padx=4, pady=6)
        SharpButton(
            prof_bar, "RESET TO STOCK", command=self._on_reset_all_stock, style="red", width=140
        ).pack(side="left", padx=(8, 4), pady=6)

        # Watchdog 82C / 86C selector
        wd_right = tk.Frame(prof_bar, bg=BG_SIDEBAR)
        wd_right.pack(side="right", padx=10)
        tk.Label(
            wd_right, text="WATCHDOG:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR
        ).pack(side="left", padx=(0, 6))
        self.btn_wd_82 = SharpButton(
            wd_right, "82°C SAFE", command=lambda: self._on_set_watchdog(82.0), style="neutral", width=92, height=26
        )
        self.btn_wd_82.pack(side="left", padx=2)
        self.btn_wd_86 = SharpButton(
            wd_right, "86°C MAX", command=lambda: self._on_set_watchdog(86.0), style="green", width=92, height=26
        )
        self.btn_wd_86.pack(side="left", padx=2)
        self.lbl_oc_profile = tk.Label(
            wd_right, text="PROFILE: STOCK", font=FONT_XS_BOLD, fg=STATUS_GREEN, bg=BG_RAIL, padx=8, pady=4
        )
        self.lbl_oc_profile.pack(side="left", padx=(8, 0))

        # Left Panel: GPU Clock OC
        gpu_box = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        gpu_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        self.hdr_oc_gpu = SectionHeaderBar(
            gpu_box, "GPU CLOCK OVERCLOCK (nvidia-smi)", "STOCK", STATUS_GREEN
        )
        self.hdr_oc_gpu.pack(fill="x")

        g_inner = tk.Frame(gpu_box, bg=BG_SIDEBAR)
        g_inner.pack(fill="both", expand=True, padx=12, pady=10)

        g_read = tk.Frame(g_inner, bg=BORDER_GRID, highlightbackground=BORDER_GRID, highlightthickness=1)
        g_read.pack(fill="x", pady=(0, 10))
        g_read.grid_columnconfigure(0, weight=3, minsize=220)
        g_read.grid_columnconfigure(1, weight=2, minsize=140)
        self.lbl_oc_gpu_live = self._kpi_cell(g_read, 0, "LIVE CORE / MEM CLOCK", "---- / ---- MHz", STATUS_GREEN)
        self.lbl_oc_gpu_target = self._kpi_cell(g_read, 1, "LOCK TARGET", "STOCK", THERMAL_AMBER)

        sp_g = tk.Frame(g_inner, bg=BG_SIDEBAR)
        sp_g.pack(fill="x")
        tk.Label(
            sp_g, text="SETPOINT [800–1850 MHz | 15 MHz STEP]", font=FONT_XS, fg=TEXT_SECONDARY, bg=BG_SIDEBAR
        ).pack(side="left")
        self.lbl_oc_gpu_sp = tk.Label(
            sp_g, text="1355 MHz", font=FONT_CELL_BOLD, fg=THERMAL_AMBER, bg=BG_SIDEBAR
        )
        self.lbl_oc_gpu_sp.pack(side="right")

        gpu_ok = self.clocks.nvidia_available
        self.slider_gpu = ctk.CTkSlider(
            g_inner,
            from_=GPU_CLOCK_MIN_MHZ,
            to=GPU_CLOCK_MAX_MHZ,
            number_of_steps=70,
            height=16,
            corner_radius=2,
            button_corner_radius=2,
            button_length=12,
            fg_color=BORDER_GRID,
            progress_color=THERMAL_AMBER if gpu_ok else BG_HEADER,
            button_color=THERMAL_AMBER if gpu_ok else TEXT_MUTED,
            button_hover_color=TEXT_PRIMARY,
            state="normal" if gpu_ok else "disabled",
            command=self._on_gpu_drag,
        )
        self.slider_gpu.set(self._gpu_slider_mhz)
        self.slider_gpu.pack(fill="x", pady=(6, 8))

        g_btns = tk.Frame(g_inner, bg=BG_SIDEBAR)
        g_btns.pack(fill="x")
        g_btns.grid_columnconfigure((0, 1), weight=1)
        SharpButton(
            g_btns,
            "APPLY GPU CLOCK",
            command=self._on_gpu_apply,
            style="amber",
            state="normal" if gpu_ok else "disabled",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        SharpButton(
            g_btns,
            "RESET GPU STOCK",
            command=self._on_gpu_reset,
            style="neutral",
            state="normal" if gpu_ok else "disabled",
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # Right Panel: CPU Max Freq OC
        cpu_box = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        cpu_box.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        self.hdr_oc_cpu = SectionHeaderBar(
            cpu_box, "CPU MAX FREQUENCY (powercfg PROCFREQMAX)", "TURBO AUTO", STATUS_GREEN
        )
        self.hdr_oc_cpu.pack(fill="x")

        c_inner = tk.Frame(cpu_box, bg=BG_SIDEBAR)
        c_inner.pack(fill="both", expand=True, padx=12, pady=10)

        c_read = tk.Frame(c_inner, bg=BORDER_GRID, highlightbackground=BORDER_GRID, highlightthickness=1)
        c_read.pack(fill="x", pady=(0, 10))
        c_read.grid_columnconfigure((0, 1), weight=1, minsize=170)
        self.lbl_oc_cpu_live = self._kpi_cell(c_read, 0, "LIVE CPU CLOCK", "-.-- GHz", STATUS_GREEN)
        self.lbl_oc_cpu_target = self._kpi_cell(c_read, 1, "PROCFREQMAX CAP", "AUTO", THERMAL_AMBER)

        sp_c = tk.Frame(c_inner, bg=BG_SIDEBAR)
        sp_c.pack(fill="x")
        tk.Label(
            sp_c, text="SETPOINT [1.80–4.50 GHz | 0.10 GHz STEP]", font=FONT_XS, fg=TEXT_SECONDARY, bg=BG_SIDEBAR
        ).pack(side="left")
        self.lbl_oc_cpu_sp = tk.Label(
            sp_c, text="3.20 GHz (3200 MHz)", font=FONT_CELL_BOLD, fg=THERMAL_AMBER, bg=BG_SIDEBAR
        )
        self.lbl_oc_cpu_sp.pack(side="right")

        self.slider_cpu = ctk.CTkSlider(
            c_inner,
            from_=CPU_FREQ_MIN_GHZ,
            to=CPU_FREQ_MAX_GHZ,
            number_of_steps=27,
            height=16,
            corner_radius=2,
            button_corner_radius=2,
            button_length=12,
            fg_color=BORDER_GRID,
            progress_color=THERMAL_AMBER,
            button_color=THERMAL_AMBER,
            button_hover_color=TEXT_PRIMARY,
            command=self._on_cpu_drag,
        )
        self.slider_cpu.set(self._cpu_slider_ghz)
        self.slider_cpu.pack(fill="x", pady=(6, 8))

        c_btns = tk.Frame(c_inner, bg=BG_SIDEBAR)
        c_btns.pack(fill="x")
        c_btns.grid_columnconfigure((0, 1), weight=1)
        SharpButton(c_btns, "APPLY CPU FREQ", command=self._on_cpu_apply, style="amber").grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        SharpButton(c_btns, "TURBO AUTO (0)", command=self._on_cpu_reset, style="neutral").grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        # Bottom Table: Safe OC Profiles & Watchdog Matrix
        self.tbl_profiles = HWiNFOTable(
            page,
            title="SAFE OVERCLOCK PROFILES & THERMAL CUTOFF MATRIX",
            columns=[
                ("PROFILE", 140, "w"),
                ("GPU CLOCK TARGET", 150, "e"),
                ("CPU CAP (PROCFREQMAX)", 175, "e"),
                ("POWER SCHEME", 140, "center"),
                ("WATCHDOG CUTOFF", 140, "center"),
            ],
            badge_text="ZERO-VOLTAGE SAFE LOCK",
            badge_color=ACCENT_BLURPLE,
        )
        self.tbl_profiles.grid(row=2, column=0, columnspan=2, sticky="nsew")
        for pname, spec in SAFE_OC_PROFILES.items():
            g_str = f"{spec['gpu_mhz']} MHz" if spec["gpu_mhz"] else "STOCK AUTO"
            c_str = f"{spec['cpu_ghz']:.2f} GHz" if spec["cpu_ghz"] else "TURBO AUTO"
            p_str = "AC TURBO" if spec["power_mode"] == "AC_TURBO" else "BALANCED"
            w_str = f"{spec['watchdog_c']:.0f} °C"
            col = STATUS_GREEN if pname == "ECO" else (THERMAL_AMBER if pname == "MAX SAFE OC" else TEXT_PRIMARY)
            self.tbl_profiles.add_row(
                pname, [pname, g_str, c_str, p_str, w_str], colors=[col, TEXT_PRIMARY, TEXT_PRIMARY, col, col]
            )
        self.tbl_profiles.add_row(
            "STOCK",
            ["STOCK DEFAULT", "UNLOCKED", "TURBO AUTO (0)", "BALANCED", "82 / 86 °C"],
            colors=[TEXT_SECONDARY, TEXT_SECONDARY, TEXT_SECONDARY, TEXT_SECONDARY, STATUS_GREEN],
        )

    # ------------------------------------------------- [4] # system-boosters
    def _build_ch_system_boosters(self) -> None:
        page = self._new_channel_frame("system-boosters")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        top = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            top,
            text="DEDICATED SYSTEM OPTIMIZATION MATRIX (4 INDEPENDENT MODULES)",
            font=FONT_SECTION,
            fg=TEXT_PRIMARY,
            bg=BG_SIDEBAR,
            padx=12,
            pady=10,
        ).pack(side="left")

        self.lbl_boost_counter = tk.Label(
            top,
            text="ACTIVE: 0 / 4",
            font=FONT_CELL_BOLD,
            fg=ACCENT_BLURPLE,
            bg=BG_RAIL,
            padx=10,
            pady=4,
        )
        self.lbl_boost_counter.pack(side="right", padx=(6, 10))
        SharpButton(
            top, "ACTIVATE ALL 4 BOOSTERS", command=self._on_boost_all, style="blurple", width=210, height=30
        ).pack(side="right", padx=6, pady=6)

        # Tabular Booster Rows
        matrix = tk.Frame(
            page, bg=BG_MAIN, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        matrix.grid(row=1, column=0, sticky="nsew")
        matrix.grid_columnconfigure(0, weight=0, minsize=175)
        matrix.grid_columnconfigure(1, weight=2, minsize=290)
        matrix.grid_columnconfigure(2, weight=2, minsize=250)
        matrix.grid_columnconfigure(3, weight=0, minsize=150)

        for c_i, head in enumerate(("MODULE", "TECHNICAL ACTION", "LIVE EXECUTION STATUS", "CONTROL")):
            tk.Label(
                matrix,
                text=head,
                font=FONT_HDR,
                fg=TEXT_SECONDARY,
                bg=BG_HEADER,
                anchor="w" if c_i < 3 else "center",
                padx=12,
                pady=7,
            ).grid(row=0, column=c_i, sticky="ew")

        booster_specs = [
            (
                "ram",
                "[01] RAM BOOST",
                "Trims process working sets (EmptyWorkingSet) and purges the Windows Standby Memory List to reclaim RAM immediately.",
                self._on_boost_ram,
            ),
            (
                "gpu",
                "[02] GPU BOOST",
                "Enforces NVIDIA P0 persistence (-pm 1), DirectX High-Perf GPU rules, PCIe ASPM=0 on AC, and HAGS scheduling.",
                self._on_boost_gpu,
            ),
            (
                "cpu",
                "[03] CPU BOOST",
                "Unparks 100% logical CPU cores (CPMINCORES=100), locks AC min/max state to 100%, and activates Aggressive Boost.",
                self._on_boost_cpu,
            ),
            (
                "os",
                "[04] WINDOWS OS BOOST",
                "Stops SysMain & DiagTrack services, purges temp/shader caches, flushes DNS, and elevates Game Mode priority.",
                self._on_boost_os,
            ),
        ]
        self.booster_widgets: dict[str, tuple[tk.Label, tk.Label, ctk.CTkButton]] = {}
        for r_idx, (key, title, desc, cmd) in enumerate(booster_specs, start=1):
            matrix.grid_rowconfigure(r_idx, weight=1)
            bg = BG_MAIN if (r_idx % 2 == 1) else BG_ROW_ALT

            c0 = tk.Frame(matrix, bg=bg)
            c0.grid(row=r_idx, column=0, sticky="nsew", pady=(1, 0))
            tk.Label(c0, text=title, font=FONT_CELL_BOLD, fg=TEXT_PRIMARY, bg=bg, anchor="w", padx=12).pack(
                fill="x", pady=(16, 4)
            )
            badge = tk.Label(c0, text="STANDBY", font=FONT_XS_BOLD, fg=TEXT_MUTED, bg=BG_RAIL, padx=8, pady=2)
            badge.pack(anchor="w", padx=12)

            c1 = tk.Frame(matrix, bg=bg)
            c1.grid(row=r_idx, column=1, sticky="nsew", pady=(1, 0))
            tk.Label(
                c1,
                text=desc,
                font=FONT_XS,
                fg=TEXT_SECONDARY,
                bg=bg,
                justify="left",
                anchor="w",
                wraplength=330,
                padx=12,
            ).pack(fill="both", expand=True)

            c2 = tk.Frame(matrix, bg=bg)
            c2.grid(row=r_idx, column=2, sticky="nsew", pady=(1, 0))
            status_lbl = tk.Label(
                c2,
                text="STANDBY",
                font=FONT_XS_BOLD,
                fg=TEXT_SECONDARY,
                bg=bg,
                anchor="w",
                wraplength=280,
                justify="left",
                padx=12,
            )
            status_lbl.pack(fill="both", expand=True)

            c3 = tk.Frame(matrix, bg=bg)
            c3.grid(row=r_idx, column=3, sticky="nsew", pady=(1, 0))
            btn = SharpButton(c3, "ACTIVATE BOOST", command=cmd, style="green", width=136, height=32)
            btn.place(relx=0.5, rely=0.5, anchor="center")

            self.booster_widgets[key] = (badge, status_lbl, btn)

    # ------------------------------------------------- [5] # process-killer
    def _build_ch_process_killer(self) -> None:
        page = self._new_channel_frame("process-killer")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        tk.Label(bar, text="FILTER:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=8).pack(side="left")
        self.ent_proc_filter = ctk.CTkEntry(
            bar,
            width=190,
            height=28,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_GRID,
            fg_color=BG_RAIL,
            text_color=TEXT_PRIMARY,
            placeholder_text="process name or PID...",
            font=FONT_CELL,
        )
        self.ent_proc_filter.pack(side="left", padx=(0, 8), pady=6)
        self.ent_proc_filter.bind("<Return>", lambda _e: self._refresh_processes())

        self.btn_sort_ram = SharpButton(
            bar, "SORT: RAM", command=lambda: self._set_proc_sort("ram"), style="blurple", width=95
        )
        self.btn_sort_ram.pack(side="left", padx=2, pady=6)
        self.btn_sort_cpu = SharpButton(
            bar, "SORT: CPU", command=lambda: self._set_proc_sort("cpu"), style="neutral", width=95
        )
        self.btn_sort_cpu.pack(side="left", padx=(2, 10), pady=6)

        SharpButton(bar, "REFRESH", command=self._refresh_processes, style="neutral", width=85).pack(
            side="right", padx=(4, 8), pady=6
        )
        SharpButton(bar, "KILL TASK", command=self._on_kill_process, style="red", width=100).pack(
            side="right", padx=4, pady=6
        )
        SharpButton(
            bar, "SET NORMAL", command=lambda: self._on_set_priority(False), style="neutral", width=105
        ).pack(side="right", padx=4, pady=6)
        SharpButton(
            bar, "SET HIGH PRIORITY", command=lambda: self._on_set_priority(True), style="green", width=155
        ).pack(side="right", padx=4, pady=6)

        self.proc_tree = DataTreeTable(
            page,
            columns=[
                ("pid", "PID", 75, "e"),
                ("name", "PROCESS IMAGE", 260, "w"),
                ("cpu", "CPU %", 85, "e"),
                ("ram", "RAM (MB)", 110, "e"),
                ("priority", "PRIORITY", 125, "center"),
                ("tag", "CLASSIFICATION", 120, "center"),
            ],
            height=18,
        )
        self.proc_tree.grid(row=1, column=0, sticky="nsew")

    # ------------------------------------------------- [6] # network-ping
    def _build_ch_network_ping(self) -> None:
        page = self._new_channel_frame("network-ping")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        # Row 0: DNS Switcher Toolbar
        dns_bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        dns_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        tk.Label(dns_bar, text="ADAPTER:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=8).pack(side="left")

        self.var_net_iface = ctk.StringVar(value="Detecting...")
        self.opt_net_iface = ctk.CTkOptionMenu(
            dns_bar,
            variable=self.var_net_iface,
            values=["Detecting..."],
            width=180,
            height=28,
            corner_radius=2,
            fg_color=BG_RAIL,
            button_color=BG_HEADER,
            button_hover_color=BG_HOVER,
            dropdown_fg_color=BG_SIDEBAR,
            text_color=TEXT_PRIMARY,
            font=FONT_XS_BOLD,
            command=lambda _v: self._update_dns_label(),
        )
        self.opt_net_iface.pack(side="left", padx=(0, 8), pady=6)

        self.lbl_net_dns = tk.Label(
            dns_bar, text="DNS: --", font=FONT_XS_BOLD, fg=STATUS_GREEN, bg=BG_RAIL, padx=8, pady=4
        )
        self.lbl_net_dns.pack(side="left", padx=(0, 8))

        SharpButton(dns_bar, "FLUSH DNS CACHE", command=self._on_flush_dns, style="amber", width=138).pack(
            side="right", padx=(4, 8), pady=6
        )
        SharpButton(dns_bar, "AUTOMATIC DHCP", command=lambda: self._on_set_dns("dhcp"), style="neutral", width=130).pack(
            side="right", padx=3, pady=6
        )
        SharpButton(dns_bar, "GOOGLE 8.8.8.8", command=lambda: self._on_set_dns("google"), style="neutral", width=128).pack(
            side="right", padx=3, pady=6
        )
        SharpButton(
            dns_bar, "CLOUDFLARE GAMING DNS", command=lambda: self._on_set_dns("cloudflare"), style="blurple", width=180
        ).pack(side="right", padx=3, pady=6)

        # Row 1: Ping Controls
        ping_bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        ping_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Label(ping_bar, text="CUSTOM TARGET:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=8).pack(side="left")
        self.ent_ping_host = ctk.CTkEntry(
            ping_bar,
            width=210,
            height=28,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_GRID,
            fg_color=BG_RAIL,
            text_color=TEXT_PRIMARY,
            placeholder_text="optional host or IP (e.g. 1.1.1.1)",
            font=FONT_CELL,
        )
        self.ent_ping_host.pack(side="left", padx=(0, 8), pady=6)
        SharpButton(ping_bar, "PING NOW", command=self._refresh_pings, style="green", width=100).pack(
            side="left", padx=4, pady=6
        )
        self.btn_auto_ping = SharpButton(
            ping_bar, "AUTO-PING: ON (3s)", command=self._toggle_auto_ping, style="blurple", width=165
        )
        self.btn_auto_ping.pack(side="right", padx=8, pady=6)

        self.net_tree = DataTreeTable(
            page,
            columns=[
                ("host", "ENDPOINT IP / HOST", 160, "w"),
                ("label", "GAMING / DNS ROUTE", 230, "w"),
                ("last", "LAST (ms)", 95, "e"),
                ("min", "MIN (ms)", 90, "e"),
                ("avg", "AVG (ms)", 90, "e"),
                ("max", "MAX (ms)", 90, "e"),
                ("loss", "LOSS %", 80, "e"),
                ("status", "LINK QUALITY", 110, "center"),
            ],
            height=16,
        )
        self.net_tree.grid(row=2, column=0, sticky="nsew")

    # ------------------------------------------------- [7] # storage-health
    def _build_ch_storage_health(self) -> None:
        page = self._new_channel_frame("storage-health")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=3)
        page.grid_rowconfigure(2, weight=2)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_disk_io_strip = tk.Label(
            bar,
            text="THROUGHPUT // READ: 0.00 MB/s  |  WRITE: 0.00 MB/s",
            font=FONT_CELL_BOLD,
            fg=STATUS_GREEN,
            bg=BG_RAIL,
            padx=10,
            pady=4,
        )
        self.lbl_disk_io_strip.pack(side="left", padx=8, pady=6)

        SharpButton(bar, "REFRESH", command=self._refresh_storage, style="neutral", width=95).pack(
            side="right", padx=(4, 8), pady=6
        )
        SharpButton(
            bar, "OPTIMIZE / RE-TRIM VOLUME", command=self._on_retrim_volume, style="blurple", width=215
        ).pack(side="right", padx=4, pady=6)

        self.var_retrim_drive = ctk.StringVar(value="C:")
        self.opt_retrim_drive = ctk.CTkOptionMenu(
            bar,
            variable=self.var_retrim_drive,
            values=["C:"],
            width=80,
            height=28,
            corner_radius=2,
            fg_color=BG_RAIL,
            button_color=BG_HEADER,
            button_hover_color=BG_HOVER,
            dropdown_fg_color=BG_SIDEBAR,
            text_color=TEXT_PRIMARY,
            font=FONT_CELL_BOLD,
        )
        self.opt_retrim_drive.pack(side="right", padx=6, pady=6)

        self.vol_tree = DataTreeTable(
            page,
            columns=[
                ("drive", "DRIVE", 70, "center"),
                ("fs", "FS", 70, "center"),
                ("model", "STORAGE DEVICE", 220, "w"),
                ("bus", "BUS", 75, "center"),
                ("total", "TOTAL (GB)", 95, "e"),
                ("used", "USED (GB)", 95, "e"),
                ("free", "FREE (GB)", 95, "e"),
                ("pct", "USED %", 80, "e"),
                ("temp", "TEMP °C", 80, "e"),
                ("health", "HEALTH", 95, "center"),
            ],
            height=8,
        )
        self.vol_tree.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        self.disk_io_tree = DataTreeTable(
            page,
            columns=[
                ("disk", "PHYSICAL DISK", 140, "w"),
                ("model", "DEVICE MODEL", 250, "w"),
                ("rmbs", "READ (MB/s)", 115, "e"),
                ("wmbs", "WRITE (MB/s)", 115, "e"),
                ("rops", "READ OPS", 105, "e"),
                ("wops", "WRITE OPS", 105, "e"),
                ("temp", "TEMP °C", 85, "e"),
            ],
            height=6,
        )
        self.disk_io_tree.grid(row=2, column=0, sticky="nsew")

    # ------------------------------------------------- [8] # startup-optimizer
    def _build_ch_startup_optimizer(self) -> None:
        page = self._new_channel_frame("startup-optimizer")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.btn_st_all = SharpButton(
            bar, "ALL", command=lambda: self._set_startup_filter("ALL"), style="blurple", width=72
        )
        self.btn_st_all.pack(side="left", padx=(8, 3), pady=6)
        self.btn_st_apps = SharpButton(
            bar, "STARTUP APPS", command=lambda: self._set_startup_filter("STARTUP APP"), style="neutral", width=118
        )
        self.btn_st_apps.pack(side="left", padx=3, pady=6)
        self.btn_st_svcs = SharpButton(
            bar, "SERVICES", command=lambda: self._set_startup_filter("SERVICE"), style="neutral", width=95
        )
        self.btn_st_svcs.pack(side="left", padx=3, pady=6)

        self.lbl_startup_count = tk.Label(
            bar, text="ITEMS: 0", font=FONT_XS_BOLD, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=10
        )
        self.lbl_startup_count.pack(side="left")

        SharpButton(bar, "REFRESH", command=self._refresh_startup, style="neutral", width=90).pack(
            side="right", padx=(4, 8), pady=6
        )
        SharpButton(
            bar, "TOGGLE ENABLE / DISABLE", command=self._on_toggle_startup, style="amber", width=195
        ).pack(side="right", padx=4, pady=6)

        self.startup_tree = DataTreeTable(
            page,
            columns=[
                ("name", "STARTUP ENTRY / SERVICE", 220, "w"),
                ("cat", "CATEGORY", 105, "center"),
                ("source", "LOCATION", 125, "w"),
                ("impact", "IMPACT", 85, "center"),
                ("status", "STATE", 135, "center"),
                ("cmd", "COMMAND / TARGET", 300, "w"),
            ],
            height=18,
            on_double_click=self._on_toggle_startup,
        )
        self.startup_tree.grid(row=1, column=0, sticky="nsew")

    # ------------------------------------------------- [9] # display-hz
    def _build_ch_display_hz(self) -> None:
        page = self._new_channel_frame("display-hz")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=2)
        page.grid_rowconfigure(3, weight=3)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.lbl_disp_summary = tk.Label(
            bar, text="ACTIVE DISPLAY: DETECTING...", font=FONT_CELL_BOLD, fg=TEXT_PRIMARY, bg=BG_SIDEBAR, padx=10
        )
        self.lbl_disp_summary.pack(side="left", pady=6)

        SharpButton(bar, "REFRESH", command=self._refresh_displays, style="neutral", width=90).pack(
            side="right", padx=(4, 8), pady=6
        )
        SharpButton(bar, "BATTERY 60Hz", command=self._on_display_60hz, style="amber", width=128).pack(
            side="right", padx=4, pady=6
        )
        SharpButton(bar, "MAX REFRESH RATE", command=self._on_display_max_hz, style="green", width=155).pack(
            side="right", padx=4, pady=6
        )

        # Dynamic Hz buttons strip
        self.hz_strip = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        self.hz_strip.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            self.hz_strip, text="QUICK HZ SWITCH:", font=FONT_HDR, fg=TEXT_MUTED, bg=BG_SIDEBAR, padx=10, pady=8
        ).pack(side="left")
        self.hz_btn_host = tk.Frame(self.hz_strip, bg=BG_SIDEBAR)
        self.hz_btn_host.pack(side="left", fill="x", expand=True)

        self.disp_tree = DataTreeTable(
            page,
            columns=[
                ("dev", "DISPLAY OUTPUT", 145, "w"),
                ("adapter", "GRAPHICS ADAPTER", 250, "w"),
                ("res", "RESOLUTION", 130, "center"),
                ("bpp", "BIT DEPTH", 90, "center"),
                ("cur_hz", "CURRENT Hz", 105, "e"),
                ("max_hz", "MAX Hz", 95, "e"),
                ("primary", "ROLE", 95, "center"),
            ],
            height=6,
        )
        self.disp_tree.grid(row=2, column=0, sticky="nsew", pady=(0, 8))

        self.disp_modes_tree = DataTreeTable(
            page,
            columns=[
                ("dev", "DISPLAY", 145, "w"),
                ("res", "RESOLUTION", 150, "center"),
                ("bpp", "COLOR", 105, "center"),
                ("hz", "REFRESH RATE", 130, "e"),
                ("state", "STATUS (DOUBLE-CLICK TO APPLY)", 240, "center"),
            ],
            height=9,
            on_double_click=self._on_apply_selected_hz_mode,
        )
        self.disp_modes_tree.grid(row=3, column=0, sticky="nsew")

    # ------------------------------------------------- [10] # benchmark-stress
    def _build_ch_benchmark_stress(self) -> None:
        page = self._new_channel_frame("benchmark-stress")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.btn_mode_cpu = SharpButton(
            bar, "CPU ALL-CORE 30s", command=lambda: self._set_stress_mode("CPU_ONLY"), style="blurple", width=155
        )
        self.btn_mode_cpu.pack(side="left", padx=(8, 4), pady=6)
        self.btn_mode_cpugpu = SharpButton(
            bar, "CPU + GPU LOAD 30s", command=lambda: self._set_stress_mode("CPU_GPU"), style="neutral", width=165
        )
        self.btn_mode_cpugpu.pack(side="left", padx=4, pady=6)

        SharpButton(bar, "CLEAR TABLE", command=lambda: self.stress_tree.clear(), style="muted", width=110).pack(
            side="right", padx=(4, 8), pady=6
        )
        self.btn_stress_abort = SharpButton(
            bar, "ABORT TEST", command=self._on_stress_abort, style="red", width=110, state="disabled"
        )
        self.btn_stress_abort.pack(side="right", padx=4, pady=6)
        self.btn_stress_start = SharpButton(
            bar, "START 30s STRESS TEST", command=self._on_stress_start, style="green", width=190
        )
        self.btn_stress_start.pack(side="right", padx=4, pady=6)

        kpi = tk.Frame(page, bg=BORDER_GRID, highlightbackground=BORDER_GRID, highlightthickness=1)
        kpi.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for idx, mw in enumerate((130, 160, 185, 195, 220)):
            kpi.grid_columnconfigure(idx, weight=1, minsize=mw)
        self.kpi_stress_timer = self._kpi_cell(kpi, 0, "TIMER", "00 / 30 s", TEXT_PRIMARY)
        self.kpi_stress_ops = self._kpi_cell(kpi, 1, "COMPUTE RATE", "-- kOps/s", ACCENT_BLURPLE)
        self.kpi_stress_peak = self._kpi_cell(kpi, 2, "PEAK CPU / GPU", "-- / -- °C", THERMAL_AMBER)
        self.kpi_stress_clks = self._kpi_cell(kpi, 3, "AVG CLOCKS", "-.-- GHz / ---- MHz", TEXT_PRIMARY)
        self.kpi_stress_verdict = self._kpi_cell(kpi, 4, "STABILITY VERDICT", "READY", STATUS_GREEN)

        self.stress_tree = DataTreeTable(
            page,
            columns=[
                ("sec", "SEC", 65, "center"),
                ("cpu_t", "CPU °C", 85, "e"),
                ("cpu_clk", "CPU GHz", 95, "e"),
                ("cpu_load", "CPU %", 85, "e"),
                ("gpu_t", "GPU °C", 85, "e"),
                ("gpu_clk", "GPU MHz", 95, "e"),
                ("gpu_load", "GPU %", 85, "e"),
                ("ops", "kOps/s", 95, "e"),
                ("status", "STABILITY", 145, "center"),
            ],
            height=15,
        )
        self.stress_tree.grid(row=2, column=0, sticky="nsew")

    # ------------------------------------------------- [11] # session-logger
    def _build_ch_session_logger(self) -> None:
        page = self._new_channel_frame("session-logger")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=3)
        page.grid_rowconfigure(3, weight=2)

        bar = tk.Frame(
            page, bg=BG_SIDEBAR, highlightbackground=BORDER_GRID, highlightthickness=1, bd=0
        )
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.btn_log_toggle = SharpButton(
            bar, "START CSV RECORDING", command=self._on_toggle_recording, style="green", width=185
        )
        self.btn_log_toggle.pack(side="left", padx=(8, 6), pady=6)
        SharpButton(bar, "CHOOSE CSV PATH", command=self._on_choose_log_path, style="neutral", width=145).pack(
            side="left", padx=4, pady=6
        )
        SharpButton(bar, "OPEN LOGS FOLDER", command=self._on_open_logs_dir, style="blurple", width=155).pack(
            side="left", padx=4, pady=6
        )
        SharpButton(bar, "REFRESH REPORTS", command=self._refresh_saved_logs, style="neutral", width=140).pack(
            side="right", padx=8, pady=6
        )

        kpi = tk.Frame(page, bg=BORDER_GRID, highlightbackground=BORDER_GRID, highlightthickness=1)
        kpi.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for idx, mw in enumerate((140, 120, 130, 130, 290)):
            kpi.grid_columnconfigure(idx, weight=1 if idx == 4 else 0, minsize=mw)
        self.kpi_log_state = self._kpi_cell(kpi, 0, "RECORDER", "STANDBY", TEXT_MUTED)
        self.kpi_log_samples = self._kpi_cell(kpi, 1, "SAMPLES", "0", TEXT_PRIMARY)
        self.kpi_log_elapsed = self._kpi_cell(kpi, 2, "ELAPSED", "0.0 s", TEXT_PRIMARY)
        self.kpi_log_size = self._kpi_cell(kpi, 3, "FILE SIZE", "0.0 KB", ACCENT_BLURPLE)
        self.kpi_log_path = self._kpi_cell(kpi, 4, "ACTIVE CSV FILE", str(self.session_logger.log_dir), TEXT_SECONDARY)

        self.log_live_tree = DataTreeTable(
            page,
            columns=[
                ("ts", "TIME", 95, "center"),
                ("el", "ELAPSED", 85, "e"),
                ("ct", "CPU °C", 80, "e"),
                ("cf", "CPU GHz", 90, "e"),
                ("gt", "GPU °C", 80, "e"),
                ("gc", "GPU MHz", 90, "e"),
                ("vram", "VRAM MB", 95, "e"),
                ("ram", "RAM %", 80, "e"),
                ("fan", "FAN 01 RPM", 105, "e"),
            ],
            height=9,
        )
        self.log_live_tree.grid(row=2, column=0, sticky="nsew", pady=(0, 8))

        self.log_files_tree = DataTreeTable(
            page,
            columns=[
                ("name", "SAVED CSV REPORT", 250, "w"),
                ("size", "SIZE (KB)", 100, "e"),
                ("mod", "MODIFIED", 165, "center"),
                ("path", "FULL PATH", 380, "w"),
            ],
            height=6,
        )
        self.log_files_tree.grid(row=3, column=0, sticky="nsew")

    # ------------------------------------------------------------------ Actions & Async Helper
    def _run_bg(self, fn: Any, after_ui: Any = None) -> None:
        def _worker() -> None:
            try:
                ok, msg = fn()
                self._status_queue.put(msg if ok else f"ERR: {msg}")
            except Exception as exc:
                self._status_queue.put(f"ERR: {exc}")
            if after_ui is not None and not self._closing:
                self.after(0, after_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _reset_minmax(self) -> None:
        self._minmax.clear()
        self._status_queue.put("MIN/MAX TELEMETRY COUNTERS RESET")

    def _on_ac_turbo_mode(self) -> None:
        def _task() -> tuple[bool, str]:
            res = activate_ac_turbo_power_mode()
            self.mon.refresh_power_plan()
            return res

        self._run_bg(_task)

    def _on_balanced_mode(self) -> None:
        def _task() -> tuple[bool, str]:
            res = restore_balanced_power_mode()
            self.mon.refresh_power_plan()
            return res

        self._run_bg(_task)

    # Clock & OC callbacks
    def _on_gpu_drag(self, val: float) -> None:
        snapped = snap_gpu_clock_mhz(val)
        self._gpu_slider_mhz = snapped
        self.lbl_oc_gpu_sp.configure(text=f"{snapped:4d} MHz")

    def _on_gpu_apply(self) -> None:
        target = snap_gpu_clock_mhz(self.slider_gpu.get())
        self._gpu_slider_mhz = target
        self.lbl_oc_gpu_sp.configure(text=f"{target:4d} MHz")
        self._run_bg(lambda: self.clocks.apply_gpu_clock(target), self._sync_clock_ui)

    def _on_gpu_reset(self) -> None:
        self._run_bg(self.clocks.reset_gpu, self._sync_clock_ui)

    def _on_cpu_drag(self, val: float) -> None:
        snapped = snap_cpu_freq_ghz(val)
        self._cpu_slider_ghz = snapped
        mhz = ghz_to_mhz(snapped)
        self.lbl_oc_cpu_sp.configure(text=f"{snapped:4.2f} GHz ({mhz:4d} MHz)")

    def _on_cpu_apply(self) -> None:
        snapped = snap_cpu_freq_ghz(self.slider_cpu.get())
        self._cpu_slider_ghz = snapped
        self._run_bg(lambda: self.clocks.apply_cpu_freq(snapped), self._sync_clock_ui)

    def _on_cpu_reset(self) -> None:
        self._run_bg(self.clocks.reset_cpu, self._sync_clock_ui)

    def _on_apply_profile(self, name: str) -> None:
        snap = self.mon.get()

        def _task() -> tuple[bool, str]:
            res = self.clocks.apply_profile(name, detected_max_gpu_mhz=snap.gpu_max_clock_mhz)
            self.mon.refresh_power_plan()
            return res

        self._run_bg(_task, self._sync_clock_ui)

    def _on_reset_all_stock(self) -> None:
        def _task() -> tuple[bool, str]:
            res = self.clocks.reset_to_stock()
            self.mon.refresh_power_plan()
            return res

        self._run_bg(_task, self._sync_clock_ui)

    def _on_set_watchdog(self, limit_c: float) -> None:
        ok, msg = self.clocks.set_watchdog_limit(limit_c)
        self._status_queue.put(msg if ok else f"ERR: {msg}")
        self._sync_clock_ui()

    def _sync_clock_ui(self) -> None:
        st = self.clocks.get_state()
        if not st.nvidia_available:
            self.hdr_oc_gpu.set_badge("NO NVIDIA GPU", TEXT_MUTED)
            self.lbl_oc_gpu_target.configure(text="N/A", fg=TEXT_MUTED)
        elif st.target_gpu_mhz is not None:
            self.hdr_oc_gpu.set_badge("LOCKED", THERMAL_AMBER)
            self.lbl_oc_gpu_target.configure(text=f"{st.target_gpu_mhz} MHz", fg=THERMAL_AMBER)
        else:
            self.hdr_oc_gpu.set_badge("STOCK", STATUS_GREEN)
            self.lbl_oc_gpu_target.configure(text="STOCK", fg=TEXT_SECONDARY)

        if st.watchdog_limit_c <= THERMAL_WATCHDOG_SAFE_C:
            self.btn_wd_82.configure(fg_color=STATUS_GREEN, border_color=STATUS_GREEN, text_color=TEXT_PRIMARY)
            self.btn_wd_86.configure(fg_color=BG_HEADER, border_color=BORDER_GRID, text_color=TEXT_SECONDARY)
        else:
            self.btn_wd_82.configure(fg_color=BG_HEADER, border_color=BORDER_GRID, text_color=TEXT_SECONDARY)
            self.btn_wd_86.configure(fg_color=STATUS_GREEN, border_color=STATUS_GREEN, text_color=TEXT_PRIMARY)

        if st.watchdog_tripped:
            self.lbl_dock_watchdog.configure(text="WATCHDOG: TRIPPED!", fg=DANGER_RED)
        else:
            self.lbl_dock_watchdog.configure(
                text=f"WATCHDOG: {st.watchdog_limit_c:.0f}°C ARMED", fg=STATUS_GREEN
            )

        self.lbl_oc_profile.configure(
            text=f"PROFILE: {st.active_profile}",
            fg=THERMAL_AMBER if st.active_profile not in ("STOCK", "BALANCED") else STATUS_GREEN,
        )

        if st.target_cpu_ghz is not None and st.target_cpu_mhz > 0:
            snapped = snap_cpu_freq_ghz(st.target_cpu_ghz)
            self._cpu_slider_ghz = snapped
            self.slider_cpu.set(snapped)
            self.lbl_oc_cpu_sp.configure(text=f"{snapped:4.2f} GHz ({st.target_cpu_mhz:4d} MHz)")
            self.hdr_oc_cpu.set_badge("CAPPED", THERMAL_AMBER)
            self.lbl_oc_cpu_target.configure(text=f"{snapped:.2f} GHz", fg=THERMAL_AMBER)
        else:
            self.hdr_oc_cpu.set_badge("TURBO AUTO", STATUS_GREEN)
            self.lbl_oc_cpu_target.configure(text="AUTO", fg=TEXT_SECONDARY)

    # Boosters
    def _on_boost_ram(self) -> None:
        self._run_bg(self.boosters.activate_ram_boost, self._sync_booster_ui)

    def _on_boost_gpu(self) -> None:
        self._run_bg(lambda: self.boosters.toggle_gpu_boost(None), self._sync_booster_ui)

    def _on_boost_cpu(self) -> None:
        self._run_bg(lambda: self.boosters.toggle_cpu_boost(None), self._sync_booster_ui)

    def _on_boost_os(self) -> None:
        self._run_bg(lambda: self.boosters.toggle_windows_os_boost(None), self._sync_booster_ui)

    def _on_boost_all(self) -> None:
        self._run_bg(self.boosters.activate_all, self._sync_booster_ui)

    def _sync_booster_ui(self) -> None:
        self.lbl_boost_counter.configure(text=f"ACTIVE: {self.boosters.active_count()} / 4")
        mapping = {
            "ram": (self.boosters.ram_active, self.boosters.ram_summary, True),
            "gpu": (self.boosters.gpu_active, self.boosters.gpu_summary, False),
            "cpu": (self.boosters.cpu_active, self.boosters.cpu_summary, False),
            "os": (self.boosters.os_active, self.boosters.os_summary, False),
        }
        for key, (active, summary, one_shot) in mapping.items():
            badge, status_lbl, btn = self.booster_widgets[key]
            badge.configure(
                text="ACTIVE" if active else "STANDBY",
                fg=STATUS_GREEN if active else TEXT_MUTED,
            )
            status_lbl.configure(
                text=summary,
                fg=STATUS_GREEN if active else TEXT_SECONDARY,
            )
            if one_shot:
                btn.configure(text="FLUSH AGAIN" if active else "FLUSH RAM")
            else:
                btn.configure(
                    text="DEACTIVATE" if active else "ACTIVATE",
                    border_color=THERMAL_AMBER if active else STATUS_GREEN,
                    fg_color=AMBER_DIM if active else GREEN_DIM,
                    text_color=THERMAL_AMBER if active else STATUS_GREEN,
                )

    # [5] Process Killer callbacks
    def _set_proc_sort(self, sort_by: str) -> None:
        self._proc_sort = sort_by
        if sort_by == "ram":
            self.btn_sort_ram.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE, text_color=TEXT_PRIMARY)
            self.btn_sort_cpu.configure(fg_color=BG_HEADER, border_color=BORDER_GRID, text_color=TEXT_PRIMARY)
        else:
            self.btn_sort_ram.configure(fg_color=BG_HEADER, border_color=BORDER_GRID, text_color=TEXT_PRIMARY)
            self.btn_sort_cpu.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE, text_color=TEXT_PRIMARY)
        self._refresh_processes()

    def _refresh_processes(self) -> None:
        if self._proc_busy or self._closing:
            return
        self._proc_busy = True
        q = self.ent_proc_filter.get()
        sort_by = self._proc_sort

        def _work() -> None:
            try:
                rows = self.proc_mgr.list_top_processes(sort_by=sort_by, limit=35, query=q)
                if not self._closing:
                    self.after(0, lambda: self._render_processes(rows))
            finally:
                self._proc_busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _render_processes(self, rows: list[dict[str, Any]]) -> None:
        table_rows = []
        tones = []
        for r in rows:
            table_rows.append(
                [
                    r["pid"],
                    r["name"],
                    f"{r['cpu_pct']:5.1f} %",
                    f"{r['ram_mb']:8.1f} MB",
                    r["priority_label"],
                    r["tag"],
                ]
            )
            tones.append("amber" if r["tag"] == "BLOAT" else ("muted" if r["protected"] else ""))
        self.proc_tree.set_rows(table_rows, key_col=0, row_tones=tones)

    def _on_kill_process(self) -> None:
        vals = self.proc_tree.get_selected_values()
        if not vals:
            self._status_queue.put("ERR: Select a process row first")
            return
        pid = int(vals[0])
        self._run_bg(lambda: self.proc_mgr.kill_process(pid), self._refresh_processes)

    def _on_set_priority(self, high: bool) -> None:
        vals = self.proc_tree.get_selected_values()
        if not vals:
            self._status_queue.put("ERR: Select a process row first")
            return
        pid = int(vals[0])
        self._run_bg(lambda: self.proc_mgr.set_priority(pid, high=high), self._refresh_processes)

    # [6] Network Ping & DNS callbacks
    def _refresh_net_interfaces(self) -> None:
        def _work() -> None:
            ifaces = self.net_tools.list_active_interfaces()
            if not self._closing:
                self.after(0, lambda: self._apply_net_interfaces(ifaces))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_net_interfaces(self, ifaces: list[dict[str, Any]]) -> None:
        self._net_ifaces = ifaces
        if not ifaces:
            self.opt_net_iface.configure(values=["No Active Adapter"])
            self.var_net_iface.set("No Active Adapter")
            self.lbl_net_dns.configure(text="DNS: OFFLINE", fg=DANGER_RED)
            return
        names = [i["name"] for i in ifaces]
        self.opt_net_iface.configure(values=names)
        if self.var_net_iface.get() not in names:
            self.var_net_iface.set(names[0])
        self._update_dns_label()

    def _update_dns_label(self) -> None:
        ifaces = getattr(self, "_net_ifaces", [])
        cur = self.var_net_iface.get()
        match = next((i for i in ifaces if i["name"] == cur), None)
        if not match:
            self.lbl_net_dns.configure(text="DNS: --")
            return
        mode = match["dns_mode"]
        col = ACCENT_BLURPLE if "1.1.1.1" in match["dns_servers"] else STATUS_GREEN
        self.lbl_net_dns.configure(text=f"DNS [{mode}]: {match['dns_servers']}", fg=col)

    def _refresh_pings(self) -> None:
        if self._ping_busy or self._closing:
            return
        self._ping_busy = True
        extra = self.ent_ping_host.get().strip()

        def _work() -> None:
            try:
                rows = self.net_tools.ping_all(extra_host=extra)
                if not self._closing:
                    self.after(0, lambda: self._render_pings(rows))
            finally:
                self._ping_busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _render_pings(self, rows: list[dict[str, Any]]) -> None:
        t_rows = []
        tones = []
        for r in rows:
            last_s = f"{r['last_ms']:.1f}" if r["last_ms"] is not None else "TIMEOUT"
            min_s = f"{r['min_ms']:.1f}" if r["min_ms"] is not None else "--"
            avg_s = f"{r['avg_ms']:.1f}" if r["avg_ms"] is not None else "--"
            max_s = f"{r['max_ms']:.1f}" if r["max_ms"] is not None else "--"
            t_rows.append(
                [r["host"], r["label"], last_s, min_s, avg_s, max_s, f"{r['loss_pct']:.1f}%", r["status"]]
            )
            st = r["status"]
            tones.append("green" if st == "LOW" else ("amber" if st == "MODERATE" else "red"))
        self.net_tree.set_rows(t_rows, key_col=0, row_tones=tones)

    def _on_set_dns(self, mode: str) -> None:
        iface = self.var_net_iface.get()
        if mode == "cloudflare":
            fn = lambda: self.net_tools.set_dns_cloudflare(iface)
        elif mode == "google":
            fn = lambda: self.net_tools.set_dns_google(iface)
        else:
            fn = lambda: self.net_tools.set_dns_dhcp(iface)
        self._run_bg(fn, self._refresh_net_interfaces)

    def _on_flush_dns(self) -> None:
        self._run_bg(self.net_tools.flush_dns)

    def _toggle_auto_ping(self) -> None:
        self._auto_ping = not self._auto_ping
        if self._auto_ping:
            self.btn_auto_ping.configure(
                text="AUTO-PING: ON (3s)", fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE, text_color=TEXT_PRIMARY
            )
        else:
            self.btn_auto_ping.configure(
                text="AUTO-PING: OFF", fg_color=BG_HEADER, border_color=BORDER_GRID, text_color=TEXT_SECONDARY
            )

    # [7] Storage Health callbacks
    def _refresh_storage(self) -> None:
        if self._storage_busy or self._closing:
            return
        self._storage_busy = True
        snap = self.mon.get()
        lhm_list = list(snap.storage_sensors)

        def _work() -> None:
            try:
                vols, ios = self.storage_mon.get_volumes_and_health(lhm_sensors=lhm_list)
                if not self._closing:
                    self.after(0, lambda: self._render_storage(vols, ios))
            finally:
                self._storage_busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _render_storage(self, vols: list[dict[str, Any]], ios: list[dict[str, Any]]) -> None:
        drives = [v["drive"] for v in vols if len(v["drive"]) >= 2]
        if drives:
            self.opt_retrim_drive.configure(values=drives)
            if self.var_retrim_drive.get() not in drives:
                self.var_retrim_drive.set(drives[0])

        v_rows, v_tones = [], []
        for v in vols:
            t_s = f"{v['temp_c']:.0f} °C" if v["temp_c"] is not None else "--"
            v_rows.append(
                [
                    v["drive"],
                    v["fstype"],
                    v["model"],
                    v["bus"],
                    f"{v['total_gb']:.1f}",
                    f"{v['used_gb']:.1f}",
                    f"{v['free_gb']:.1f}",
                    f"{v['used_pct']:.1f}%",
                    t_s,
                    v["health"],
                ]
            )
            v_tones.append("red" if v["used_pct"] >= 90.0 else ("amber" if v["used_pct"] >= 78.0 else "green"))
        self.vol_tree.set_rows(v_rows, key_col=0, row_tones=v_tones)

        io_rows = []
        tot_r, tot_w = 0.0, 0.0
        for d in ios:
            tot_r += d["read_mb_s"]
            tot_w += d["write_mb_s"]
            t_s = f"{d['temp_c']:.0f} °C" if d["temp_c"] is not None else "--"
            io_rows.append(
                [
                    d["disk"],
                    d["model"],
                    f"{d['read_mb_s']:.2f}",
                    f"{d['write_mb_s']:.2f}",
                    d["read_count"],
                    d["write_count"],
                    t_s,
                ]
            )
        self.disk_io_tree.set_rows(io_rows, key_col=0)
        self.lbl_disk_io_strip.configure(
            text=f"THROUGHPUT // READ: {tot_r:.2f} MB/s  |  WRITE: {tot_w:.2f} MB/s"
        )

    def _on_retrim_volume(self) -> None:
        drive = self.var_retrim_drive.get()
        self._run_bg(lambda: self.storage_mon.optimize_retrim(drive), self._refresh_storage)

    # [8] Startup Optimizer callbacks
    def _set_startup_filter(self, filt: str) -> None:
        self._startup_filter = filt
        for btn, key in (
            (self.btn_st_all, "ALL"),
            (self.btn_st_apps, "STARTUP APP"),
            (self.btn_st_svcs, "SERVICE"),
        ):
            if key == filt:
                btn.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE)
            else:
                btn.configure(fg_color=BG_HEADER, border_color=BORDER_GRID)
        self._render_startup_table()

    def _refresh_startup(self) -> None:
        def _work() -> None:
            items = self.startup_mgr.list_startup_items()
            if not self._closing:
                self.after(0, lambda: self._apply_startup_items(items))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_startup_items(self, items: list[dict[str, Any]]) -> None:
        self._startup_items = items
        self._render_startup_table()

    def _render_startup_table(self) -> None:
        filtered = [
            it
            for it in self._startup_items
            if self._startup_filter == "ALL" or it["category"] == self._startup_filter
        ]
        self._startup_visible = filtered
        rows, tones, keys = [], [], []
        for it in filtered:
            rows.append(
                [it["name"], it["category"], it["source"], it["impact"], it["status"], it["command"]]
            )
            keys.append(it["id"])
            tones.append("green" if it["enabled"] else "muted")
        self.startup_tree.set_rows(rows, keys=keys, row_tones=tones)
        self.lbl_startup_count.configure(text=f"ITEMS: {len(filtered)}")

    def _on_toggle_startup(self) -> None:
        idx = self.startup_tree.get_selected_index()
        visible = getattr(self, "_startup_visible", [])
        if idx is None or idx >= len(visible):
            self._status_queue.put("ERR: Select a startup entry or service row first")
            return
        item = visible[idx]
        self._run_bg(lambda: self.startup_mgr.toggle_item(item), self._refresh_startup)

    # [9] Display & Refresh Rate callbacks
    def _refresh_displays(self) -> None:
        displays = self.display_mgr.list_displays()
        self._displays_cache = displays
        if not displays:
            self.lbl_disp_summary.configure(text="NO ACTIVE DISPLAY DETECTED")
            return
        primary = next((d for d in displays if d["is_primary"]), displays[0])
        self._selected_display_dev = primary["device_name"]
        self.lbl_disp_summary.configure(
            text=f"{primary['device_name']} | {primary['width']}x{primary['height']} @ {primary['current_hz']} Hz ({primary['bit_depth']}-bit)"
        )

        d_rows, d_tones = [], []
        for d in displays:
            d_rows.append(
                [
                    d["device_name"],
                    d["adapter_name"],
                    f"{d['width']}x{d['height']}",
                    f"{d['bit_depth']}-bit",
                    f"{d['current_hz']} Hz",
                    f"{d['max_hz']} Hz",
                    "PRIMARY" if d["is_primary"] else "EXTENDED",
                ]
            )
            d_tones.append("green" if d["current_hz"] == d["max_hz"] else "amber")
        self.disp_tree.set_rows(d_rows, key_col=0, row_tones=d_tones)

        # Populate quick Hz buttons & mode table for primary display
        for child in self.hz_btn_host.winfo_children():
            child.destroy()
        m_rows, m_tones = [], []
        for hz in primary["refresh_rates"]:
            is_cur = hz == primary["current_hz"]
            SharpButton(
                self.hz_btn_host,
                f"{hz} Hz",
                command=lambda h=hz, dev=primary["device_name"]: self._on_set_hz(dev, h),
                style="blurple" if is_cur else "neutral",
                width=78,
                height=26,
            ).pack(side="left", padx=3, pady=5)
            m_rows.append(
                [
                    primary["device_name"],
                    f"{primary['width']}x{primary['height']}",
                    f"{primary['bit_depth']}-bit",
                    f"{hz} Hz",
                    "ACTIVE MODE" if is_cur else "AVAILABLE",
                ]
            )
            m_tones.append("green" if is_cur else "")
        self.disp_modes_tree.set_rows(m_rows, key_col=3, row_tones=m_tones)

    def _on_set_hz(self, dev: str, hz: int) -> None:
        self._run_bg(lambda: self.display_mgr.set_refresh_rate(dev, hz), self._refresh_displays)

    def _on_display_max_hz(self) -> None:
        self._run_bg(
            lambda: self.display_mgr.set_max_refresh_rate(self._selected_display_dev),
            self._refresh_displays,
        )

    def _on_display_60hz(self) -> None:
        self._run_bg(
            lambda: self.display_mgr.set_battery_60hz(self._selected_display_dev),
            self._refresh_displays,
        )

    def _on_apply_selected_hz_mode(self) -> None:
        vals = self.disp_modes_tree.get_selected_values()
        if not vals:
            return
        dev = vals[0]
        hz = int(vals[3].split()[0])
        self._on_set_hz(dev, hz)

    # [10] Benchmark Stress callbacks
    def _set_stress_mode(self, mode: str) -> None:
        self._stress_mode = mode
        if mode == "CPU_ONLY":
            self.btn_mode_cpu.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE)
            self.btn_mode_cpugpu.configure(fg_color=BG_HEADER, border_color=BORDER_GRID)
        else:
            self.btn_mode_cpu.configure(fg_color=BG_HEADER, border_color=BORDER_GRID)
            self.btn_mode_cpugpu.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE)

    def _on_stress_start(self) -> None:
        self.stress_tree.clear()
        self.kpi_stress_verdict.configure(text="RUNNING...", fg=ACCENT_BLURPLE)
        self.btn_stress_start.configure(state="disabled")
        self.btn_stress_abort.configure(state="normal")
        wd_limit = self.clocks.get_state().watchdog_limit_c

        ok, msg = self.stress_tester.start(
            mode=self._stress_mode,
            monitor_fn=self.mon.get,
            watchdog_limit_c=wd_limit,
            duration_s=30,
            on_sample=lambda row: None if self._closing else self.after(0, lambda r=row: self._on_stress_sample(r)),
            on_done=lambda summary: None if self._closing else self.after(0, lambda s=summary: self._on_stress_done(s)),
        )
        self._status_queue.put(msg if ok else f"ERR: {msg}")
        if not ok:
            self.btn_stress_start.configure(state="normal")
            self.btn_stress_abort.configure(state="disabled")

    def _on_stress_abort(self) -> None:
        _, msg = self.stress_tester.stop()
        self._status_queue.put(msg)

    def _on_stress_sample(self, r: dict[str, Any]) -> None:
        self.kpi_stress_timer.configure(text=f"{r['sec']:02d} / {r['duration_s']:02d} s")
        self.kpi_stress_ops.configure(text=f"{r['kops_s']:.1f} kOps/s")
        ct_s = f"{r['cpu_temp']:.0f}" if r["cpu_temp"] is not None else "--"
        gt_s = f"{r['gpu_temp']:.0f}" if r["gpu_temp"] is not None else "--"
        self.kpi_stress_peak.configure(text=f"{ct_s} / {gt_s} °C")
        cf_s = f"{r['cpu_ghz']:.2f}" if r["cpu_ghz"] else "-.--"
        gc_s = f"{r['gpu_mhz']:.0f}" if r["gpu_mhz"] is not None else "----"
        self.kpi_stress_clks.configure(text=f"{cf_s} GHz / {gc_s} MHz")

        tone = "red" if "ABORT" in r["status"] else ("amber" if r["status"] == "WARM" else "green")
        self.stress_tree.prepend_or_append_row(
            [
                f"{r['sec']:02d}s",
                f"{r['cpu_temp']:.1f}" if r["cpu_temp"] is not None else "--",
                cf_s,
                f"{r['cpu_load']:.1f}%",
                f"{r['gpu_temp']:.1f}" if r["gpu_temp"] is not None else "--",
                gc_s,
                f"{r['gpu_load']:.1f}%" if r["gpu_load"] is not None else "--",
                f"{r['kops_s']:.1f}",
                r["status"],
            ],
            tone=tone,
            prepend=False,
        )

    def _on_stress_done(self, summary: dict[str, Any]) -> None:
        self.btn_stress_start.configure(state="normal")
        self.btn_stress_abort.configure(state="disabled")
        verdict = str(summary.get("verdict", "DONE"))
        col = STATUS_GREEN if verdict.startswith("PASS") else (THERMAL_AMBER if "WARN" in verdict else DANGER_RED)
        self.kpi_stress_verdict.configure(text=verdict, fg=col)
        pc = f"{summary['peak_cpu_c']:.0f}" if summary.get("peak_cpu_c") is not None else "--"
        pg = f"{summary['peak_gpu_c']:.0f}" if summary.get("peak_gpu_c") is not None else "--"
        self.kpi_stress_peak.configure(text=f"{pc} / {pg} °C (PEAK)")
        self._status_queue.put(f"STRESS TEST COMPLETE: {verdict}")

    # [11] Session Logger callbacks
    def _on_toggle_recording(self) -> None:
        if self.session_logger.is_recording:
            ok, msg = self.session_logger.stop()
            self._status_queue.put(msg if ok else f"ERR: {msg}")
            self.btn_log_toggle.configure(
                text="START CSV RECORDING",
                fg_color=GREEN_DIM,
                border_color=STATUS_GREEN,
                text_color=STATUS_GREEN,
            )
            self._refresh_saved_logs()
        else:
            self.log_live_tree.clear()
            ok, msg = self.session_logger.start()
            self._status_queue.put(msg if ok else f"ERR: {msg}")
            if ok:
                self.btn_log_toggle.configure(
                    text="STOP RECORDING",
                    fg_color=RED_DIM,
                    border_color=DANGER_RED,
                    text_color=DANGER_RED,
                )
        self._sync_logger_kpi()

    def _on_choose_log_path(self) -> None:
        if self.session_logger.is_recording:
            self._status_queue.put("ERR: Stop active recording before choosing a new path")
            return
        self.session_logger.log_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Select BANKO Telemetry CSV Log File",
            initialdir=str(self.session_logger.log_dir),
            initialfile=f"BANKO_session_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            defaultextension=".csv",
            filetypes=[("CSV Telemetry Report", "*.csv")],
        )
        if not path:
            return
        self.log_live_tree.clear()
        ok, msg = self.session_logger.start(custom_path=Path(path))
        self._status_queue.put(msg if ok else f"ERR: {msg}")
        if ok:
            self.btn_log_toggle.configure(
                text="STOP RECORDING",
                fg_color=RED_DIM,
                border_color=DANGER_RED,
                text_color=DANGER_RED,
            )
        self._sync_logger_kpi()

    def _on_open_logs_dir(self) -> None:
        self.session_logger.log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(self.session_logger.log_dir))  # type: ignore[attr-defined]
        except Exception as exc:
            self._status_queue.put(f"ERR: Cannot open folder: {exc}")

    def _refresh_saved_logs(self) -> None:
        files = self.session_logger.list_saved_logs()
        rows = [[f["name"], f"{f['size_kb']:.1f}", f["modified"], f["path"]] for f in files]
        self.log_files_tree.set_rows(rows, key_col=0)

    def _sync_logger_kpi(self) -> None:
        st = self.session_logger.get_status()
        if st["recording"]:
            self.kpi_log_state.configure(text="● RECORDING", fg=DANGER_RED)
            self.lbl_dock_rec.configure(text=f"● REC CSV ({st['samples']})", fg=DANGER_RED)
        else:
            self.kpi_log_state.configure(text="STANDBY", fg=TEXT_MUTED)
            self.lbl_dock_rec.configure(text="CSV REC: STANDBY", fg=TEXT_MUTED)
        self.kpi_log_samples.configure(text=str(st["samples"]))
        self.kpi_log_elapsed.configure(text=f"{st['elapsed_s']:.1f} s")
        self.kpi_log_size.configure(text=f"{st['size_kb']:.1f} KB")
        self.kpi_log_path.configure(text=st["path"] or st["log_dir"])

    # ------------------------------------------------------------------ Overlays (Discord Dark)
    def _toggle_mini(self) -> None:
        self.mini_on = not self.mini_on
        if self.mini_on:
            self._open_mini()
            self.btn_mini.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE, text_color=TEXT_PRIMARY)
        else:
            if self.mini_win:
                self.mini_win.destroy()
                self.mini_win = None
            self.btn_mini.configure(fg_color=BG_SIDEBAR, border_color=BORDER_GRID, text_color=TEXT_SECONDARY)

    def _open_mini(self) -> None:
        if self.mini_win:
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.94)
        win.configure(bg=BG_RAIL, highlightthickness=1, highlightbackground=ACCENT_BLURPLE)
        win.geometry("540x40+24+24")

        lbl = tk.Label(
            win,
            text="BANKO v3 // GPU --°C ----/---- MHz | CPU --°C -.--GHz | RAM --%",
            font=FONT_XS_BOLD,
            fg=STATUS_GREEN,
            bg=BG_RAIL,
            padx=10,
            pady=8,
        )
        lbl.pack(fill="both", expand=True)
        win._lbl = lbl  # type: ignore[attr-defined]

        drag: dict[str, int] = {}

        def press(e: tk.Event[Any]) -> None:
            drag["x"] = e.x_root - win.winfo_x()
            drag["y"] = e.y_root - win.winfo_y()

        def move(e: tk.Event[Any]) -> None:
            if "x" in drag:
                win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        lbl.bind("<ButtonPress-1>", press)
        lbl.bind("<B1-Motion>", move)
        lbl.bind("<Double-Button-1>", lambda _e: self._toggle_mini())
        self.mini_win = win

    def _toggle_fps(self) -> None:
        self.fps_on = not self.fps_on
        self.mon.sample_fps = self.fps_on
        if self.fps_on:
            self._open_fps()
            self.btn_fps.configure(fg_color=ACCENT_BLURPLE, border_color=ACCENT_BLURPLE, text_color=TEXT_PRIMARY)
        else:
            if self.fps_win:
                self.fps_win.destroy()
                self.fps_win = None
            self.btn_fps.configure(fg_color=BG_SIDEBAR, border_color=BORDER_GRID, text_color=TEXT_SECONDARY)

    def _open_fps(self) -> None:
        if self.fps_win:
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG_RAIL, highlightthickness=1, highlightbackground=ACCENT_BLURPLE)
        win.geometry("124x34+18+76")
        lbl = tk.Label(win, text="FPS --", font=("Consolas", 13, "bold"), fg=STATUS_GREEN, bg=BG_RAIL)
        lbl.pack(fill="both", expand=True)
        win._lbl = lbl  # type: ignore[attr-defined]
        win.update_idletasks()
        try:
            u = ctypes.windll.user32
            hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass
        self.fps_win = win

    # ------------------------------------------------------------------ Live 1s Telemetry Tick
    def _track_mm(self, key: str, val: float | None, fmt: str, unit: str) -> tuple[str, str]:
        if val is None:
            return "--", "--"
        cur_min, cur_max = self._minmax.get(key, (None, None))
        new_min = val if cur_min is None else min(cur_min, val)
        new_max = val if cur_max is None else max(cur_max, val)
        self._minmax[key] = (new_min, new_max)
        return f"{format(new_min, fmt)} {unit}", f"{format(new_max, fmt)} {unit}"

    def _fmt_console(self, msg: str) -> str:
        return f"[{time.strftime('%H:%M:%S')}] {msg}"

    def _ui_tick(self) -> None:
        if self._closing:
            return
        self._tick_count += 1

        while not self._status_queue.empty():
            try:
                msg = self._status_queue.get_nowait()
                col = DANGER_RED if msg.startswith("ERR:") else TEXT_SECONDARY
                self.lbl_console.configure(text=self._fmt_console(msg), fg=col)
            except queue.Empty:
                break

        snap: Snapshot = self.mon.get()

        # Heartbeat indicator
        self._heartbeat = not self._heartbeat
        self.hb_canvas.itemconfig(self._hb_rect, fill=STATUS_GREEN if self._heartbeat else BG_HEADER)

        # Watchdog check
        tripped, w_msg = self.clocks.check_thermal_watchdog(snap.gpu_temp)
        if tripped and w_msg:
            self._status_queue.put(w_msg)
            self._sync_clock_ui()
        wd_limit = self.clocks.get_state().watchdog_limit_c

        # Top Channel Header Ticker
        gt_s = f"{snap.gpu_temp:.0f}°C" if snap.gpu_temp is not None else "--°C"
        gc_mem_s = _fmt_gpu_clocks(snap.gpu_clock_mhz, snap.gpu_mem_clock_mhz)
        ct_s = f"{snap.cpu_temp:.0f}°C" if snap.cpu_temp is not None else "--°C"
        cf_s = f"{snap.cpu_freq_ghz:.2f}GHz" if snap.cpu_freq_ghz > 0 else "-.--GHz"
        bat_s = f"{snap.battery_percent:.0f}%" if snap.battery_percent is not None else "AC"
        self.lbl_header_ticker.configure(
            text=f"GPU {gt_s} {gc_mem_s} | CPU {ct_s} {cf_s} | RAM {snap.ram_percent:.1f}% | BAT {bat_s}"
        )

        # [1] # hw-telemetry
        if self._active_channel == "hw-telemetry":
            self.kpi_gpu_clk.configure(text=gc_mem_s)
            g_load_s = f"{snap.gpu_load:.0f} %" if snap.gpu_load is not None else "-- %"
            self.kpi_gpu_temp.configure(
                text=f"{gt_s} | {g_load_s}",
                fg=_temp_color(snap.gpu_temp, wd_limit),
            )
            self.kpi_cpu_stat.configure(
                text=f"{ct_s} | {cf_s}",
                fg=_temp_color(snap.cpu_temp, 90.0),
            )
            if snap.gpu_vram_used_mb is not None and snap.gpu_vram_total_mb is not None:
                vram_txt = f"{snap.gpu_vram_used_mb:.0f} / {snap.gpu_vram_total_mb:.0f} MB"
            elif snap.gpu_vram_total_mb is not None:
                vram_txt = f"-- / {snap.gpu_vram_total_mb:.0f} MB"
            else:
                vram_txt = "SHARED RAM"
            self.kpi_vram.configure(text=vram_txt)
            pwr_s = f"{snap.est_system_power_w:.1f} W" if snap.est_system_power_w is not None else "-- W"
            self.kpi_ram_pwr.configure(text=f"{snap.ram_percent:.1f}% | {pwr_s}")

            # Left HWiNFO64 Table
            mn, mx = self._track_mm("cpu_temp", snap.cpu_temp, ".1f", "°C")
            ct_col = _temp_color(snap.cpu_temp, 90.0)
            self.tbl_compute.update_row(
                "cpu_temp",
                ["CPU Package Temp", f"{snap.cpu_temp:.1f} °C" if snap.cpu_temp is not None else "-- °C", mn, mx, "HOT" if (snap.cpu_temp or 0) >= 85 else "OK"],
                [None, ct_col, TEXT_MUTED, TEXT_MUTED, ct_col],
            )
            mn, mx = self._track_mm("cpu_clk", snap.cpu_freq_ghz if snap.cpu_freq_ghz > 0 else None, ".2f", "GHz")
            self.tbl_compute.update_row(
                "cpu_clk",
                ["CPU Effective Clock", f"{snap.cpu_freq_ghz:.2f} GHz" if snap.cpu_freq_ghz > 0 else "-.-- GHz", mn, mx, "TURBO" if snap.cpu_freq_ghz >= 2.8 else "BASE"],
                [None, TEXT_PRIMARY, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )
            mn, mx = self._track_mm("cpu_load", snap.cpu_load, ".1f", "%")
            self.tbl_compute.update_row(
                "cpu_load",
                ["CPU All-Core Load", f"{snap.cpu_load:.1f} %", mn, mx, "HIGH" if snap.cpu_load >= 80 else "OK"],
                [None, STATUS_GREEN if snap.cpu_load < 80 else THERMAL_AMBER, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )
            mn, mx = self._track_mm("cpu_peak", snap.cpu_peak_core, ".1f", "%")
            self.tbl_compute.update_row(
                "cpu_peak",
                ["CPU Peak Logical Core", f"{snap.cpu_peak_core:.1f} %", mn, mx, "OK"],
                [None, THERMAL_AMBER if snap.cpu_peak_core >= 85 else TEXT_PRIMARY, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )
            mn, mx = self._track_mm("gpu_temp", snap.gpu_temp, ".1f", "°C")
            gt_col = _temp_color(snap.gpu_temp, wd_limit)
            self.tbl_compute.update_row(
                "gpu_temp",
                ["GPU Core Temperature", f"{snap.gpu_temp:.1f} °C" if snap.gpu_temp is not None else "-- °C", mn, mx, "LIMIT" if (snap.gpu_temp or 0) >= wd_limit else "OK"],
                [None, gt_col, TEXT_MUTED, TEXT_MUTED, gt_col],
            )
            mn, mx = self._track_mm("gpu_clk", snap.gpu_clock_mhz, ".0f", "MHz")
            self.tbl_compute.update_row(
                "gpu_clk",
                ["GPU Core / Mem Clock", gc_mem_s, mn, mx, snap.gpu_pstate or "ACTIVE"],
                [None, ACCENT_BLURPLE, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )
            mn, mx = self._track_mm("gpu_load", snap.gpu_load, ".1f", "%")
            self.tbl_compute.update_row(
                "gpu_load",
                ["GPU 3D Compute Load", f"{snap.gpu_load:.1f} %" if snap.gpu_load is not None else "-- %", mn, mx, "OK"],
                [None, STATUS_GREEN, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )
            mn, mx = self._track_mm("gpu_vram", snap.gpu_vram_used_mb, ".0f", "MB")
            vp_s = f"{snap.gpu_vram_pct:.1f}%" if snap.gpu_vram_pct is not None else "--%"
            self.tbl_compute.update_row(
                "gpu_vram",
                ["GPU VRAM Used / Total", vram_txt, mn, mx, vp_s],
                [None, TEXT_PRIMARY, TEXT_MUTED, TEXT_MUTED, ACCENT_BLURPLE],
            )
            gp_w = f"{snap.gpu_power_w:.1f} W" if snap.gpu_power_w is not None else "N/A W"
            self.tbl_compute.update_row(
                "gpu_pstate",
                ["GPU P-State / Power", f"{snap.gpu_pstate or '--'} / {gp_w}", "--", "--", "NVSMI"],
                [None, TEXT_PRIMARY, TEXT_MUTED, TEXT_MUTED, STATUS_GREEN],
            )

            # Right HWiNFO64 Table
            self.tbl_sys_pwr.update_row(
                "ram_total",
                ["Installed / Usable RAM", f"{snap.ram_installed_gb:.2f} GB", f"{snap.ram_total_gb:.2f} GB USABLE", f"{snap.ram_percent:.1f}%"],
                [None, TEXT_PRIMARY, TEXT_SECONDARY, THERMAL_AMBER if snap.ram_percent >= 85 else STATUS_GREEN],
            )
            self.tbl_sys_pwr.update_row(
                "ram_os",
                ["Windows OS Footprint", f"{snap.ram_os_kernel_gb:.2f} GB", f"Cache {snap.ram_sys_cache_gb:.2f} GB", "KERNEL"],
                [None, THERMAL_AMBER, TEXT_SECONDARY, TEXT_MUTED],
            )
            self.tbl_sys_pwr.update_row(
                "ram_apps",
                ["Apps & User Memory", f"{snap.ram_apps_gb:.2f} GB", f"Used {snap.ram_used_gb:.2f} GB", "ACTIVE"],
                [None, ACCENT_BLURPLE, TEXT_SECONDARY, STATUS_GREEN],
            )
            self.tbl_sys_pwr.update_row(
                "ram_free",
                ["Available Free RAM", f"{snap.ram_free_gb:.2f} GB", "Ready / Reclaimable", "FREE"],
                [None, STATUS_GREEN, TEXT_SECONDARY, STATUS_GREEN],
            )
            bat_pct_s = f"{snap.battery_percent:.0f} %" if snap.battery_percent is not None else "AC ONLY"
            eta_s = (
                f"{snap.battery_time_left_min // 60}h {snap.battery_time_left_min % 60:02d}m left"
                if snap.battery_time_left_min is not None
                else snap.power_status
            )
            bat_col = STATUS_GREEN if snap.power_status != "DISCHARGING" else THERMAL_AMBER
            self.tbl_sys_pwr.update_row(
                "bat_state",
                ["AC Charger / Battery", bat_pct_s, eta_s, snap.power_status[:12]],
                [None, bat_col, TEXT_SECONDARY, bat_col],
            )
            self.tbl_sys_pwr.update_row(
                "pwr_plan",
                ["Active Power Plan", snap.power_plan_name[:18], "Windows PowerCfg", "ACTIVE"],
                [None, ACCENT_BLURPLE, TEXT_MUTED, STATUS_GREEN],
            )
            rate_src = "FIRMWARE RATE" if snap.power_rate_w is not None else "THERMAL EST."
            self.tbl_sys_pwr.update_row(
                "pwr_est",
                ["System Wattage Est.", pwr_s, rate_src, "OK"],
                [None, TEXT_PRIMARY, TEXT_MUTED, STATUS_GREEN],
            )
            fans = snap.fan_state.fans
            for idx, rk in enumerate(("fan_01", "fan_02")):
                if idx < len(fans):
                    f = fans[idx]
                    fcol = THERMAL_AMBER if f.is_estimated else STATUS_GREEN
                    self.tbl_sys_pwr.update_row(
                        rk,
                        [f"{f.label} ({'CPU' if idx == 0 else 'GPU'})", f"{f.rpm:.0f} RPM", f"{f.duty_pct:.1f} % duty", "EST." if f.is_estimated else "LIVE"],
                        [None, fcol, TEXT_SECONDARY, fcol],
                    )

        # [2] # fan-cooling
        if self._active_channel == "fan-cooling":
            fs: FanArrayState = snap.fan_state
            is_est = fs.mode == "HYBRID_ESTIMATED"
            self.lbl_fan_backend.configure(
                text=f"BACKEND: {fs.backend} | DLL LOADED: {fs.dll_loaded} | CHANNELS: {fs.fan_count}"
            )
            self.lbl_fan_banner.configure(
                text=fs.mode_banner, fg=THERMAL_AMBER if is_est else STATUS_GREEN
            )
            f_rows = [
                [
                    ch.label,
                    ch.name,
                    ch.node_type,
                    f"{ch.rpm:.0f} RPM",
                    f"{ch.duty_pct:.1f} %",
                    ch.status_tag,
                ]
                for ch in fs.fans
            ]
            f_tones = ["amber" if ch.is_estimated else "green" for ch in fs.fans]
            self.fan_tree.set_rows(f_rows, key_col=0, row_tones=f_tones)

        # [3] # clock-overclock live readouts
        if self._active_channel == "clock-overclock":
            self.lbl_oc_gpu_live.configure(text=gc_mem_s)
            self.lbl_oc_cpu_live.configure(text=cf_s)

        # [5] # process-killer periodic refresh (every 3s)
        if self._active_channel == "process-killer" and self._tick_count % 3 == 0:
            self._refresh_processes()

        # [6] # network-ping periodic refresh (every 3s when auto-ping ON)
        if self._active_channel == "network-ping" and self._auto_ping and self._tick_count % 3 == 0:
            self._refresh_pings()

        # [7] # storage-health periodic refresh (every 2s)
        if self._active_channel == "storage-health" and self._tick_count % 2 == 0:
            self._refresh_storage()

        # [11] # session-logger live recording
        if self.session_logger.is_recording:
            rec = self.session_logger.record_snapshot(snap)
            self._sync_logger_kpi()
            if rec and self._active_channel == "session-logger":
                self.log_live_tree.prepend_or_append_row(
                    [
                        rec["time"],
                        f"{rec['elapsed_s']:.1f}s",
                        f"{rec['cpu_c']:.1f}" if rec["cpu_c"] is not None else "--",
                        f"{rec['cpu_ghz']:.2f}" if rec["cpu_ghz"] else "--",
                        f"{rec['gpu_c']:.1f}" if rec["gpu_c"] is not None else "--",
                        f"{rec['gpu_mhz']:.0f}" if rec["gpu_mhz"] is not None else "--",
                        f"{rec['vram_mb']:.0f}" if rec["vram_mb"] is not None else "--",
                        f"{rec['ram_pct']:.1f}%",
                        f"{rec['fan01_rpm']:.0f}" if rec["fan01_rpm"] is not None else "--",
                    ],
                    tone="green",
                    prepend=True,
                )

        # Overlays
        if self.mini_win is not None:
            self.mini_win._lbl.configure(  # type: ignore[attr-defined]
                text=f"BANKO v3 // GPU {gt_s} {gc_mem_s} | CPU {ct_s} {cf_s} | RAM {snap.ram_percent:.0f}%"
            )
        if self.fps_win is not None:
            fps_txt = "FPS --" if snap.fps is None else f"FPS {snap.fps:3.0f}"
            self.fps_win._lbl.configure(text=fps_txt)  # type: ignore[attr-defined]

        self.after(1000, self._ui_tick)

    def _finish_smoke_test(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        print(f"SMOKE_WINDOW={w}x{h}", flush=True)
        print("SMOKE_TEST_OK", flush=True)
        self._on_close()

    def _on_close(self) -> None:
        self._closing = True
        try:
            self.stress_tester.stop()
            if self.session_logger.is_recording:
                self.session_logger.stop()
            self.mon.stop()
        finally:
            self.destroy()


def main() -> None:
    smoke = "--smoke-test" in sys.argv
    app = BankoWorkspaceApp(smoke_test=smoke)
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
