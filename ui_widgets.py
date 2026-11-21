"""BANKO v3.0.0 Discord-Inspired Dark Tabular & HWiNFO64 UI Widgets.

Zero AI aesthetics: zero rounded cards, zero neon glows, zero gradients,
zero clipped labels. Pure 1px #313338 grid borders, #2B2D31 flat headers,
alternating #1E1F22 / #232428 Consolas rows, and sharp 2px action controls.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Sequence

import customtkinter as ctk

# --- Discord / Industrial Dark Palette ---
BG_RAIL = "#111214"
BG_SIDEBAR = "#18191C"
BG_MAIN = "#1E1F22"
BG_ROW_ALT = "#232428"
BG_HEADER = "#2B2D31"
BG_HOVER = "#35373C"
BORDER_GRID = "#313338"

ACCENT_BLURPLE = "#5865F2"
BLURPLE_HOVER = "#4752C4"
BLURPLE_DIM = "#2B3059"

STATUS_GREEN = "#23A55A"
GREEN_HOVER = "#1E8E4C"
GREEN_DIM = "#173324"

THERMAL_AMBER = "#F0B232"
AMBER_HOVER = "#C99220"
AMBER_DIM = "#3B3017"

DANGER_RED = "#F23F43"
RED_HOVER = "#C93033"
RED_DIM = "#3D1D20"

TEXT_PRIMARY = "#F2F3F5"
TEXT_SECONDARY = "#B5BAC1"
TEXT_MUTED = "#80848E"

# --- Consolas Monospace Typography ---
FONT_TITLE = ("Consolas", 13, "bold")
FONT_SECTION = ("Consolas", 11, "bold")
FONT_KPI = ("Consolas", 15, "bold")
FONT_CELL = ("Consolas", 10)
FONT_CELL_BOLD = ("Consolas", 10, "bold")
FONT_HDR = ("Consolas", 9, "bold")
FONT_XS = ("Consolas", 9)
FONT_XS_BOLD = ("Consolas", 9, "bold")
FONT_BTN = ("Consolas", 10, "bold")


_BUTTON_STYLES: dict[str, tuple[str, str, str, str]] = {
    "blurple": (ACCENT_BLURPLE, BLURPLE_HOVER, ACCENT_BLURPLE, TEXT_PRIMARY),
    "green": (GREEN_DIM, GREEN_HOVER, STATUS_GREEN, STATUS_GREEN),
    "amber": (AMBER_DIM, AMBER_HOVER, THERMAL_AMBER, THERMAL_AMBER),
    "red": (RED_DIM, RED_HOVER, DANGER_RED, DANGER_RED),
    "neutral": (BG_HEADER, BG_HOVER, BORDER_GRID, TEXT_PRIMARY),
    "muted": (BG_SIDEBAR, BG_HOVER, BORDER_GRID, TEXT_SECONDARY),
}


def SharpButton(
    master: Any,
    text: str,
    command: Any = None,
    style: str = "neutral",
    width: int = 0,
    height: int = 28,
    state: str = "normal",
    font: tuple[Any, ...] = FONT_BTN,
) -> ctk.CTkButton:
    """Create a crisp 2px-radius flat industrial action button."""
    fg, hover, border, txt = _BUTTON_STYLES.get(style, _BUTTON_STYLES["neutral"])
    kw: dict[str, Any] = {
        "text": text,
        "command": command,
        "height": height,
        "corner_radius": 2,
        "border_width": 1,
        "border_color": border,
        "fg_color": fg,
        "hover_color": hover,
        "text_color": txt,
        "font": font,
        "state": state,
    }
    if width > 0:
        kw["width"] = width
    return ctk.CTkButton(master, **kw)


def configure_treeview_style(root: tk.Misc) -> None:
    """Configure flat dark Discord/HWiNFO64 ttk.Treeview styling."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        "Banko.Treeview",
        background=BG_MAIN,
        fieldbackground=BG_MAIN,
        foreground=TEXT_PRIMARY,
        bordercolor=BORDER_GRID,
        lightcolor=BORDER_GRID,
        darkcolor=BORDER_GRID,
        borderwidth=0,
        rowheight=26,
        font=FONT_CELL,
    )
    style.map(
        "Banko.Treeview",
        background=[("selected", BLURPLE_DIM)],
        foreground=[("selected", TEXT_PRIMARY)],
    )
    style.configure(
        "Banko.Treeview.Heading",
        background=BG_HEADER,
        foreground=TEXT_SECONDARY,
        bordercolor=BORDER_GRID,
        lightcolor=BORDER_GRID,
        darkcolor=BORDER_GRID,
        relief="flat",
        borderwidth=1,
        font=FONT_HDR,
        padding=(8, 5),
    )
    style.map(
        "Banko.Treeview.Heading",
        background=[("active", BG_HOVER)],
        foreground=[("active", TEXT_PRIMARY)],
    )


class SectionHeaderBar(tk.Frame):
    """Flat #2B2D31 section title bar with 1px #313338 bottom border."""

    def __init__(
        self,
        master: Any,
        title: str,
        badge_text: str = "",
        badge_color: str = TEXT_SECONDARY,
    ) -> None:
        super().__init__(master, bg=BG_HEADER, height=30, bd=0, highlightthickness=0)
        self.pack_propagate(False)

        self.lbl_title = tk.Label(
            self,
            text=title,
            font=FONT_HDR,
            fg=TEXT_PRIMARY,
            bg=BG_HEADER,
            anchor="w",
            padx=10,
        )
        self.lbl_title.pack(side="left", fill="y")

        self.lbl_badge = tk.Label(
            self,
            text=badge_text,
            font=FONT_XS_BOLD,
            fg=badge_color,
            bg=BG_RAIL if badge_text else BG_HEADER,
            padx=8 if badge_text else 0,
            pady=2,
        )
        if badge_text:
            self.lbl_badge.pack(side="right", padx=8, pady=4)

        tk.Frame(self, bg=BORDER_GRID, height=1).place(
            relx=0, rely=1.0, anchor="sw", relwidth=1.0
        )

    def set_badge(self, text: str, color: str = TEXT_SECONDARY) -> None:
        if text:
            self.lbl_badge.configure(text=text, fg=color, bg=BG_RAIL, padx=8)
            if not self.lbl_badge.winfo_manager():
                self.lbl_badge.pack(side="right", padx=8, pady=4)
        else:
            self.lbl_badge.pack_forget()


class HWiNFOTable(tk.Frame):
    """1px-bordered HWiNFO64-style structured multi-column table with O(1) cell updates."""

    def __init__(
        self,
        master: Any,
        title: str,
        columns: Sequence[tuple[str, int, str]],
        badge_text: str = "",
        badge_color: str = TEXT_SECONDARY,
    ) -> None:
        """columns: sequence of (header_text, min_width_px, anchor 'w'|'e'|'center')."""
        super().__init__(
            master,
            bg=BG_MAIN,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        self._col_specs = list(columns)
        self._row_cells: dict[str, list[tk.Label]] = {}
        self._row_bgs: dict[str, str] = {}
        self._row_order: list[str] = []

        self.header_bar = SectionHeaderBar(
            self, title=title, badge_text=badge_text, badge_color=badge_color
        )
        self.header_bar.pack(fill="x", side="top")

        self.grid_wrap = tk.Frame(self, bg=BORDER_GRID, bd=0, highlightthickness=0)
        self.grid_wrap.pack(fill="both", expand=True, side="top")

        for c_idx, (col_title, min_w, anchor) in enumerate(self._col_specs):
            weight = 1 if c_idx == 0 or anchor == "w" else 0
            self.grid_wrap.grid_columnconfigure(
                c_idx, weight=weight, minsize=min_w
            )
            lbl = tk.Label(
                self.grid_wrap,
                text=col_title,
                font=FONT_HDR,
                fg=TEXT_SECONDARY,
                bg=BG_HEADER,
                anchor=anchor,
                padx=10,
                pady=5,
            )
            lbl.grid(
                row=0,
                column=c_idx,
                sticky="nsew",
                padx=(0, 1 if c_idx < len(self._col_specs) - 1 else 0),
                pady=(0, 1),
            )

    def add_row(
        self,
        row_key: str,
        values: Sequence[str],
        colors: Sequence[str | None] | None = None,
        bold_cols: Sequence[int] = (1,),
    ) -> None:
        r_idx = len(self._row_order) + 1
        bg = BG_MAIN if (r_idx % 2 == 1) else BG_ROW_ALT
        self._row_bgs[row_key] = bg
        self._row_order.append(row_key)

        cells: list[tk.Label] = []
        for c_idx, (_col_title, _min_w, anchor) in enumerate(self._col_specs):
            val = values[c_idx] if c_idx < len(values) else ""
            fg = TEXT_PRIMARY if c_idx == 0 else TEXT_SECONDARY
            if colors and c_idx < len(colors) and colors[c_idx]:
                fg = str(colors[c_idx])
            font = FONT_CELL_BOLD if c_idx in bold_cols else FONT_CELL
            lbl = tk.Label(
                self.grid_wrap,
                text=val,
                font=font,
                fg=fg,
                bg=bg,
                anchor=anchor,
                padx=10,
                pady=5,
            )
            lbl.grid(
                row=r_idx,
                column=c_idx,
                sticky="nsew",
                padx=(0, 1 if c_idx < len(self._col_specs) - 1 else 0),
                pady=(0, 1),
            )
            cells.append(lbl)
        self._row_cells[row_key] = cells

    def update_row(
        self,
        row_key: str,
        values: Sequence[str],
        colors: Sequence[str | None] | None = None,
    ) -> None:
        cells = self._row_cells.get(row_key)
        if not cells:
            return
        for c_idx, lbl in enumerate(cells):
            if c_idx < len(values):
                new_txt = values[c_idx]
                if lbl.cget("text") != new_txt:
                    lbl.configure(text=new_txt)
            if colors and c_idx < len(colors) and colors[c_idx]:
                new_fg = str(colors[c_idx])
                if lbl.cget("fg") != new_fg:
                    lbl.configure(fg=new_fg)

    def clear_rows(self) -> None:
        for cells in self._row_cells.values():
            for lbl in cells:
                lbl.destroy()
        self._row_cells.clear()
        self._row_bgs.clear()
        self._row_order.clear()


class DataTreeTable(tk.Frame):
    """Scrollable dark ttk.Treeview wrapped in a 1px #313338 grid border."""

    def __init__(
        self,
        master: Any,
        columns: Sequence[tuple[str, str, int, str]],
        height: int = 14,
        on_double_click: Any = None,
    ) -> None:
        """columns: sequence of (id, heading, width_px, anchor 'w'|'e'|'center')."""
        super().__init__(
            master,
            bg=BG_MAIN,
            highlightbackground=BORDER_GRID,
            highlightthickness=1,
            bd=0,
        )
        self._col_ids = [c[0] for c in columns]
        self._key_to_iid: dict[str, str] = {}

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self,
            columns=self._col_ids,
            show="headings",
            style="Banko.Treeview",
            selectmode="browse",
            height=height,
        )
        for col_id, heading, width, anchor in columns:
            stretch = anchor == "w"
            self.tree.heading(col_id, text=heading, anchor=anchor)
            self.tree.column(
                col_id,
                width=width,
                minwidth=max(48, int(width * 0.65)),
                anchor=anchor,  # type: ignore[arg-type]
                stretch=stretch,
            )

        self.tree.tag_configure("even", background=BG_MAIN, foreground=TEXT_PRIMARY)
        self.tree.tag_configure("odd", background=BG_ROW_ALT, foreground=TEXT_PRIMARY)
        self.tree.tag_configure("green", background=BG_MAIN, foreground=STATUS_GREEN)
        self.tree.tag_configure("green_alt", background=BG_ROW_ALT, foreground=STATUS_GREEN)
        self.tree.tag_configure("amber", background=BG_MAIN, foreground=THERMAL_AMBER)
        self.tree.tag_configure("amber_alt", background=BG_ROW_ALT, foreground=THERMAL_AMBER)
        self.tree.tag_configure("red", background=BG_MAIN, foreground=DANGER_RED)
        self.tree.tag_configure("red_alt", background=BG_ROW_ALT, foreground=DANGER_RED)
        self.tree.tag_configure("blurple", background=BG_MAIN, foreground=ACCENT_BLURPLE)
        self.tree.tag_configure("blurple_alt", background=BG_ROW_ALT, foreground=ACCENT_BLURPLE)
        self.tree.tag_configure("muted", background=BG_MAIN, foreground=TEXT_MUTED)
        self.tree.tag_configure("muted_alt", background=BG_ROW_ALT, foreground=TEXT_MUTED)

        scroll = ctk.CTkScrollbar(
            self,
            orientation="vertical",
            command=self.tree.yview,
            fg_color=BG_SIDEBAR,
            button_color=BORDER_GRID,
            button_hover_color=BG_HOVER,
            corner_radius=0,
            width=12,
        )
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        if on_double_click is not None:
            self.tree.bind("<Double-1>", lambda _e: on_double_click())

    def set_rows(
        self,
        rows: Sequence[Sequence[Any]],
        key_col: int = 0,
        row_tones: Sequence[str] | None = None,
        keys: Sequence[str] | None = None,
    ) -> None:
        selected_keys: set[str] = set()
        for iid in self.tree.selection():
            vals = self.tree.item(iid, "values")
            if keys:
                for k, mapped_iid in self._key_to_iid.items():
                    if mapped_iid == iid:
                        selected_keys.add(k)
                        break
            elif vals and key_col < len(vals):
                selected_keys.add(str(vals[key_col]))

        self.tree.delete(*self.tree.get_children())
        self._key_to_iid.clear()

        to_reselect: list[str] = []
        for idx, row in enumerate(rows):
            tone = row_tones[idx] if row_tones and idx < len(row_tones) else ""
            if tone in ("green", "amber", "red", "blurple", "muted"):
                tag = tone if (idx % 2 == 0) else f"{tone}_alt"
            else:
                tag = "even" if (idx % 2 == 0) else "odd"

            str_vals = [str(v) for v in row]
            iid = self.tree.insert("", "end", values=str_vals, tags=(tag,))
            row_key = (
                str(keys[idx])
                if keys and idx < len(keys)
                else (str_vals[key_col] if key_col < len(str_vals) else str(idx))
            )
            self._key_to_iid[row_key] = iid
            if row_key in selected_keys:
                to_reselect.append(iid)

        if to_reselect:
            try:
                self.tree.selection_set(to_reselect[0])
            except tk.TclError:
                pass

    def prepend_or_append_row(
        self, values: Sequence[Any], tone: str = "", prepend: bool = False
    ) -> None:
        cnt = len(self.tree.get_children())
        if tone in ("green", "amber", "red", "blurple", "muted"):
            tag = tone if (cnt % 2 == 0) else f"{tone}_alt"
        else:
            tag = "even" if (cnt % 2 == 0) else "odd"
        pos = 0 if prepend else "end"
        iid = self.tree.insert("", pos, values=[str(v) for v in values], tags=(tag,))
        if not prepend:
            self.tree.see(iid)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._key_to_iid.clear()

    def get_selected_values(self) -> tuple[str, ...] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return tuple(str(v) for v in vals) if vals else None

    def get_selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        children = self.tree.get_children()
        try:
            return children.index(sel[0])
        except ValueError:
            return None


class FlatMeterBar(tk.Canvas):
    """Crisp flat 1px-bordered horizontal meter bar (zero neon glows)."""

    def __init__(
        self,
        master: Any,
        height: int = 12,
        fill_color: str = STATUS_GREEN,
        warn_threshold: float = 0.75,
        crit_threshold: float = 0.90,
    ) -> None:
        super().__init__(
            master,
            height=height,
            bg=BG_RAIL,
            highlightthickness=1,
            highlightbackground=BORDER_GRID,
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
        w = max(60, self.winfo_width())
        h = max(8, self.winfo_height())
        if self._ratio >= self._crit_t:
            col = DANGER_RED
        elif self._ratio >= self._warn_t:
            col = THERMAL_AMBER
        else:
            col = self._base_color
        fill_w = max(0.0, (w - 4.0) * self._ratio)
        if fill_w > 0:
            self.create_rectangle(2, 2, 2 + fill_w, max(3, h - 2), fill=col, outline="")
