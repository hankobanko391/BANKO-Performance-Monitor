"""BANKO Performance Monitor v2.2.0 — Tactical Multi-Page Industrial Command UI."""

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
from tkinter import font as tkfont
from typing import Any

import customtkinter as ctk
from PIL import ImageTk

from fan_engine import FanArrayState, FanChannel
from generate_icon import make_emblem
from monitor import Monitor, Snapshot
from tuner_engine import (
    CPU_FREQ_MAX_GHZ,
    CPU_FREQ_MIN_GHZ,
    GPU_CLOCK_MAX_MHZ,
    GPU_CLOCK_MIN_MHZ,
    THERMAL_WATCHDOG_LIMIT_C,
    ClockController,
    SystemBoosters,
    activate_ac_turbo_power_mode,
    ghz_to_mhz,
    is_admin,
    restore_balanced_power_mode,
    snap_cpu_freq_ghz,
    snap_gpu_clock_mhz,
)

APP_VERSION = "2.2.0"

# --- Tactical Industrial Palette (Zero Neon Glows / Zero Gradients / Sharp 1px) ---
BG_BASE = "#0A0C0F"
BG_PANEL = "#12161C"
BG_HEADER = "#0D1015"
BG_RECESSED = "#0B0E12"
BORDER_COLOR = "#222933"
DIVIDER_COLOR = "#1A2029"
SLATE_SURFACE = "#181E27"

TEXT_PRIMARY = "#E6EDF3"
TEXT_SECONDARY = "#9CA3AF"
TEXT_MUTED = "#6B7280"

ACCENT_GREEN = "#44D62C"
ACCENT_GREEN_DIM = "#1C3B1A"
ACCENT_AMBER = "#FF9E00"
ACCENT_AMBER_DIM = "#38250D"
ACCENT_CYAN = "#00D8FF"
STATUS_CRIT = "#EF4444"

FONT_TITLE = ("Consolas", 14, "bold")
FONT_HEADER = ("Consolas", 11, "bold")
FONT_HERO_NUM = ("Consolas", 22, "bold")
FONT_VAL_LG = ("Consolas", 16, "bold")
FONT_MONO_BOLD = ("Consolas", 11, "bold")
FONT_MONO = ("Consolas", 11)
FONT_MONO_SM = ("Consolas", 10)
FONT_MONO_SM_BOLD = ("Consolas", 10, "bold")
FONT_MONO_XS = ("Consolas", 9)
FONT_MONO_XS_BOLD = ("Consolas", 9, "bold")
FONT_UI_SM = ("Segoe UI", 10)


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def _temp_color(temp_c: float | None) -> str:
    if temp_c is None:
        return TEXT_MUTED
    if temp_c >= THERMAL_WATCHDOG_LIMIT_C:
        return STATUS_CRIT
    if temp_c >= 75.0:
        return ACCENT_AMBER
    return ACCENT_GREEN


class SegmentedProgressBar(tk.Canvas):
    """Flat 20-segment industrial progress meter with crisp 1px border."""

    SEGMENTS = 20

    def __init__(
        self,
        master: Any,
        height: int = 12,
        fill_color: str = ACCENT_GREEN,
        warn_threshold: float = 0.78,
        crit_threshold: float = 0.92,
    ) -> None:
        super().__init__(
            master,
            height=height,
            bg=BG_RECESSED,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            bd=0,
        )
        self._ratio = 0.0
        self._base_color = fill_color
        self._warn_t = warn_threshold
        self._crit_t = crit_threshold
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_ratio(self, ratio: float, color_override: str | None = None) -> None:
        clamped = max(0.0, min(1.0, float(ratio)))
        if color_override is not None:
            self._base_color = color_override
        if abs(clamped - self._ratio) >= 0.005 or clamped in (0.0, 1.0):
            self._ratio = clamped
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(80, self.winfo_width())
        h = max(10, self.winfo_height())

        pad_x = 3.0
        pad_y = 2.0
        gap = 2.0
        usable_w = max(20.0, w - 2.0 * pad_x - (self.SEGMENTS - 1) * gap)
        seg_w = usable_w / self.SEGMENTS
        y0 = pad_y
        y1 = max(pad_y + 2.0, h - pad_y)

        lit = int(round(self._ratio * self.SEGMENTS))
        if self._ratio >= self._crit_t:
            active_c = STATUS_CRIT
        elif self._ratio >= self._warn_t:
            active_c = ACCENT_AMBER
        else:
            active_c = self._base_color

        for idx in range(self.SEGMENTS):
            x0 = pad_x + idx * (seg_w + gap)
            x1 = x0 + seg_w
            self.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                fill=active_c if idx < lit else DIVIDER_COLOR,
                outline="",
            )


class FanRowWidget:
    """Single hybrid fan channel row (FAN 01, name, LIVE/ESTIMATED tag, RPM, %, segmented meter)."""

    def __init__(self, parent: Any, channel: FanChannel) -> None:
        self.frame = ctk.CTkFrame(
            parent,
            fg_color=BG_RECESSED,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        self.frame.pack(fill="x", padx=2, pady=(0, 6))
        self.frame.grid_columnconfigure(1, weight=1)

        top = ctk.CTkFrame(self.frame, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 4))
        top.grid_columnconfigure(1, weight=1)

        self.lbl_code = ctk.CTkLabel(
            top,
            text=channel.label,
            font=FONT_MONO_SM_BOLD,
            text_color=ACCENT_AMBER if channel.is_estimated else ACCENT_GREEN,
            fg_color=BG_PANEL,
            corner_radius=2,
            width=64,
        )
        self.lbl_code.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.lbl_name = ctk.CTkLabel(
            top,
            text=channel.name,
            font=FONT_MONO_SM_BOLD,
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.lbl_name.grid(row=0, column=1, sticky="ew")

        self.lbl_tag = ctk.CTkLabel(
            top,
            text=channel.status_tag,
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_AMBER if channel.is_estimated else ACCENT_GREEN,
            anchor="e",
            width=195,
        )
        self.lbl_tag.grid(row=0, column=2, padx=(8, 10), sticky="e")

        self.lbl_rpm = ctk.CTkLabel(
            top,
            text="    0 RPM",
            font=FONT_MONO_BOLD,
            text_color=ACCENT_CYAN if not channel.is_estimated else ACCENT_AMBER,
            anchor="e",
            width=84,
        )
        self.lbl_rpm.grid(row=0, column=3, padx=(0, 8), sticky="e")

        self.lbl_duty = ctk.CTkLabel(
            top,
            text="  0.0 %",
            font=FONT_MONO_SM_BOLD,
            text_color=TEXT_PRIMARY,
            anchor="e",
            width=62,
        )
        self.lbl_duty.grid(row=0, column=4, sticky="e")

        bar_color = ACCENT_AMBER if channel.is_estimated else ACCENT_GREEN
        self.meter = SegmentedProgressBar(self.frame, height=11, fill_color=bar_color)
        self.meter.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 7))
        self.update(channel)

    def update(self, channel: FanChannel) -> None:
        tag_color = ACCENT_AMBER if channel.is_estimated else ACCENT_GREEN
        self.lbl_code.configure(text=channel.label, text_color=tag_color)
        self.lbl_name.configure(text=channel.name)
        self.lbl_tag.configure(text=channel.status_tag, text_color=tag_color)
        self.lbl_rpm.configure(
            text=f"{channel.rpm:5.0f} RPM",
            text_color=ACCENT_AMBER if channel.is_estimated else ACCENT_CYAN,
        )
        self.lbl_duty.configure(text=f"{channel.duty_pct:5.1f} %")
        self.meter.set_ratio(
            channel.duty_pct / 100.0,
            color_override=ACCENT_AMBER if channel.is_estimated else ACCENT_GREEN,
        )


class BankoTacticalApp(ctk.CTk):
    """BANKO v2.2.0 Multi-Page Industrial Gaming Command Center."""

    def __init__(self, smoke_test: bool = False) -> None:
        super().__init__()
        self._smoke_test = smoke_test
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._photo_refs: list[Any] = []
        self._active_page = "01"
        self._nav_buttons: dict[str, tuple[ctk.CTkFrame, ctk.CTkFrame, ctk.CTkButton]] = {}
        self._pages: dict[str, ctk.CTkFrame] = {}

        self.mini_on = False
        self.fps_on = False
        self.mini_win: tk.Toplevel | None = None
        self.fps_win: tk.Toplevel | None = None
        self._fan_rows: list[FanRowWidget] = []
        self._fan_signature: tuple[str, ...] = ()
        self._heartbeat = False

        ctk.set_appearance_mode("dark")
        self.title(f"BANKO v{APP_VERSION} // TACTICAL EDITION")
        self.geometry("1240x780")
        self.minsize(1180, 740)
        self.resizable(True, True)
        self.configure(fg_color=BG_BASE)

        ico_path = resource_path("banko_icon.ico")
        if ico_path.exists():
            try:
                self.iconbitmap(str(ico_path))
            except Exception:
                pass

        self.mon = Monitor(interval=1.0)
        self.clocks = ClockController()
        self.boosters = SystemBoosters()

        self._gpu_slider_mhz = 1355
        self._cpu_slider_ghz = 3.20

        self._build_shell()
        self._build_page_telemetry_power()
        self._build_page_clocks_fans()
        self._build_page_system_boosters()

        self._switch_page("01")
        self._sync_clock_ui()
        self._sync_booster_ui()

        self.mon.start()
        self._ui_tick()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._smoke_test:
            self.after(600, lambda: self._switch_page("02"))
            self.after(1100, lambda: self._switch_page("03"))
            self.after(1600, self._finish_smoke_test)

    # ------------------------------------------------------------------ Shell
    def _build_shell(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # === TOP HEADER BAR ===
        header = ctk.CTkFrame(self, fg_color=BG_HEADER, corner_radius=0, height=58)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        emblem_img = ImageTk.PhotoImage(make_emblem(32, with_tile=False))
        self._photo_refs.append(emblem_img)
        emblem_lbl = tk.Label(header, image=emblem_img, bg=BG_HEADER, bd=0)
        emblem_lbl.grid(row=0, column=0, padx=(14, 10), pady=12)

        brand_col = ctk.CTkFrame(header, fg_color="transparent")
        brand_col.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            brand_col,
            text="BANKO // PERFORMANCE COMMAND",
            font=FONT_TITLE,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")
        self.lbl_sys_hw = ctk.CTkLabel(
            brand_col,
            text=f"SYS: {self.mon.system_name.upper()}",
            font=FONT_MONO_XS,
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.lbl_sys_hw.pack(anchor="w")

        strip_box = ctk.CTkFrame(
            header,
            fg_color=BG_RECESSED,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        strip_box.grid(row=0, column=2, padx=(8, 10), pady=12, sticky="e")
        self.lbl_header_strip = ctk.CTkLabel(
            strip_box,
            text="GPU: --°C ----MHz | CPU: --°C -.--GHz | RAM: --.-% | BAT: ---%",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_GREEN,
            padx=10,
            pady=4,
        )
        self.lbl_header_strip.pack()

        badges = ctk.CTkFrame(header, fg_color="transparent")
        badges.grid(row=0, column=3, padx=(0, 14), sticky="e")

        ver_frame = ctk.CTkFrame(
            badges,
            fg_color=BG_RECESSED,
            border_color=ACCENT_GREEN,
            border_width=1,
            corner_radius=2,
        )
        ver_frame.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            ver_frame,
            text=f"BANKO v{APP_VERSION} // TACTICAL EDITION",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_GREEN,
            padx=8,
            pady=3,
        ).pack()

        uac_ok = is_admin()
        uac_txt = "UAC ADMIN: VERIFIED" if uac_ok else "UAC ADMIN: STANDARD"
        uac_col = ACCENT_GREEN if uac_ok else ACCENT_AMBER
        uac_frame = ctk.CTkFrame(
            badges,
            fg_color=BG_RECESSED,
            border_color=uac_col,
            border_width=1,
            corner_radius=2,
        )
        uac_frame.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            uac_frame,
            text=uac_txt,
            font=FONT_MONO_XS_BOLD,
            text_color=uac_col,
            padx=8,
            pady=3,
        ).pack()

        self.hb_canvas = tk.Canvas(badges, width=10, height=10, bg=BG_HEADER, highlightthickness=0, bd=0)
        self.hb_canvas.pack(side="left", padx=(2, 0))
        self._hb_rect = self.hb_canvas.create_rectangle(1, 1, 9, 9, fill=ACCENT_GREEN, outline="")

        tk.Frame(self, bg=BORDER_COLOR, height=1).place(in_=header, relx=0, rely=1.0, anchor="sw", relwidth=1.0)

        # === LEFT NAVIGATION SIDEBAR ===
        sidebar = ctk.CTkFrame(self, fg_color=BG_HEADER, corner_radius=0, width=236)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(4, weight=1)

        tk.Frame(sidebar, bg=BORDER_COLOR, width=1).place(relx=1.0, rely=0, anchor="ne", relheight=1.0)

        ctk.CTkLabel(
            sidebar,
            text="COMMAND MODULES",
            font=FONT_MONO_XS_BOLD,
            text_color=TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))

        nav_specs = [
            ("01", "[01] TELEMETRY & POWER"),
            ("02", "[02] CLOCKS & FANS"),
            ("03", "[03] SYSTEM BOOSTERS"),
        ]
        for idx, (pid, label) in enumerate(nav_specs, start=1):
            row_wrap = ctk.CTkFrame(
                sidebar,
                fg_color=BG_PANEL,
                border_color=BORDER_COLOR,
                border_width=1,
                corner_radius=2,
            )
            row_wrap.grid(row=idx, column=0, sticky="ew", padx=10, pady=(0, 6))
            row_wrap.grid_columnconfigure(1, weight=1)

            bar = ctk.CTkFrame(row_wrap, fg_color=BORDER_COLOR, corner_radius=0, width=4, height=38)
            bar.grid(row=0, column=0, sticky="ns")

            btn = ctk.CTkButton(
                row_wrap,
                text=label,
                height=38,
                corner_radius=0,
                fg_color="transparent",
                hover_color=SLATE_SURFACE,
                text_color=TEXT_SECONDARY,
                font=FONT_MONO_SM_BOLD,
                anchor="w",
                command=lambda p=pid: self._switch_page(p),
            )
            btn.grid(row=0, column=1, sticky="ew")
            self._nav_buttons[pid] = (row_wrap, bar, btn)

        # Lower Sidebar Utility Strip
        util_box = ctk.CTkFrame(
            sidebar,
            fg_color=BG_RECESSED,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        util_box.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        util_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            util_box,
            text="OVERLAY & SAFETY",
            font=FONT_MONO_XS_BOLD,
            text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 6))

        self.btn_mini = ctk.CTkButton(
            util_box,
            text="MINI OVERLAY : OFF",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=BG_PANEL,
            hover_color=SLATE_SURFACE,
            text_color=TEXT_SECONDARY,
            font=FONT_MONO_XS_BOLD,
            command=self._toggle_mini,
        )
        self.btn_mini.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_fps = ctk.CTkButton(
            util_box,
            text="DWM FPS OVERLAY : OFF",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=BG_PANEL,
            hover_color=SLATE_SURFACE,
            text_color=TEXT_SECONDARY,
            font=FONT_MONO_XS_BOLD,
            command=self._toggle_fps,
        )
        self.btn_fps.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_sidebar_watchdog = ctk.CTkLabel(
            util_box,
            text="WATCHDOG: 86°C ARMED",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_GREEN,
            fg_color=BG_PANEL,
            corner_radius=2,
            pady=5,
        )
        self.lbl_sidebar_watchdog.pack(fill="x", padx=10, pady=(0, 10))

        # === MAIN PAGE STAGE ===
        self.stage = ctk.CTkFrame(self, fg_color=BG_BASE, corner_radius=0)
        self.stage.grid(row=1, column=1, sticky="nsew", padx=12, pady=10)
        self.stage.grid_rowconfigure(0, weight=1)
        self.stage.grid_columnconfigure(0, weight=1)

        # === FOOTER DIAGNOSTIC CONSOLE ===
        footer = ctk.CTkFrame(self, fg_color=BG_HEADER, corner_radius=0, height=26)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.grid_propagate(False)
        tk.Frame(footer, bg=BORDER_COLOR, height=1).pack(fill="x", side="top")
        self.lbl_console = ctk.CTkLabel(
            footer,
            text=self._fmt_console("BANKO v2.2.0 tactical command center initialized"),
            font=FONT_MONO_XS,
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.lbl_console.pack(fill="x", padx=12, pady=2)

    def _switch_page(self, page_id: str) -> None:
        self._active_page = page_id
        for pid, frame in self._pages.items():
            if pid == page_id:
                frame.grid()
            else:
                frame.grid_remove()

        for pid, (wrap, bar, btn) in self._nav_buttons.items():
            if pid == page_id:
                wrap.configure(fg_color=SLATE_SURFACE, border_color=ACCENT_GREEN)
                bar.configure(fg_color=ACCENT_GREEN)
                btn.configure(text_color=ACCENT_GREEN)
            else:
                wrap.configure(fg_color=BG_PANEL, border_color=BORDER_COLOR)
                bar.configure(fg_color=BORDER_COLOR)
                btn.configure(text_color=TEXT_SECONDARY)

    def _make_card(self, parent: Any, title: str, badge_text: str, badge_color: str = ACCENT_GREEN) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkFrame]:
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(card, fg_color=BG_HEADER, corner_radius=0, height=32)
        hdr.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
        hdr.grid_propagate(False)

        ctk.CTkLabel(
            hdr,
            text=title,
            font=FONT_HEADER,
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=10)

        badge = ctk.CTkLabel(
            hdr,
            text=badge_text,
            font=FONT_MONO_XS_BOLD,
            text_color=badge_color,
            fg_color=BG_RECESSED,
            corner_radius=2,
            padx=8,
            pady=2,
        )
        badge.pack(side="right", padx=8)

        body = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        return card, badge, body

    # ------------------------------------------------- Page [01] TELEMETRY & POWER
    def _build_page_telemetry_power(self) -> None:
        page = ctk.CTkFrame(self.stage, fg_color="transparent", corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure((0, 1), weight=1, uniform="p1c")
        page.grid_rowconfigure((0, 1), weight=1, uniform="p1r")
        self._pages["01"] = page

        # --- Card 1: GPU & VRAM Telemetry ---
        gpu_card, self.lbl_gpu_pstate_badge, g_body = self._make_card(page, "GPU & VRAM TELEMETRY", "P-STATE: --", ACCENT_GREEN)
        gpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        g_body.grid_columnconfigure(0, weight=1)

        self.lbl_gpu_name = ctk.CTkLabel(g_body, text=self.mon.fallback_gpu_name, font=FONT_MONO_SM_BOLD, text_color=TEXT_SECONDARY, anchor="w")
        self.lbl_gpu_name.pack(fill="x", pady=(0, 6))

        g_metrics = ctk.CTkFrame(g_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        g_metrics.pack(fill="x", pady=(0, 8))
        g_metrics.grid_columnconfigure((0, 1, 2), weight=1, uniform="gm")

        self.lbl_gpu_temp_val = self._metric_cell(g_metrics, 0, "CORE TEMP", "  -- °C", ACCENT_GREEN)
        self.lbl_gpu_clk_val = self._metric_cell(g_metrics, 1, "CORE CLOCK", "---- MHz", TEXT_PRIMARY)
        self.lbl_gpu_load_val = self._metric_cell(g_metrics, 2, "GPU LOAD", "  -- %", ACCENT_CYAN)

        load_hdr = ctk.CTkFrame(g_body, fg_color="transparent")
        load_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(load_hdr, text="GPU COMPUTE UTILIZATION", font=FONT_MONO_XS, text_color=TEXT_MUTED).pack(side="left")
        self.lbl_gpu_load_pct = ctk.CTkLabel(load_hdr, text="  0.0 %", font=FONT_MONO_XS_BOLD, text_color=ACCENT_GREEN, anchor="e")
        self.lbl_gpu_load_pct.pack(side="right")
        self.bar_gpu_load = SegmentedProgressBar(g_body, height=11, fill_color=ACCENT_GREEN)
        self.bar_gpu_load.pack(fill="x", pady=(0, 10))

        vram_hdr = ctk.CTkFrame(g_body, fg_color="transparent")
        vram_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(vram_hdr, text="DEDICATED VRAM ALLOCATION", font=FONT_MONO_XS, text_color=TEXT_MUTED).pack(side="left")
        self.lbl_vram_detail = ctk.CTkLabel(vram_hdr, text="   0 /    0 MB ( 0.0 %)", font=FONT_MONO_XS_BOLD, text_color=ACCENT_CYAN, anchor="e")
        self.lbl_vram_detail.pack(side="right")
        self.bar_vram = SegmentedProgressBar(g_body, height=11, fill_color=ACCENT_CYAN)
        self.bar_vram.pack(fill="x")

        # --- Card 2: CPU Package Telemetry ---
        cpu_card, self.lbl_cpu_topo_badge, c_body = self._make_card(
            page,
            "CPU PACKAGE TELEMETRY",
            f"{self.mon.cpu_cores}C / {self.mon.cpu_threads}T",
            ACCENT_CYAN,
        )
        cpu_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))

        self.lbl_cpu_name = ctk.CTkLabel(c_body, text=self.mon.cpu_name, font=FONT_MONO_SM_BOLD, text_color=TEXT_SECONDARY, anchor="w")
        self.lbl_cpu_name.pack(fill="x", pady=(0, 6))

        c_metrics = ctk.CTkFrame(c_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        c_metrics.pack(fill="x", pady=(0, 8))
        c_metrics.grid_columnconfigure((0, 1, 2), weight=1, uniform="cm")

        self.lbl_cpu_temp_val = self._metric_cell(c_metrics, 0, "PACKAGE TEMP", "  -- °C", ACCENT_GREEN)
        self.lbl_cpu_freq_val = self._metric_cell(c_metrics, 1, "LIVE FREQUENCY", "-.-- GHz", TEXT_PRIMARY)
        self.lbl_cpu_load_val = self._metric_cell(c_metrics, 2, "CORE LOAD", " 0.0 %", ACCENT_CYAN)

        cl_hdr = ctk.CTkFrame(c_body, fg_color="transparent")
        cl_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(cl_hdr, text="ALL-CORE CPU UTILIZATION", font=FONT_MONO_XS, text_color=TEXT_MUTED).pack(side="left")
        self.lbl_cpu_load_pct = ctk.CTkLabel(cl_hdr, text="  0.0 %", font=FONT_MONO_XS_BOLD, text_color=ACCENT_GREEN, anchor="e")
        self.lbl_cpu_load_pct.pack(side="right")
        self.bar_cpu_load = SegmentedProgressBar(c_body, height=11, fill_color=ACCENT_GREEN)
        self.bar_cpu_load.pack(fill="x", pady=(0, 10))

        cp_hdr = ctk.CTkFrame(c_body, fg_color="transparent")
        cp_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(cp_hdr, text="PEAK LOGICAL THREAD LOAD", font=FONT_MONO_XS, text_color=TEXT_MUTED).pack(side="left")
        self.lbl_cpu_peak = ctk.CTkLabel(cp_hdr, text="  0.0 %", font=FONT_MONO_XS_BOLD, text_color=ACCENT_AMBER, anchor="e")
        self.lbl_cpu_peak.pack(side="right")
        self.bar_cpu_peak = SegmentedProgressBar(c_body, height=11, fill_color=ACCENT_AMBER)
        self.bar_cpu_peak.pack(fill="x")

        # --- Card 3: Detailed RAM Breakdown ---
        ram_card, self.lbl_ram_pct_badge, r_body = self._make_card(page, "DETAILED RAM BREAKDOWN", "0.0 % USED", ACCENT_GREEN)
        ram_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))

        self.ram_rows: dict[str, tuple[ctk.CTkLabel, SegmentedProgressBar]] = {}
        ram_specs = [
            ("installed", "TOTAL INSTALLED RAM", ACCENT_CYAN),
            ("used", "ACTIVE USED RAM", ACCENT_GREEN),
            ("kernel", "WINDOWS OS & KERNEL FOOTPRINT", ACCENT_AMBER),
            ("free", "AVAILABLE FREE RAM", ACCENT_GREEN),
        ]
        for key, label_txt, bar_col in ram_specs:
            row_box = ctk.CTkFrame(r_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
            row_box.pack(fill="x", pady=(0, 7))
            top = ctk.CTkFrame(row_box, fg_color="transparent")
            top.pack(fill="x", padx=8, pady=(4, 2))
            ctk.CTkLabel(top, text=label_txt, font=FONT_MONO_XS, text_color=TEXT_SECONDARY, anchor="w").pack(side="left")
            val_lbl = ctk.CTkLabel(top, text=" 0.00 GB", font=FONT_MONO_SM_BOLD, text_color=TEXT_PRIMARY, anchor="e")
            val_lbl.pack(side="right")
            bar = SegmentedProgressBar(row_box, height=9, fill_color=bar_col)
            bar.pack(fill="x", padx=8, pady=(0, 5))
            self.ram_rows[key] = (val_lbl, bar)

        # --- Card 4: Charger & Battery Power Telemetry ---
        pwr_card, self.lbl_pwr_status_badge, p_body = self._make_card(page, "CHARGER & BATTERY POWER", "AC MAINS", ACCENT_GREEN)
        pwr_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(6, 0))

        p_metrics = ctk.CTkFrame(p_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        p_metrics.pack(fill="x", pady=(0, 8))
        p_metrics.grid_columnconfigure((0, 1, 2), weight=1, uniform="pm")

        self.lbl_bat_pct_val = self._metric_cell(p_metrics, 0, "BATTERY LEVEL", "  -- %", ACCENT_GREEN)
        self.lbl_bat_rate_val = self._metric_cell(p_metrics, 1, "CHARGE / DRAW", "  -- W", ACCENT_CYAN)
        self.lbl_bat_eta_val = self._metric_cell(p_metrics, 2, "EST. REMAINING", "  AC", TEXT_PRIMARY)

        bat_hdr = ctk.CTkFrame(p_body, fg_color="transparent")
        bat_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(bat_hdr, text="BATTERY CHARGE STATE", font=FONT_MONO_XS, text_color=TEXT_MUTED).pack(side="left")
        self.lbl_bat_line = ctk.CTkLabel(bat_hdr, text="AC PLUGGED IN", font=FONT_MONO_XS_BOLD, text_color=ACCENT_GREEN, anchor="e")
        self.lbl_bat_line.pack(side="right")
        self.bar_battery = SegmentedProgressBar(p_body, height=11, fill_color=ACCENT_GREEN)
        self.bar_battery.pack(fill="x", pady=(0, 10))

        plan_box = ctk.CTkFrame(p_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        plan_box.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(plan_box, text="ACTIVE WINDOWS POWER PLAN", font=FONT_MONO_XS, text_color=TEXT_MUTED, anchor="w").pack(fill="x", padx=8, pady=(5, 0))
        self.lbl_active_plan = ctk.CTkLabel(plan_box, text="BALANCED", font=FONT_MONO_BOLD, text_color=ACCENT_CYAN, anchor="w")
        self.lbl_active_plan.pack(fill="x", padx=8, pady=(0, 5))

        p_btns = ctk.CTkFrame(p_body, fg_color="transparent")
        p_btns.pack(fill="x")
        p_btns.grid_columnconfigure(0, weight=3)
        p_btns.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            p_btns,
            text="FORCE AC TURBO POWER MODE",
            height=34,
            corner_radius=2,
            border_width=1,
            border_color=ACCENT_GREEN,
            fg_color=ACCENT_GREEN_DIM,
            hover_color="#245222",
            text_color=ACCENT_GREEN,
            font=FONT_MONO_SM_BOLD,
            command=self._on_ac_turbo_mode,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            p_btns,
            text="RESTORE BALANCED",
            height=34,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=BG_RECESSED,
            hover_color=SLATE_SURFACE,
            text_color=TEXT_SECONDARY,
            font=FONT_MONO_SM_BOLD,
            command=self._on_balanced_mode,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _metric_cell(self, parent: ctk.CTkFrame, col: int, title: str, init_val: str, color: str) -> ctk.CTkLabel:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=0, column=col, sticky="ew", padx=8, pady=7)
        ctk.CTkLabel(cell, text=title, font=FONT_MONO_XS, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        val = ctk.CTkLabel(cell, text=init_val, font=FONT_VAL_LG, text_color=color, anchor="e")
        val.pack(fill="x")
        return val

    # ------------------------------------------------- Page [02] CLOCKS & FANS
    def _build_page_clocks_fans(self) -> None:
        page = ctk.CTkFrame(self.stage, fg_color="transparent", corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure((0, 1), weight=1, uniform="p2c")
        page.grid_rowconfigure(1, weight=1)
        self._pages["02"] = page

        # --- Top Left: GPU Clock Overclock (MHz) ---
        gpu_oc, self.lbl_oc_gpu_badge, go_body = self._make_card(page, "GPU CORE CLOCK OVERCLOCK (MHz)", "STOCK", ACCENT_GREEN)
        gpu_oc.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))

        g_read = ctk.CTkFrame(go_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        g_read.pack(fill="x", pady=(0, 8))
        g_read.grid_columnconfigure((0, 1), weight=1, uniform="goc")
        self.lbl_oc_gpu_live = self._metric_cell(g_read, 0, "LIVE GPU CLOCK", "---- MHz", ACCENT_GREEN)
        self.lbl_oc_gpu_target = self._metric_cell(g_read, 1, "TARGET LOCK", "   STOCK", ACCENT_AMBER)

        sp_g = ctk.CTkFrame(go_body, fg_color="transparent")
        sp_g.pack(fill="x")
        ctk.CTkLabel(sp_g, text="SETPOINT [800–1850 MHz | 15 MHz STEP]", font=FONT_MONO_XS, text_color=TEXT_SECONDARY).pack(side="left")
        self.lbl_oc_gpu_sp = ctk.CTkLabel(sp_g, text="1355 MHz", font=FONT_MONO_SM_BOLD, text_color=ACCENT_AMBER, anchor="e")
        self.lbl_oc_gpu_sp.pack(side="right")

        gpu_ok = self.clocks.nvidia_available
        self.slider_gpu = ctk.CTkSlider(
            go_body,
            from_=GPU_CLOCK_MIN_MHZ,
            to=GPU_CLOCK_MAX_MHZ,
            number_of_steps=70,
            height=16,
            corner_radius=2,
            button_corner_radius=2,
            button_length=10,
            fg_color=BORDER_COLOR,
            progress_color=ACCENT_AMBER if gpu_ok else DIVIDER_COLOR,
            button_color=ACCENT_AMBER if gpu_ok else TEXT_MUTED,
            button_hover_color=TEXT_PRIMARY,
            state="normal" if gpu_ok else "disabled",
            command=self._on_gpu_drag,
        )
        self.slider_gpu.set(self._gpu_slider_mhz)
        self.slider_gpu.pack(fill="x", pady=(4, 2))

        g_ticks = ctk.CTkFrame(go_body, fg_color="transparent")
        g_ticks.pack(fill="x", pady=(0, 8))
        for idx, t in enumerate(("800", "1150", "1500", "1850")):
            g_ticks.grid_columnconfigure(idx, weight=1)
            ctk.CTkLabel(
                g_ticks,
                text=t,
                font=FONT_MONO_XS,
                text_color=TEXT_MUTED,
                anchor="w" if idx == 0 else ("e" if idx == 3 else "center"),
            ).grid(row=0, column=idx, sticky="ew")

        g_btns = ctk.CTkFrame(go_body, fg_color="transparent")
        g_btns.pack(fill="x", pady=(0, 6))
        g_btns.grid_columnconfigure((0, 1), weight=1, uniform="ogb")
        ctk.CTkButton(
            g_btns,
            text="APPLY CLOCK",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=ACCENT_AMBER,
            fg_color=ACCENT_AMBER_DIM,
            hover_color="#4D3311",
            text_color=ACCENT_AMBER,
            font=FONT_MONO_SM_BOLD,
            state="normal" if gpu_ok else "disabled",
            command=self._on_gpu_apply,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            g_btns,
            text="RESET STOCK",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=BG_RECESSED,
            hover_color=SLATE_SURFACE,
            text_color=TEXT_SECONDARY,
            font=FONT_MONO_SM_BOLD,
            state="normal" if gpu_ok else "disabled",
            command=self._on_gpu_reset,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.lbl_oc_watchdog = ctk.CTkLabel(
            go_body,
            text="THERMAL WATCHDOG: 86°C AUTO-CUTOFF ARMED",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_GREEN,
            fg_color=BG_RECESSED,
            corner_radius=2,
            pady=4,
        )
        self.lbl_oc_watchdog.pack(fill="x")

        # --- Top Right: CPU Max Frequency Control (GHz) ---
        cpu_oc, self.lbl_oc_cpu_badge, co_body = self._make_card(page, "CPU MAX FREQUENCY CONTROL (GHz)", "TURBO AUTO", ACCENT_GREEN)
        cpu_oc.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))

        c_read = ctk.CTkFrame(co_body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
        c_read.pack(fill="x", pady=(0, 8))
        c_read.grid_columnconfigure((0, 1), weight=1, uniform="coc")
        self.lbl_oc_cpu_live = self._metric_cell(c_read, 0, "LIVE CPU CLOCK", "-.-- GHz", ACCENT_GREEN)
        self.lbl_oc_cpu_target = self._metric_cell(c_read, 1, "PROCFREQMAX CAP", "    AUTO", ACCENT_AMBER)

        sp_c = ctk.CTkFrame(co_body, fg_color="transparent")
        sp_c.pack(fill="x")
        ctk.CTkLabel(sp_c, text="SETPOINT [1.80–4.50 GHz | 0.10 GHz STEP]", font=FONT_MONO_XS, text_color=TEXT_SECONDARY).pack(side="left")
        self.lbl_oc_cpu_sp = ctk.CTkLabel(sp_c, text="3.20 GHz (3200 MHz)", font=FONT_MONO_SM_BOLD, text_color=ACCENT_AMBER, anchor="e")
        self.lbl_oc_cpu_sp.pack(side="right")

        self.slider_cpu = ctk.CTkSlider(
            co_body,
            from_=CPU_FREQ_MIN_GHZ,
            to=CPU_FREQ_MAX_GHZ,
            number_of_steps=27,
            height=16,
            corner_radius=2,
            button_corner_radius=2,
            button_length=10,
            fg_color=BORDER_COLOR,
            progress_color=ACCENT_AMBER,
            button_color=ACCENT_AMBER,
            button_hover_color=TEXT_PRIMARY,
            command=self._on_cpu_drag,
        )
        self.slider_cpu.set(self._cpu_slider_ghz)
        self.slider_cpu.pack(fill="x", pady=(4, 2))

        c_ticks = ctk.CTkFrame(co_body, fg_color="transparent")
        c_ticks.pack(fill="x", pady=(0, 8))
        for idx, t in enumerate(("1.80", "2.70", "3.60", "4.50")):
            c_ticks.grid_columnconfigure(idx, weight=1)
            ctk.CTkLabel(
                c_ticks,
                text=t,
                font=FONT_MONO_XS,
                text_color=TEXT_MUTED,
                anchor="w" if idx == 0 else ("e" if idx == 3 else "center"),
            ).grid(row=0, column=idx, sticky="ew")

        c_btns = ctk.CTkFrame(co_body, fg_color="transparent")
        c_btns.pack(fill="x", pady=(0, 6))
        c_btns.grid_columnconfigure((0, 1), weight=1, uniform="ocb")
        ctk.CTkButton(
            c_btns,
            text="APPLY FREQ",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=ACCENT_AMBER,
            fg_color=ACCENT_AMBER_DIM,
            hover_color="#4D3311",
            text_color=ACCENT_AMBER,
            font=FONT_MONO_SM_BOLD,
            command=self._on_cpu_apply,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            c_btns,
            text="TURBO AUTO",
            height=30,
            corner_radius=2,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=BG_RECESSED,
            hover_color=SLATE_SURFACE,
            text_color=TEXT_SECONDARY,
            font=FONT_MONO_SM_BOLD,
            command=self._on_cpu_reset,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctk.CTkLabel(
            co_body,
            text="POWERCFG PROCFREQMAX APPLIES AC+DC CEILING (0 = UNLIMITED TURBO)",
            font=FONT_MONO_XS,
            text_color=TEXT_MUTED,
            fg_color=BG_RECESSED,
            corner_radius=2,
            pady=4,
        ).pack(fill="x")

        # --- Bottom Full-Width: Hybrid Multi-Fan Speed Array ---
        fan_card = ctk.CTkFrame(
            page,
            fg_color=BG_PANEL,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        fan_card.grid(row=1, column=0, columnspan=2, sticky="nsew")
        fan_card.grid_columnconfigure(0, weight=1)
        fan_card.grid_rowconfigure(1, weight=1)

        f_hdr = ctk.CTkFrame(fan_card, fg_color=BG_HEADER, corner_radius=0, height=34)
        f_hdr.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
        f_hdr.grid_propagate(False)

        ctk.CTkLabel(
            f_hdr,
            text="HYBRID MULTI-FAN SPEED ENGINE (LHM PAWNIO + THERMAL CURVE FALLBACK)",
            font=FONT_HEADER,
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=10)

        self.lbl_fan_count_badge = ctk.CTkLabel(
            f_hdr,
            text="FANS: 0",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_CYAN,
            fg_color=BG_RECESSED,
            corner_radius=2,
            padx=8,
            pady=2,
        )
        self.lbl_fan_count_badge.pack(side="right", padx=(6, 8))

        self.lbl_fan_mode_badge = ctk.CTkLabel(
            f_hdr,
            text="HYBRID THERMAL CURVE ESTIMATION",
            font=FONT_MONO_XS_BOLD,
            text_color=ACCENT_AMBER,
            fg_color=BG_RECESSED,
            corner_radius=2,
            padx=8,
            pady=2,
        )
        self.lbl_fan_mode_badge.pack(side="right", padx=(0, 6))

        self.fan_scroll = ctk.CTkScrollableFrame(
            fan_card,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=BORDER_COLOR,
            scrollbar_button_hover_color=SLATE_SURFACE,
        )
        self.fan_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

    # ------------------------------------------------- Page [03] SYSTEM BOOSTERS
    def _build_page_system_boosters(self) -> None:
        page = ctk.CTkFrame(self.stage, fg_color="transparent", corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure((0, 1), weight=1, uniform="p3c")
        page.grid_rowconfigure((1, 2), weight=1, uniform="p3r")
        self._pages["03"] = page

        # Top Control Banner
        banner = ctk.CTkFrame(
            page,
            fg_color=BG_PANEL,
            border_color=BORDER_COLOR,
            border_width=1,
            corner_radius=2,
        )
        banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        banner.grid_columnconfigure(0, weight=1)

        b_left = ctk.CTkFrame(banner, fg_color="transparent")
        b_left.grid(row=0, column=0, sticky="w", padx=12, pady=10)
        ctk.CTkLabel(
            b_left,
            text="DEDICATED SYSTEM OPTIMIZATION MODULES",
            font=FONT_HEADER,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            b_left,
            text="Execute targeted memory, NVIDIA GPU scheduling, CPU core unparking, or Windows OS service optimization independently.",
            font=FONT_UI_SM,
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w")

        b_right = ctk.CTkFrame(banner, fg_color="transparent")
        b_right.grid(row=0, column=1, sticky="e", padx=12, pady=10)
        self.lbl_boost_counter = ctk.CTkLabel(
            b_right,
            text="ACTIVE MODULES: 0 / 4",
            font=FONT_MONO_SM_BOLD,
            text_color=ACCENT_CYAN,
            fg_color=BG_RECESSED,
            corner_radius=2,
            padx=10,
            pady=6,
        )
        self.lbl_boost_counter.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            b_right,
            text="ACTIVATE ALL BOOSTERS",
            height=34,
            corner_radius=2,
            border_width=1,
            border_color=ACCENT_GREEN,
            fg_color=ACCENT_GREEN_DIM,
            hover_color="#245222",
            text_color=ACCENT_GREEN,
            font=FONT_MONO_SM_BOLD,
            command=self._on_boost_all,
        ).pack(side="left")

        self.booster_widgets: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel, ctk.CTkButton]] = {}
        card_defs = [
            (
                "ram",
                1,
                0,
                "[01] RAM BOOST",
                "Flushes process working sets (EmptyWorkingSet) and purges the Windows standby memory list (elevated) to reclaim physical RAM immediately.",
                "ACTIVATE BOOST",
                self._on_boost_ram,
            ),
            (
                "gpu",
                1,
                1,
                "[02] GPU BOOST",
                "Enforces DirectX High-Performance GPU preferences, disables PCIe ASPM power saving on AC, enables HAGS scheduling, and maximizes P0 responsiveness.",
                "ACTIVATE BOOST",
                self._on_boost_gpu,
            ),
            (
                "cpu",
                2,
                0,
                "[03] CPU BOOST",
                "Unparks all CPU logical cores (CPMINCORES 100), locks AC processor min/max state to 100%, disables idle throttling, and enables Aggressive Boost.",
                "ACTIVATE BOOST",
                self._on_boost_cpu,
            ),
            (
                "os",
                2,
                1,
                "[04] WINDOWS OS BOOST",
                "Suspends SysMain/DiagTrack telemetry services, clears temporary & DirectX/NVIDIA/AMD shader caches, flushes DNS, and elevates Game Mode priority.",
                "ACTIVATE BOOST",
                self._on_boost_os,
            ),
        ]

        for key, r, c, title, desc, btn_txt, handler in card_defs:
            card, badge, body = self._make_card(page, title, "STANDBY", TEXT_MUTED)
            pad_r = (0, 6) if c == 0 else (6, 0)
            pad_b = (0, 6) if r == 1 else (6, 0)
            card.grid(row=r, column=c, sticky="nsew", padx=pad_r, pady=pad_b)

            ctk.CTkLabel(
                body,
                text=desc,
                font=FONT_UI_SM,
                text_color=TEXT_SECONDARY,
                wraplength=390,
                justify="left",
                anchor="nw",
            ).pack(fill="x", pady=(2, 10))

            status_box = ctk.CTkFrame(body, fg_color=BG_RECESSED, border_color=BORDER_COLOR, border_width=1, corner_radius=2)
            status_box.pack(fill="x", pady=(0, 12))
            status_lbl = ctk.CTkLabel(
                status_box,
                text="STANDBY",
                font=FONT_MONO_XS_BOLD,
                text_color=TEXT_SECONDARY,
                anchor="w",
                padx=8,
                pady=7,
            )
            status_lbl.pack(fill="x")

            btn = ctk.CTkButton(
                body,
                text=btn_txt,
                height=34,
                corner_radius=2,
                border_width=1,
                border_color=ACCENT_GREEN,
                fg_color=SLATE_SURFACE,
                hover_color=ACCENT_GREEN_DIM,
                text_color=ACCENT_GREEN,
                font=FONT_MONO_SM_BOLD,
                command=handler,
            )
            btn.pack(fill="x", side="bottom")
            self.booster_widgets[key] = (badge, status_lbl, btn)

    # ------------------------------------------------- Actions & Callbacks
    def _run_bg(self, fn: Any, after_ui: Any = None) -> None:
        def _worker() -> None:
            try:
                ok, msg = fn()
                self._status_queue.put(msg if ok else f"ERR: {msg}")
            except Exception as exc:
                self._status_queue.put(f"ERR: {exc}")
            if after_ui is not None:
                self.after(0, after_ui)

        threading.Thread(target=_worker, daemon=True).start()

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

    def _sync_clock_ui(self) -> None:
        st = self.clocks.get_state()
        if not st.nvidia_available:
            self.lbl_oc_gpu_badge.configure(text="NO NVIDIA GPU", text_color=TEXT_MUTED)
            self.lbl_oc_gpu_target.configure(text="     N/A", text_color=TEXT_MUTED)
        elif st.target_gpu_mhz is not None:
            self.lbl_oc_gpu_badge.configure(text="LOCKED", text_color=ACCENT_AMBER)
            self.lbl_oc_gpu_target.configure(text=f"{st.target_gpu_mhz:4d} MHz", text_color=ACCENT_AMBER)
        else:
            self.lbl_oc_gpu_badge.configure(text="STOCK", text_color=ACCENT_GREEN)
            self.lbl_oc_gpu_target.configure(text="   STOCK", text_color=TEXT_SECONDARY)

        if st.watchdog_tripped:
            self.lbl_oc_watchdog.configure(text="THERMAL WATCHDOG: TRIPPED (>=86°C) — RESET TO STOCK", text_color=STATUS_CRIT)
            self.lbl_sidebar_watchdog.configure(text="WATCHDOG: TRIPPED", text_color=STATUS_CRIT)
        else:
            self.lbl_oc_watchdog.configure(text="THERMAL WATCHDOG: 86°C AUTO-CUTOFF ARMED", text_color=ACCENT_GREEN)
            self.lbl_sidebar_watchdog.configure(text="WATCHDOG: 86°C ARMED", text_color=ACCENT_GREEN)

        if st.target_cpu_ghz is not None and st.target_cpu_mhz > 0:
            snapped = snap_cpu_freq_ghz(st.target_cpu_ghz)
            self._cpu_slider_ghz = snapped
            self.slider_cpu.set(snapped)
            self.lbl_oc_cpu_sp.configure(text=f"{snapped:4.2f} GHz ({st.target_cpu_mhz:4d} MHz)")
            self.lbl_oc_cpu_badge.configure(text="CAPPED", text_color=ACCENT_AMBER)
            self.lbl_oc_cpu_target.configure(text=f"{snapped:4.2f} GHz", text_color=ACCENT_AMBER)
        else:
            self.lbl_oc_cpu_badge.configure(text="TURBO AUTO", text_color=ACCENT_GREEN)
            self.lbl_oc_cpu_target.configure(text="    AUTO", text_color=TEXT_SECONDARY)

    def _sync_booster_ui(self) -> None:
        self.lbl_boost_counter.configure(text=f"ACTIVE MODULES: {self.boosters.active_count()} / 4")
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
                text_color=ACCENT_GREEN if active else TEXT_MUTED,
            )
            status_lbl.configure(
                text=summary,
                text_color=ACCENT_GREEN if active else TEXT_SECONDARY,
            )
            if one_shot:
                btn.configure(text="FLUSH RAM AGAIN" if active else "ACTIVATE BOOST")
            else:
                btn.configure(
                    text="DEACTIVATE" if active else "ACTIVATE BOOST",
                    border_color=ACCENT_AMBER if active else ACCENT_GREEN,
                    text_color=ACCENT_AMBER if active else ACCENT_GREEN,
                )

    # ------------------------------------------------- Overlays (Industrial)
    def _toggle_mini(self) -> None:
        self.mini_on = not self.mini_on
        if self.mini_on:
            self._open_mini()
            self.btn_mini.configure(text="MINI OVERLAY : ON", border_color=ACCENT_GREEN, text_color=ACCENT_GREEN)
        else:
            if self.mini_win:
                self.mini_win.destroy()
                self.mini_win = None
            self.btn_mini.configure(text="MINI OVERLAY : OFF", border_color=BORDER_COLOR, text_color=TEXT_SECONDARY)

    def _open_mini(self) -> None:
        if self.mini_win:
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.92)
        win.configure(bg=BG_BASE, highlightthickness=1, highlightbackground=ACCENT_GREEN)
        win.geometry("460x42+24+24")

        lbl = tk.Label(
            win,
            text="BANKO // GPU --°C | CPU --°C | RAM --% | VRAM --%",
            font=("Consolas", 10, "bold"),
            fg=ACCENT_GREEN,
            bg=BG_BASE,
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
            self.btn_fps.configure(text="DWM FPS OVERLAY : ON", border_color=ACCENT_GREEN, text_color=ACCENT_GREEN)
        else:
            if self.fps_win:
                self.fps_win.destroy()
                self.fps_win = None
            self.btn_fps.configure(text="DWM FPS OVERLAY : OFF", border_color=BORDER_COLOR, text_color=TEXT_SECONDARY)

    def _open_fps(self) -> None:
        if self.fps_win:
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG_BASE, highlightthickness=1, highlightbackground=ACCENT_GREEN)
        win.geometry("126x34+18+76")
        lbl = tk.Label(win, text="FPS --", font=("Consolas", 14, "bold"), fg=ACCENT_GREEN, bg=BG_BASE)
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

    # ------------------------------------------------- Live Telemetry Refresh
    def _fmt_console(self, msg: str) -> str:
        return f"[{time.strftime('%H:%M:%S')}] {msg}"

    def _ui_tick(self) -> None:
        while not self._status_queue.empty():
            try:
                self.lbl_console.configure(text=self._fmt_console(self._status_queue.get_nowait()))
            except queue.Empty:
                break

        snap: Snapshot = self.mon.get()

        # Heartbeat square
        self._heartbeat = not self._heartbeat
        self.hb_canvas.itemconfig(self._hb_rect, fill=ACCENT_GREEN if self._heartbeat else SLATE_SURFACE)

        # Thermal Watchdog 86C Check
        tripped, w_msg = self.clocks.check_thermal_watchdog(snap.gpu_temp)
        if tripped and w_msg:
            self._status_queue.put(w_msg)
            self._sync_clock_ui()

        # Header live summary strip
        gt_s = f"{snap.gpu_temp:2.0f}°C" if snap.gpu_temp is not None else "--°C"
        gc_s = f"{snap.gpu_clock_mhz:4.0f}MHz" if snap.gpu_clock_mhz is not None else "----MHz"
        ct_s = f"{snap.cpu_temp:2.0f}°C" if snap.cpu_temp is not None else "--°C"
        cf_s = f"{snap.cpu_freq_ghz:4.2f}GHz" if snap.cpu_freq_ghz > 0 else "-.--GHz"
        bat_s = f"{snap.battery_percent:3.0f}%" if snap.battery_percent is not None else " AC "
        self.lbl_header_strip.configure(
            text=f"GPU: {gt_s} {gc_s} | CPU: {ct_s} {cf_s} | RAM: {snap.ram_percent:4.1f}% | BAT: {bat_s}"
        )

        # Page [01] GPU & VRAM
        self.lbl_gpu_name.configure(text=snap.gpu_name)
        self.lbl_gpu_pstate_badge.configure(text=f"P-STATE: {snap.gpu_pstate or '--'}")
        self.lbl_gpu_temp_val.configure(
            text=f"{snap.gpu_temp:4.0f} °C" if snap.gpu_temp is not None else "  -- °C",
            text_color=_temp_color(snap.gpu_temp),
        )
        self.lbl_gpu_clk_val.configure(
            text=f"{snap.gpu_clock_mhz:4.0f} MHz" if snap.gpu_clock_mhz is not None else "---- MHz"
        )
        g_load = snap.gpu_load if snap.gpu_load is not None else 0.0
        self.lbl_gpu_load_val.configure(text=f"{snap.gpu_load:4.0f} %" if snap.gpu_load is not None else "  -- %")
        self.lbl_gpu_load_pct.configure(text=f"{g_load:5.1f} %")
        self.bar_gpu_load.set_ratio(g_load / 100.0)

        if snap.gpu_vram_used_mb is not None and snap.gpu_vram_total_mb is not None:
            vp = snap.gpu_vram_pct or 0.0
            self.lbl_vram_detail.configure(
                text=f"{snap.gpu_vram_used_mb:4.0f} / {snap.gpu_vram_total_mb:4.0f} MB ({vp:4.1f} %)"
            )
            self.bar_vram.set_ratio(vp / 100.0)
        elif snap.gpu_vram_total_mb is not None:
            self.lbl_vram_detail.configure(text=f"  -- / {snap.gpu_vram_total_mb:4.0f} MB ( N/A )")
            self.bar_vram.set_ratio(0.0)
        else:
            self.lbl_vram_detail.configure(text="   N/A (SHARED)")
            self.bar_vram.set_ratio(0.0)

        # Page [01] CPU
        self.lbl_cpu_name.configure(text=snap.cpu_name)
        self.lbl_cpu_temp_val.configure(
            text=f"{snap.cpu_temp:4.0f} °C" if snap.cpu_temp is not None else "  -- °C",
            text_color=_temp_color(snap.cpu_temp),
        )
        self.lbl_cpu_freq_val.configure(text=f"{snap.cpu_freq_ghz:4.2f} GHz" if snap.cpu_freq_ghz > 0 else "-.-- GHz")
        self.lbl_cpu_load_val.configure(text=f"{snap.cpu_load:4.1f} %")
        self.lbl_cpu_load_pct.configure(text=f"{snap.cpu_load:5.1f} %")
        self.bar_cpu_load.set_ratio(snap.cpu_load / 100.0)
        self.lbl_cpu_peak.configure(text=f"{snap.cpu_peak_core:5.1f} %")
        self.bar_cpu_peak.set_ratio(snap.cpu_peak_core / 100.0)

        # Page [01] Detailed RAM Breakdown
        self.lbl_ram_pct_badge.configure(text=f"{snap.ram_percent:4.1f} % USED")
        denom = max(0.1, snap.ram_total_gb)
        inst_lbl, inst_bar = self.ram_rows["installed"]
        inst_lbl.configure(text=f"{snap.ram_installed_gb:5.2f} GB ({snap.ram_total_gb:5.2f} GB USABLE)")
        inst_bar.set_ratio(min(1.0, snap.ram_total_gb / max(0.1, snap.ram_installed_gb)))

        used_lbl, used_bar = self.ram_rows["used"]
        used_lbl.configure(text=f"{snap.ram_used_gb:5.2f} GB / {snap.ram_total_gb:5.2f} GB ({snap.ram_percent:4.1f}%)")
        used_bar.set_ratio(snap.ram_used_gb / denom)

        kern_lbl, kern_bar = self.ram_rows["kernel"]
        kern_pct = (snap.ram_os_kernel_gb / denom) * 100.0
        kern_lbl.configure(text=f"{snap.ram_os_kernel_gb:5.2f} GB ({kern_pct:4.1f}% | CACHE {snap.ram_sys_cache_gb:4.2f} GB)")
        kern_bar.set_ratio(snap.ram_os_kernel_gb / denom)

        free_lbl, free_bar = self.ram_rows["free"]
        free_pct = (snap.ram_free_gb / denom) * 100.0
        free_lbl.configure(text=f"{snap.ram_free_gb:5.2f} GB ({free_pct:4.1f}% FREE)")
        free_bar.set_ratio(snap.ram_free_gb / denom)

        # Page [01] Charger & Battery
        pwr_col = ACCENT_GREEN if snap.power_status in ("CHARGING", "AC PLUGGED IN", "AC MAINS") else ACCENT_AMBER
        self.lbl_pwr_status_badge.configure(text=snap.power_status, text_color=pwr_col)
        self.lbl_bat_line.configure(text=snap.power_status, text_color=pwr_col)
        if snap.battery_present and snap.battery_percent is not None:
            self.lbl_bat_pct_val.configure(text=f"{snap.battery_percent:4.0f} %", text_color=pwr_col)
            self.bar_battery.set_ratio(snap.battery_percent / 100.0, color_override=pwr_col)
        else:
            self.lbl_bat_pct_val.configure(text="AC ONLY", text_color=ACCENT_CYAN)
            self.bar_battery.set_ratio(1.0, color_override=ACCENT_CYAN)

        if snap.power_rate_w is not None:
            self.lbl_bat_rate_val.configure(text=f"{snap.power_rate_w:4.1f} W")
        elif snap.power_status == "AC PLUGGED IN":
            self.lbl_bat_rate_val.configure(text=" FULL AC")
        else:
            self.lbl_bat_rate_val.configure(text="  N/A W")

        if snap.battery_time_left_min is not None:
            hrs, mins = divmod(snap.battery_time_left_min, 60)
            self.lbl_bat_eta_val.configure(text=f"{hrs:2d}h {mins:02d}m")
        else:
            self.lbl_bat_eta_val.configure(text="  AC PWR" if snap.power_status != "DISCHARGING" else "  EST..")

        self.lbl_active_plan.configure(text=snap.power_plan_name)

        # Page [02] Clocks & Hybrid Fans
        self.lbl_oc_gpu_live.configure(
            text=f"{snap.gpu_clock_mhz:4.0f} MHz" if snap.gpu_clock_mhz is not None else "---- MHz"
        )
        self.lbl_oc_cpu_live.configure(
            text=f"{snap.cpu_freq_ghz:4.2f} GHz" if snap.cpu_freq_ghz > 0 else "-.-- GHz"
        )
        self._update_fan_array(snap.fan_state)

        # Overlays
        if self.mini_win is not None:
            vp_s = f"{snap.gpu_vram_pct:.0f}%" if snap.gpu_vram_pct is not None else "--%"
            self.mini_win._lbl.configure(  # type: ignore[attr-defined]
                text=f"BANKO // GPU {gt_s} {gc_s} | CPU {ct_s} {cf_s} | RAM {snap.ram_percent:.0f}% | VRAM {vp_s}"
            )
        if self.fps_win is not None:
            fps_txt = "FPS --" if snap.fps is None else f"FPS {snap.fps:3.0f}"
            self.fps_win._lbl.configure(text=fps_txt)  # type: ignore[attr-defined]

        self.after(1000, self._ui_tick)

    def _update_fan_array(self, fan_state: FanArrayState) -> None:
        is_est = fan_state.mode == "HYBRID_ESTIMATED"
        self.lbl_fan_count_badge.configure(
            text=f"FANS: {fan_state.fan_count}",
            text_color=ACCENT_AMBER if is_est else ACCENT_GREEN,
        )
        self.lbl_fan_mode_badge.configure(
            text=fan_state.mode_banner,
            text_color=ACCENT_AMBER if is_est else ACCENT_GREEN,
        )

        sig = tuple(f.identifier for f in fan_state.fans)
        if sig != self._fan_signature:
            for child in self.fan_scroll.winfo_children():
                child.destroy()
            self._fan_rows = [FanRowWidget(self.fan_scroll, f) for f in fan_state.fans]
            self._fan_signature = sig
            return

        for row, channel in zip(self._fan_rows, fan_state.fans):
            row.update(channel)

    def _finish_smoke_test(self) -> None:
        print("SMOKE_TEST_OK", flush=True)
        self._on_close()

    def _on_close(self) -> None:
        try:
            self.mon.stop()
        finally:
            self.destroy()


def main() -> None:
    smoke = "--smoke-test" in sys.argv
    app = BankoTacticalApp(smoke_test=smoke)
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
