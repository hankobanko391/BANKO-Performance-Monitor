"""BANKO PERFORMANCE MONITOR - lightweight hardware monitor & overlay for Windows.

Run:  python main.py
All artwork (snake emblem, cards, gauges, toggles, button) is drawn in code with
Pillow, so there are no external image files to go missing.
"""
from __future__ import annotations

import ctypes
import math
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from monitor import Monitor, boost_system

TITLE = "BANKO PERFORMANCE MONITOR"

# ---- palette ---------------------------------------------------------------- #
BG = (13, 15, 20)  # #0D0F14
BG_HEX = "#0D0F14"
TITLEBAR = (9, 11, 15)
TITLEBAR_HEX = "#090B0F"
PANEL = (18, 22, 29)
GREEN = (0, 255, 102)  # #00FF66
CYAN = (0, 229, 255)
PURPLE = (168, 85, 247)
WHITE = "#FFFFFF"
GRAY = "#98A2B3"
DIM = "#6B7686"

# ---- layout (design pixels, multiplied by the DPI scale at runtime) ---------- #
W, H = 900, 640
TB_H = 42
COLS = (28, 316, 604)
CW = 268
CARD_Y0, CARD_Y1 = 172, 486
CTRL_Y0, CTRL_Y1 = 538, 596


def hexc(c):
    return "#%02x%02x%02x" % tuple(c[:3])


def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# =============================================================================== #
#  Drawing helpers (Pillow)
# =============================================================================== #
def make_emblem(px: int, color=GREEN, glow=True, swirl=2.0, wob=0.05, freq=2.5, wmax=0.036) -> Image.Image:
    """Triple-snake emblem: three S-curved serpents spiralling out of one knot."""
    ss = 4
    n_px = px * ss
    img = Image.new("RGBA", (n_px, n_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n_px / 2
    big_r = n_px * 0.41
    steps = 200
    col = color + (255,)
    for arm in range(3):
        a0 = arm * 2 * math.pi / 3 - math.pi / 2
        base_pts = []
        for i in range(steps + 1):
            t = i / steps
            rr = big_r * (0.05 + 0.95 * t)
            th = a0 + swirl * t
            base_pts.append((c + rr * math.cos(th), c + rr * math.sin(th)))
        # serpentine wiggle: displace each point along the local normal of the path
        pts = []
        for i, (x, y) in enumerate(base_pts):
            t = i / steps
            xa, ya = base_pts[max(0, i - 1)]
            xb, yb = base_pts[min(steps, i + 1)]
            tx, ty = xb - xa, yb - ya
            ln = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / ln, tx / ln
            off = n_px * wob * math.sin(t * math.pi * 2 * freq) * math.sin(min(1.0, t * 1.6) * math.pi * 0.5)
            pts.append((x + nx * off, y + ny * off, t))
        # body: round brush with a width envelope (thin tail, fat belly, slim neck)
        for (x, y, t) in pts:
            belly = math.sin(min(1.0, t * 1.1) * math.pi * 0.5)
            w = n_px * (0.008 + (wmax - 0.008) * belly ** 1.3)
            if t > 0.88:
                w *= 1.0 - (t - 0.88) * 3.0
            d.ellipse((x - w, y - w, x + w, y + w), fill=col)
        # head: diamond aligned with the final tangent
        (x1, y1, _), (x0, y0, _) = pts[-1], pts[-8]
        ang = math.atan2(y1 - y0, x1 - x0)
        hl, hw = n_px * 0.09, n_px * 0.052
        fwd = (math.cos(ang), math.sin(ang))
        side = (-fwd[1], fwd[0])
        cx_, cy_ = x1 + fwd[0] * hl * 0.2, y1 + fwd[1] * hl * 0.2
        head = [
            (cx_ + fwd[0] * hl, cy_ + fwd[1] * hl),
            (cx_ + side[0] * hw, cy_ + side[1] * hw),
            (cx_ - fwd[0] * hl * 0.9, cy_ - fwd[1] * hl * 0.9),
            (cx_ - side[0] * hw, cy_ - side[1] * hw),
        ]
        d.polygon(head, fill=col)
        e = n_px * 0.011
        for s in (-1, 1):
            ex, ey = cx_ + side[0] * hw * 0.4 * s, cy_ + side[1] * hw * 0.4 * s
            d.ellipse((ex - e, ey - e, ex + e, ey + e), fill=(8, 14, 10, 255))
    img = img.resize((px, px), Image.LANCZOS)
    if glow and px >= 24:
        g = img.filter(ImageFilter.GaussianBlur(px * 0.045))
        base = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        base = Image.alpha_composite(base, g)
        base = Image.alpha_composite(base, g)
        img = Image.alpha_composite(base, img)
    return img


def rounded_poly(x0, y0, x1, y1, r, chamfer=0.0, steps=8):
    """Rounded rectangle polygon with an optional chamfered bottom-right corner."""
    pts = []

    def arc(cx, cy, a0):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    arc(x0 + r, y0 + r, 180)  # top-left
    arc(x1 - r, y0 + r, 270)  # top-right
    if chamfer:
        pts.append((x1, y1 - chamfer))
        pts.append((x1 - chamfer, y1))
    else:
        arc(x1 - r, y1 - r, 0)
    arc(x0 + r, y1 - r, 90)  # bottom-left
    return pts


def radial_glow(base: Image.Image, cx, cy, radius, color, strength):
    g = Image.radial_gradient("L").resize((int(radius * 2), int(radius * 2)), Image.BICUBIC)
    # radial_gradient: 0 at the centre, ~181 at the edge mid-points -> fade to 0 at the edge
    g = g.point(lambda v: int(((max(0.0, 255 - v * 1.4143) / 255.0) ** 1.4) * 255 * strength))
    layer = Image.new("RGBA", g.size, color + (255,))
    layer.putalpha(g)
    base.paste(layer, (int(cx - radius), int(cy - radius)), layer)


def glow_composite(base: Image.Image, glow: Image.Image, sharp: Image.Image, radius, passes=2):
    g = glow.filter(ImageFilter.GaussianBlur(radius))
    for _ in range(passes):
        base.alpha_composite(g)
    base.alpha_composite(sharp)


def grad_arc(d, cx, cy, r, wd, start, sweep, c0, c1, step=2.0, caps=True, alpha=255):
    """Gradient arc whose centre-line radius is r, drawn clockwise from `start` deg."""
    box = (cx - r - wd / 2, cy - r - wd / 2, cx + r + wd / 2, cy + r + wd / 2)
    n = max(1, int(abs(sweep) / step))
    for i in range(n):
        a, b = start + sweep * i / n, start + sweep * (i + 1) / n
        col = lerp(c0, c1, (i + 0.5) / n) + (alpha,)
        d.arc(box, a, b + 0.9, fill=col, width=max(1, int(wd)))
    if caps:
        for ang, col in ((start, c0), (start + sweep, c1)):
            x = cx + r * math.cos(math.radians(ang))
            y = cy + r * math.sin(math.radians(ang))
            d.ellipse((x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2), fill=col + (alpha,))


# =============================================================================== #
#  Application
# =============================================================================== #
class Pair:
    """Two differently coloured text pieces centred as one string."""

    def __init__(self, cv, cx, y, font, fill1, fill2, gap=0):
        self.cv, self.cx, self.y, self.font, self.gap = cv, cx, y, font, gap
        self.a = cv.create_text(cx, y, text="", font=font, fill=fill1, anchor="w")
        self.b = cv.create_text(cx, y, text="", font=font, fill=fill2, anchor="w")

    def set(self, t1, t2):
        if (t1, t2) == getattr(self, "_last", None):
            return
        self._last = (t1, t2)
        wa, wb = self.font.measure(t1), self.font.measure(t2)
        left = self.cx - (wa + self.gap + wb) / 2
        self.cv.itemconfigure(self.a, text=t1)
        self.cv.itemconfigure(self.b, text=t2)
        self.cv.coords(self.a, left, self.y)
        self.cv.coords(self.b, left + wa + self.gap, self.y)


class App:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        # CustomTkinter polls the theme every 30 ms and the DPI every 100 ms by default.
        # BANKO is always dark and sizes itself, so slow those loops right down.
        try:
            from customtkinter.windows.widgets.appearance_mode import AppearanceModeTracker
            from customtkinter.windows.widgets.scaling import ScalingTracker

            AppearanceModeTracker.update_loop_interval = 30_000
            ScalingTracker.update_loop_interval = 10_000
        except Exception:
            pass
        try:
            ctk.set_window_scaling(1.0)
            ctk.set_widget_scaling(1.0)
        except Exception:
            pass
        self.root = ctk.CTk(fg_color=BG_HEX)
        self.root.title(TITLE)
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        self.S = round(max(1.0, dpi / 96.0), 2)
        S = self.S
        self.refs = []  # keep PhotoImages alive
        self.cache: dict = {}
        self.photos: dict = {}
        self._shadow: dict = {}
        self.static: dict = {}
        self._static_lock = threading.Lock()

        # fonts ---------------------------------------------------------------
        fams = set(tkfont.families())
        fam = "Bahnschrift" if "Bahnschrift" in fams else "Segoe UI"
        self.fam = fam

        def F(size, bold=False):
            return tkfont.Font(family=fam, size=-int(size * S), weight="bold" if bold else "normal")

        self.f_title = F(13)
        self.f_brand = F(60, True)
        self.f_card = F(29, True)
        self.f_sub = F(15)
        self.f_big = F(36, True)
        self.f_mid = F(31, True)
        self.f_lbl = F(16)
        self.f_lblb = F(17, True)
        self.f_small = F(14)
        self.f_ctl = F(15, True)
        self.f_btn = F(21, True)
        self.f_foot = F(13)

        # window --------------------------------------------------------------
        self.pw, self.ph = int(W * S), int(H * S)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # bypass CTk's own geometry scaling: we size everything in physical pixels
        tk.Tk.geometry(self.root, f"{self.pw}x{self.ph}+{(sw - self.pw) // 2}+{max(0, (sh - self.ph) // 3)}")
        self.root.resizable(False, False)

        ico_path = os.path.join(tempfile.gettempdir(), "banko_icon.ico")
        try:
            make_emblem(256, glow=False).save(
                ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            )
            self.root.iconbitmap(ico_path)
        except Exception:
            pass

        self.cv = tk.Canvas(self.root, width=self.pw, height=self.ph, bg=BG_HEX, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)

        self._build_static()
        self._build_dynamic()
        for it in self.cv.find_all():
            if self.cv.type(it) == "text":
                self.cv.tag_raise(it)
        self._bind()

        # state ---------------------------------------------------------------
        self.mini_on = False
        self.fps_on = False
        self.mini_win = None
        self.fps_win = None
        self._boosting = False
        self._boost_q: "queue.Queue[str]" = queue.Queue()
        self._status_until = 0.0
        self._last_stamp = 0.0
        self._hover = None
        self._drag = None
        self._last = None

        self.mon = Monitor(1.0)
        self.mon.start()

        self.root.overrideredirect(True)
        self.root.after(80, self._set_appwindow)
        self._round_corners(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(300, self.tick)

    # ------------------------------------------------------------------ utils #
    def px(self, v):
        return int(round(v * self.S))

    def photo(self, img):
        p = ImageTk.PhotoImage(img)
        self.refs.append(p)
        return p

    # --------------------------------------------------------------- statics #
    def _build_static(self):
        S, px = self.S, self.px
        w, h = self.pw, self.ph
        ss = 2
        k = S * ss
        base = Image.new("RGBA", (w, h), BG + (255,))
        radial_glow(base, w - px(100), h - px(30), px(600), GREEN, 0.34)
        radial_glow(base, px(120), px(60), px(420), CYAN, 0.08)
        radial_glow(base, w * 0.5, h * 0.55, px(380), (30, 60, 80), 0.10)

        bd = ImageDraw.Draw(base)
        bd.rectangle((0, 0, w, px(TB_H)), fill=TITLEBAR + (255,))
        bd.line((0, px(TB_H), w, px(TB_H)), fill=(32, 38, 48, 255), width=max(1, px(1)))

        fill = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        sharp = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        glow = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        fd, sd, gd = ImageDraw.Draw(fill), ImageDraw.Draw(sharp), ImageDraw.Draw(glow)

        # cards ---------------------------------------------------------------
        for x0, colr in zip(COLS, (GREEN, CYAN, PURPLE)):
            x1 = x0 + CW
            poly = [(x * k, y * k) for x, y in rounded_poly(x0, CARD_Y0, x1, CARD_Y1, 12, chamfer=30)]
            fd.polygon(poly, fill=PANEL + (238,))
            closed = poly + [poly[0]]
            gd.line(closed, fill=colr + (255,), width=int(5 * k), joint="curve")
            sd.line(closed, fill=colr + (255,), width=int(2.2 * k), joint="curve")
            # corner chamfer ticks
            ch = 30
            for c_off, a in ((11, 235), (19, 150)):
                p1 = (x1 - 5, CARD_Y1 - ch - c_off + 5)
                p2 = (x1 - ch - c_off + 5, CARD_Y1 - 5)
                m = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                q1 = ((p1[0] + m[0]) / 2 + 3, (p1[1] + m[1]) / 2 - 3)
                q2 = ((p2[0] + m[0]) / 2 - 3, (p2[1] + m[1]) / 2 + 3)
                q1 = (m[0] + (p1[0] - m[0]) * 0.8, m[1] + (p1[1] - m[1]) * 0.8)
                q2 = (m[0] + (p2[0] - m[0]) * 0.8, m[1] + (p2[1] - m[1]) * 0.8)
                sd.line([(q1[0] * k, q1[1] * k), (q2[0] * k, q2[1] * k)], fill=colr + (a,), width=int(1.6 * k))

        # control panels ------------------------------------------------------
        for x0 in (COLS[0], COLS[2]):
            poly = [(x * k, y * k) for x, y in rounded_poly(x0, CTRL_Y0, x0 + CW, CTRL_Y1, 11)]
            fd.polygon(poly, fill=PANEL + (240,))
            sd.line(poly + [poly[0]], fill=(52, 62, 78, 255), width=int(1.8 * k), joint="curve")

        # header accent lines + footer divider -------------------------------------
        ly = 156
        steps = 80
        for i in range(steps):
            xa = COLS[0] + (COLS[2] + CW - COLS[0]) * i / steps
            xb = COLS[0] + (COLS[2] + CW - COLS[0]) * (i + 1) / steps
            t = i / (steps - 1)
            a = int(110 * math.sin(t * math.pi) ** 0.6)
            sd.line([(xa * k, ly * k), (xb * k + 1, ly * k)], fill=(70, 86, 104, a), width=int(1.2 * k))
        for xa, xb, colr in ((COLS[0] + 2, COLS[0] + 54, CYAN), (COLS[2] + CW - 54, COLS[2] + CW - 2, GREEN)):
            gd.line([(xa * k, ly * k), (xb * k, ly * k)], fill=colr + (255,), width=int(5 * k))
            sd.line([(xa * k, ly * k), (xb * k, ly * k)], fill=colr + (255,), width=int(3 * k))
        dy = 512
        for i in range(steps):
            xa = 140 + (W - 280) * i / steps
            xb = 140 + (W - 280) * (i + 1) / steps
            t = i / (steps - 1)
            a = int(120 * math.sin(t * math.pi))
            sd.line([(xa * k, dy * k), (xb * k + 1, dy * k)], fill=(0, 200, 130, a), width=int(1.2 * k))

        fill = fill.resize((w, h), Image.LANCZOS)
        sharp = sharp.resize((w, h), Image.LANCZOS)
        glow = glow.resize((w, h), Image.LANCZOS)
        base.alpha_composite(fill)
        glow_composite(base, glow, sharp, 7 * S, passes=2)

        # window border
        ImageDraw.Draw(base).rectangle((0, 0, w - 1, h - 1), outline=(40, 48, 60, 255), width=max(1, px(1)))

        self.bg_pil = base.convert("RGB")
        self.bg_photo = self.photo(self.bg_pil)
        self.cv.create_image(0, 0, image=self.bg_photo, anchor="nw")

        # title bar -------------------------------------------------------------
        self.cv.create_text(px(16), px(TB_H / 2), text=TITLE, font=self.f_title, fill="#C9D1D9", anchor="w")
        self.title_emblem = self.photo(make_emblem(px(28)))
        self.cv.create_image(px(W - 128), px(TB_H / 2), image=self.title_emblem)
        self.tb_min_bg = self.cv.create_rectangle(
            px(W - 92), 1, px(W - 46), px(TB_H), fill=TITLEBAR_HEX, outline="")
        self.tb_close_bg = self.cv.create_rectangle(
            px(W - 46), 1, px(W - 1), px(TB_H), fill=TITLEBAR_HEX, outline="")
        mx, my = px(W - 69), px(TB_H / 2)
        self.cv.create_line(mx - px(6), my + px(3), mx + px(6), my + px(3), fill="#C9D1D9", width=max(1, px(1.4)))
        cx_, cy_ = px(W - 23), px(TB_H / 2)
        for sgn in (1, -1):
            self.cv.create_line(cx_ - px(6), cy_ - px(6) * sgn, cx_ + px(6), cy_ + px(6) * sgn,
                                fill="#C9D1D9", width=max(1, px(1.4)))

        # header ----------------------------------------------------------------
        self.cv.create_text(px(390), px(104), text="BANKO", font=self.f_brand, fill=WHITE)
        self.hero = self.photo(make_emblem(px(118)))
        self.cv.create_image(px(574), px(102), image=self.hero)

        # card titles
        for x0, name in zip(COLS, ("GPU", "CPU", "RAM")):
            self.cv.create_text(px(x0 + CW / 2), px(CARD_Y0 + 40), text=name, font=self.f_card, fill=WHITE)

    # -------------------------------------------------------------- dynamics #
    def _build_dynamic(self):
        S, px, cv = self.S, self.px, self.cv
        cxs = [x0 + CW / 2 for x0 in COLS]
        y0 = CARD_Y0

        # GPU
        self.gpu_name = cv.create_text(px(cxs[0]), px(y0 + 72), text="NVIDIA GeForce RTX 2050",
                                       font=self.f_sub, fill=GRAY)
        def tl(cx, cy, w, h):  # top-left of a (w, h) design-px image centred on (cx, cy)
            return px(cx) - int(w * S) // 2, px(cy) - int(h * S) // 2

        self.gpu_tl = tl(cxs[0], y0 + 193, 250, 200)
        self.cpu_tl = tl(cxs[1], y0 + 188, 220, 220)
        self.ram_tl = tl(cxs[2], y0 + 176, CW - 54 + 28, 56)
        self.gpu_img = cv.create_image(*self.gpu_tl, image="", anchor="nw")
        self.gpu_temp = cv.create_text(px(cxs[0]), px(y0 + 190), text="--°C", font=self.f_big, fill=WHITE)
        self.gpu_load = Pair(cv, px(cxs[0]), px(y0 + 270), self.f_lbl, GRAY, WHITE, gap=px(6))

        # CPU
        self.cpu_name = cv.create_text(px(cxs[1]), px(y0 + 72), text="CPU", font=self.f_sub, fill=GRAY)
        self.cpu_img = cv.create_image(*self.cpu_tl, image="", anchor="nw")
        cv.create_text(px(cxs[1]), px(y0 + 158), text="LOAD:", font=self.f_lbl, fill=GRAY)
        self.cpu_load = cv.create_text(px(cxs[1]), px(y0 + 190), text="--%", font=self.f_mid, fill=WHITE)
        self.cpu_temp = Pair(cv, px(cxs[1]), px(y0 + 224), self.f_small, GRAY, WHITE, gap=px(5))

        # RAM
        self.ram_total = cv.create_text(px(cxs[2]), px(y0 + 72), text="-- GB Total", font=self.f_sub, fill=GRAY)
        self.ram_top = Pair(cv, px(cxs[2]), px(y0 + 140), self.f_lbl, WHITE, GRAY, gap=px(7))
        self.ram_img = cv.create_image(*self.ram_tl, image="", anchor="nw")
        self.ram_bot = Pair(cv, px(cxs[2]), px(y0 + 214), self.f_lbl, WHITE, GRAY, gap=px(7))

        # controls --------------------------------------------------------------
        cy = (CTRL_Y0 + CTRL_Y1) / 2
        self.tog_imgs = {v: self.photo(self._render_toggle(v)) for v in (False, True)}
        self.tog_mini = cv.create_image(px(COLS[0] + 14 + 30), px(cy), image=self.tog_imgs[False])
        cv.create_text(px(COLS[0] + 14 + 60 + 20), px(cy), text="MINI OVERLAY MODE", font=self.f_ctl,
                       fill="#E6EDF3", anchor="w")
        cv.create_text(px(COLS[2] + 24), px(cy), text="FPS OVERLAY", font=self.f_ctl, fill="#E6EDF3", anchor="w")
        self.tog_fps = cv.create_image(px(COLS[2] + CW - 14 - 30), px(cy), image=self.tog_imgs[False])

        self.btn_imgs = {v: self.photo(self._render_button(v)) for v in (False, True)}
        self.btn_img = cv.create_image(px(COLS[1] + CW / 2), px(cy), image=self.btn_imgs[False])
        self.btn_txt = cv.create_text(px(COLS[1] + CW / 2), px(cy), text="BOOST SYSTEM", font=self.f_btn, fill=WHITE)

        # footer ----------------------------------------------------------------
        self.status = cv.create_text(px(W / 2), px(H - 24), text="", font=self.f_foot, fill="#7DF3B0")
        self.fps_text = cv.create_text(px(W - 30), px(H - 24), text="", font=self.f_foot, fill="#7CC8F0",
                                       anchor="e")

    # ----------------------------------------------------- dynamic renderers #
    def _render_toggle(self, on: bool) -> Image.Image:
        S = self.S
        ss = 4
        k = S * ss
        pad = 8
        w, h = 60 + pad * 2, 32 + pad * 2
        sharp = Image.new("RGBA", (int(w * k), int(h * k)), (0, 0, 0, 0))
        glow = Image.new("RGBA", sharp.size, (0, 0, 0, 0))
        d, g = ImageDraw.Draw(sharp), ImageDraw.Draw(glow)
        tr = (14, 64, 70) if on else (28, 34, 44)
        ol = (0, 200, 190) if on else (62, 72, 90)
        box = (pad * k, pad * k, (pad + 60) * k, (pad + 32) * k)
        d.rounded_rectangle(box, radius=16 * k, fill=tr + (255,), outline=ol + (255,), width=int(1.8 * k))
        kx = pad + (60 - 17) if on else pad + 17
        kc = (0, 255, 175) if on else (88, 99, 120)
        r = 11.5 * k
        cx_, cy_ = kx * k, (pad + 16) * k
        if on:
            g.ellipse((cx_ - r, cy_ - r, cx_ + r, cy_ + r), fill=kc + (255,))
            g.rounded_rectangle(box, radius=16 * k, outline=ol + (255,), width=int(3 * k))
        d.ellipse((cx_ - r, cy_ - r, cx_ + r, cy_ + r), fill=kc + (255,))
        out = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
        sharp = sharp.resize(out.size, Image.LANCZOS)
        glow = glow.resize(out.size, Image.LANCZOS)
        if on:
            glow_composite(out, glow, sharp, 4 * S, passes=1)
        else:
            out.alpha_composite(sharp)
        return out

    def _render_button(self, hover: bool) -> Image.Image:
        S = self.S
        ss = 3
        k = S * ss
        pad = 26
        bw, bh = CW, CTRL_Y1 - CTRL_Y0
        w, h = bw + pad * 2, bh + pad * 2
        size = (int(w * k), int(h * k))
        fill = Image.new("RGBA", size, (0, 0, 0, 0))
        sharp = Image.new("RGBA", size, (0, 0, 0, 0))
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        box = (pad * k, pad * k, (pad + bw) * k, (pad + bh) * k)
        # vertical gradient body
        grad = Image.new("RGBA", size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        top, bot = ((16, 86, 48), (8, 44, 26)) if hover else ((10, 62, 36), (6, 32, 20))
        y0, y1 = int(box[1]), int(box[3])
        for y in range(y0, y1):
            gd.line([(box[0], y), (box[2], y)], fill=lerp(top, bot, (y - y0) / max(1, y1 - y0)) + (255,))
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=11 * k, fill=255)
        fill.paste(grad, (0, 0), mask)
        ImageDraw.Draw(sharp).rounded_rectangle(box, radius=11 * k, outline=GREEN + (255,), width=int(2.6 * k))
        ImageDraw.Draw(glow).rounded_rectangle(box, radius=11 * k, outline=GREEN + (255,),
                                               width=int((8 if hover else 6) * k))
        out_size = (int(w * S), int(h * S))
        out = Image.new("RGBA", out_size, (0, 0, 0, 0))
        out.alpha_composite(fill.resize(out_size, Image.LANCZOS))
        glow_composite(out, glow.resize(out_size, Image.LANCZOS), sharp.resize(out_size, Image.LANCZOS),
                       (11 if hover else 8) * S, passes=3 if hover else 2)
        return out

    # ---- gauge frames: Pillow only, so they can be pre-rendered on a worker thread ---- #
    @staticmethod
    def _conic(size, cx, cy, rad, start, sweep, c0, c1):
        """Angular colour gradient (drawn once); per-value frames just mask it."""
        g = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(g)
        box = (cx - rad, cy - rad, cx + rad, cy + rad)
        n = max(2, int(abs(sweep)))
        # padding so the rounded end-caps (which poke past the wedge) are coloured too
        d.pieslice(box, start - 14, start + 1.5, fill=c0 + (255,))
        d.pieslice(box, start + sweep - 1.5, start + sweep + 14, fill=c1 + (255,))
        for i in range(n):
            a = start + sweep * i / n
            d.pieslice(box, a, a + sweep / n + 1.2, fill=lerp(c0, c1, (i + 0.5) / n) + (255,))
        return g

    def _gpu_static(self):
        with self._static_lock:
            if "gpu" in self.static:
                return self.static["gpu"]
            S = self.S
            ss = 3
            k = S * ss
            w, h = 250, 200
            size = (int(w * S), int(h * S))
            cx, cy, r, wd = 125, 105, 82, 15
            start, sweep = 150, 240
            tr = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
            td = ImageDraw.Draw(tr)
            grad_arc(td, cx * k, cy * k, r * k, wd * k, start, sweep, (36, 54, 58), (36, 54, 58))
            ri = r - wd / 2 - 15
            grad_arc(td, cx * k, cy * k, ri * k, 2 * k, start + 6, sweep - 12, (80, 98, 102), (80, 98, 102),
                     caps=False, step=3)
            track = tr.resize(size, Image.LANCZOS)
            conic = self._conic(size, cx * S, cy * S, (r + wd / 2 + 18) * S, start, sweep, GREEN, CYAN)
            st = dict(size=size, track=track, conic=conic, cx=cx, cy=cy, r=r, wd=wd, start=start, sweep=sweep, k=k)
            self.static["gpu"] = st
            return st

    def _gpu_frame(self, pct: int) -> Image.Image:
        st = self._gpu_static()
        S, k, size = self.S, st["k"], st["size"]
        cx, cy, r, wd, start, sweep = (st[n] for n in ("cx", "cy", "r", "wd", "start", "sweep"))
        frac = pct / 100.0
        out = st["track"].copy()
        if frac > 0.004:
            mask = Image.new("L", (size[0] * 3, size[1] * 3), 0)
            dm = ImageDraw.Draw(mask)
            ro, ri = (r + wd / 2) * k, (r - wd / 2) * k
            c3x, c3y = cx * k, cy * k
            end = start + sweep * frac
            dm.pieslice((c3x - ro, c3y - ro, c3x + ro, c3y + ro), start, end, fill=255)
            dm.ellipse((c3x - ri, c3y - ri, c3x + ri, c3y + ri), fill=0)
            for ang in (start, end):
                x = c3x + r * k * math.cos(math.radians(ang))
                y = c3y + r * k * math.sin(math.radians(ang))
                dm.ellipse((x - wd / 2 * k, y - wd / 2 * k, x + wd / 2 * k, y + wd / 2 * k), fill=255)
            a = math.radians(end)
            dm.line([(c3x + (r - wd / 2 - 3) * k * math.cos(a), c3y + (r - wd / 2 - 3) * k * math.sin(a)),
                     (c3x + (r + wd / 2 + 13) * k * math.cos(a), c3y + (r + wd / 2 + 13) * k * math.sin(a))],
                    fill=255, width=int(3 * k))
            mask = mask.resize(size, Image.LANCZOS)
            prog = Image.new("RGBA", size, (0, 0, 0, 0))
            prog.paste(st["conic"], (0, 0), mask)
            glow = prog.filter(ImageFilter.GaussianBlur(6 * S))
            out.alpha_composite(glow)
            out.alpha_composite(glow)
            out.alpha_composite(prog)
        return out

    def _cpu_static(self):
        with self._static_lock:
            if "cpu" in self.static:
                return self.static["cpu"]
            S = self.S
            ss = 3
            k = S * ss
            w = h = 220
            size = (int(w * S), int(h * S))
            c, r, wd = 110, 85, 15
            tr = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
            td = ImageDraw.Draw(tr)
            ro = (r + wd / 2) * k
            td.ellipse((c * k - ro, c * k - ro, c * k + ro, c * k + ro), outline=(36, 54, 58, 255), width=int(wd * k))
            ri = (r - wd / 2 - 13) * k
            ring = Image.new("RGBA", tr.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((c * k - ri, c * k - ri, c * k + ri, c * k + ri),
                                         outline=CYAN + (255,), width=int(2 * k))
            ring = ring.resize(size, Image.LANCZOS)
            track = tr.resize(size, Image.LANCZOS)
            track.alpha_composite(ring.filter(ImageFilter.GaussianBlur(4 * S)))
            track.alpha_composite(ring)
            conic = self._conic(size, c * S, c * S, (r + wd / 2 + 2) * S, -90, 360, (0, 205, 255), (0, 255, 215))
            st = dict(size=size, track=track, conic=conic, c=c, r=r, wd=wd, k=k)
            self.static["cpu"] = st
            return st

    def _cpu_frame(self, pct: int) -> Image.Image:
        st = self._cpu_static()
        S, k, size = self.S, st["k"], st["size"]
        c, r, wd = st["c"], st["r"], st["wd"]
        frac = pct / 100.0
        out = st["track"].copy()
        if frac > 0.004:
            mask = Image.new("L", (size[0] * 3, size[1] * 3), 0)
            dm = ImageDraw.Draw(mask)
            ro, ri = (r + wd / 2) * k, (r - wd / 2) * k
            cc = c * k
            end = -90 + 359.9 * frac
            dm.pieslice((cc - ro, cc - ro, cc + ro, cc + ro), -90, end, fill=255)
            dm.ellipse((cc - ri, cc - ri, cc + ri, cc + ri), fill=0)
            if frac < 0.995:
                for ang in (-90, end):
                    x = cc + r * k * math.cos(math.radians(ang))
                    y = cc + r * k * math.sin(math.radians(ang))
                    dm.ellipse((x - wd / 2 * k, y - wd / 2 * k, x + wd / 2 * k, y + wd / 2 * k), fill=255)
            mask = mask.resize(size, Image.LANCZOS)
            prog = Image.new("RGBA", size, (0, 0, 0, 0))
            prog.paste(st["conic"], (0, 0), mask)
            glow = prog.filter(ImageFilter.GaussianBlur(6 * S))
            out.alpha_composite(glow)
            out.alpha_composite(glow)
            out.alpha_composite(prog)
        return out

    def _flat(self, pil, tl):
        """Pre-composite a frame onto the window background -> opaque RGB.
        Tk then skips alpha blending (and the photo upload is much cheaper)."""
        w, h = pil.size
        bg = self.bg_pil.crop((tl[0], tl[1], tl[0] + w, tl[1] + h)).convert("RGBA")
        bg.alpha_composite(pil)
        return bg.convert("RGB")

    def _photo_for(self, key, builder, arg, tl):
        ph = self.photos.get(key)
        if ph is None:
            ph = ImageTk.PhotoImage(self._flat(builder(arg), tl))
            if len(self.photos) > 400:
                self.photos.clear()
            self.photos[key] = ph
        return ph

    def _gpu_gauge(self, frac: float):
        pct = max(0, min(100, int(round(frac * 100))))
        return self._photo_for(("gpu", pct), self._gpu_frame, pct, self.gpu_tl)

    def _cpu_ring(self, frac: float):
        pct = max(0, min(100, 2 * int(round(frac * 50))))  # 2 % steps: imperceptible, half the renders
        return self._photo_for(("cpu", pct), self._cpu_frame, pct, self.cpu_tl)

    def _ram_bar(self, frac: float) -> ImageTk.PhotoImage:
        key = ("ram", int(round(frac * 200)))
        if key in self.cache:
            return self.cache[key]
        S = self.S
        ss = 3
        k = S * ss
        pad = 14
        bw, bh = CW - 54, 28
        w, h = bw + pad * 2, bh + pad * 2
        size = (int(w * k), int(h * k))
        sharp = Image.new("RGBA", size, (0, 0, 0, 0))
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        box = (pad * k, pad * k, (pad + bw) * k, (pad + bh) * k)
        ImageDraw.Draw(sharp).rounded_rectangle(box, radius=bh / 2 * k, fill=(14, 18, 24, 255),
                                                outline=GREEN + (255,), width=int(2.2 * k))
        ImageDraw.Draw(glow).rounded_rectangle(box, radius=bh / 2 * k, outline=GREEN + (255,), width=int(4 * k))
        # fill
        inset = 5
        fx0, fy0 = (pad + inset) * k, (pad + inset) * k
        fx1_full, fy1 = (pad + bw - inset) * k, (pad + bh - inset) * k
        fw = (fx1_full - fx0) * max(0.0, min(1.0, frac))
        if fw > (fy1 - fy0) * 0.5:
            grad = Image.new("RGBA", size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            x_a, x_b = int(fx0), int(fx0 + fw)
            for x in range(x_a, x_b + 1):
                gd.line([(x, fy0), (x, fy1)], fill=lerp(PURPLE, GREEN, (x - x_a) / max(1, x_b - x_a)) + (255,))
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((fx0, fy0, fx0 + fw, fy1), radius=(fy1 - fy0) / 2, fill=255)
            sharp.paste(grad, (0, 0), mask)
        out_size = (int(w * S), int(h * S))
        out = Image.new("RGBA", out_size, (0, 0, 0, 0))
        glow_composite(out, glow.resize(out_size, Image.LANCZOS), sharp.resize(out_size, Image.LANCZOS),
                       5 * S, passes=2)
        ph = ImageTk.PhotoImage(self._flat(out, self.ram_tl))
        if len(self.cache) > 600:
            self.cache.clear()
        self.cache[key] = ph
        return ph

    # --------------------------------------------------------------- input ---- #
    def _regions(self):
        px = self.px
        return {
            "close": (W - 46, 0, W, TB_H),
            "min": (W - 92, 0, W - 46, TB_H),
            "mini": (COLS[0], CTRL_Y0, COLS[0] + CW, CTRL_Y1),
            "boost": (COLS[1], CTRL_Y0, COLS[1] + CW, CTRL_Y1),
            "fps": (COLS[2], CTRL_Y0, COLS[2] + CW, CTRL_Y1),
        }

    def _hit(self, ev):
        x, y = ev.x / self.S, ev.y / self.S
        for name, (a, b, c, d) in self._regions().items():
            if a <= x <= c and b <= y <= d:
                return name
        return "drag" if y < TB_H else None

    def _bind(self):
        cv = self.cv
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Leave>", lambda e: self._set_hover(None))
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)

    def _set_hover(self, name):
        if name == self._hover:
            return
        self._hover = name
        cv = self.cv
        cv.itemconfigure(self.tb_min_bg, fill="#1B212B" if name == "min" else TITLEBAR_HEX)
        cv.itemconfigure(self.tb_close_bg, fill="#C42B3A" if name == "close" else TITLEBAR_HEX)
        cv.itemconfigure(self.btn_img, image=self.btn_imgs[name == "boost" and not self._boosting])
        cv.configure(cursor="hand2" if name in ("close", "min", "mini", "boost", "fps") else "")

    def _on_motion(self, ev):
        h = self._hit(ev)
        self._set_hover(h if h != "drag" else None)

    def _on_press(self, ev):
        h = self._hit(ev)
        self._pressed = h
        if h == "drag":
            self._drag = (ev.x_root - self.root.winfo_x(), ev.y_root - self.root.winfo_y())

    def _on_drag(self, ev):
        if self._drag:
            tk.Tk.geometry(self.root, f"+{ev.x_root - self._drag[0]}+{ev.y_root - self._drag[1]}")

    def _on_release(self, ev):
        self._drag = None
        h = self._hit(ev)
        if h is None or h != getattr(self, "_pressed", None):
            return
        if h == "close":
            self.quit()
        elif h == "min":
            self.minimize()
        elif h == "mini":
            self.set_mini(not self.mini_on)
        elif h == "fps":
            self.set_fps(not self.fps_on)
        elif h == "boost":
            self.boost()

    # ------------------------------------------------------ window plumbing ---- #
    @staticmethod
    def _hwnd(win):
        return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

    def _set_appwindow(self):
        """Give the borderless window a taskbar button."""
        try:
            u = ctypes.windll.user32
            hwnd = self._hwnd(self.root)
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)
            self.root.withdraw()
            self.root.after(15, self._show_front)
        except Exception:
            pass

    def _show_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    @staticmethod
    def _round_corners(win):
        try:
            win.update_idletasks()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._on_map)

    def _on_map(self, ev):
        if ev.widget is self.root and self.root.state() == "normal":
            self.root.unbind("<Map>")
            self.root.after(20, self._restore_borderless)

    def _restore_borderless(self):
        self.root.overrideredirect(True)
        self._set_appwindow()
        self._round_corners(self.root)

    # ------------------------------------------------------------- overlays ---- #
    def set_mini(self, on: bool):
        self.mini_on = on
        self.cv.itemconfigure(self.tog_mini, image=self.tog_imgs[on])
        if on:
            self._open_mini()
            self.root.withdraw()
        else:
            if self.mini_win:
                self.mini_win.destroy()
                self.mini_win = None
            self.root.deiconify()
            self.root.lift()
            self._last_stamp = 0  # force repaint
        self._last = None

    def _open_mini(self):
        S, px = self.S, self.px
        mw, mh = 410, 54
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.86)
        win.configure(bg=BG_HEX)
        sw = win.winfo_screenwidth()
        win.geometry(f"{px(mw)}x{px(mh)}+{sw - px(mw) - px(16)}+{px(16)}")
        cv = tk.Canvas(win, width=px(mw), height=px(mh), bg=BG_HEX, highlightthickness=0, bd=0)
        cv.pack()

        # background
        ss = 3
        k = S * ss
        bg = Image.new("RGBA", (px(mw), px(mh)), BG + (255,))
        radial_glow(bg, px(mw), px(mh), px(260), GREEN, 0.18)
        lay = Image.new("RGBA", (px(mw) * ss, px(mh) * ss), (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle((1.5 * k, 1.5 * k, (mw - 1.5) * k, (mh - 1.5) * k), radius=10 * k,
                                              outline=GREEN + (255,), width=int(1.8 * k))
        bg.alpha_composite(lay.resize(bg.size, Image.LANCZOS))
        win._bg = ImageTk.PhotoImage(bg)
        cv.create_image(0, 0, image=win._bg, anchor="nw")
        win._em = ImageTk.PhotoImage(make_emblem(px(34)))
        cv.create_image(px(26), px(mh / 2), image=win._em)

        f_l = tkfont.Font(family=self.fam, size=-px(11), weight="bold")
        f_v = tkfont.Font(family=self.fam, size=-px(17), weight="bold")
        items = {}
        for key, x, label, colr in (("gpu", 54, "GPU", hexc(GREEN)), ("cpu", 164, "CPU", hexc(CYAN)),
                                    ("ram", 276, "RAM", hexc(PURPLE))):
            cv.create_text(px(x), px(15), text=label, font=f_l, fill=colr, anchor="w")
            items[key] = cv.create_text(px(x), px(35), text="--", font=f_v, fill=WHITE, anchor="w")
        cv.create_text(px(mw - 22), px(mh / 2), text="⤢", font=tkfont.Font(family="Segoe UI Symbol", size=-px(18)),
                       fill=GRAY)
        win._cv, win._items = cv, items

        state = {}

        def press(e):
            state["d"] = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())
            state["moved"] = False

        def move(e):
            if "d" in state:
                state["moved"] = True
                win.geometry(f"+{e.x_root - state['d'][0]}+{e.y_root - state['d'][1]}")

        def release(e):
            if not state.get("moved") and e.x > px(mw - 48):
                self.set_mini(False)

        cv.bind("<ButtonPress-1>", press)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", release)
        cv.bind("<Double-Button-1>", lambda e: self.set_mini(False))
        win.update_idletasks()
        self._round_corners(win)
        self.mini_win = win

    def set_fps(self, on: bool):
        self.fps_on = on
        self.mon.sample_fps = on
        self.cv.itemconfigure(self.tog_fps, image=self.tog_imgs[on])
        if on:
            self._open_fps()
        else:
            if self.fps_win:
                self.fps_win.destroy()
                self.fps_win = None
            self.cv.itemconfigure(self.fps_text, text="")

    def _open_fps(self):
        px = self.px
        key = "#010203"
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=key)
        win.attributes("-transparentcolor", key)
        font = tkfont.Font(family=self.fam, size=-px(30), weight="bold")
        cv = tk.Canvas(win, width=px(150), height=px(46), bg=key, highlightthickness=0, bd=0)
        cv.pack()
        t_shadow = cv.create_text(px(10) + 2, px(23) + 2, text="FPS --", font=font, fill="#000000", anchor="w")
        t = cv.create_text(px(10), px(23), text="FPS --", font=font, fill=hexc(GREEN), anchor="w")
        win._t, win._ts, win._cv = t, t_shadow, cv
        win.geometry(f"{px(150)}x{px(46)}+{px(16)}+{px(12)}")
        win.update_idletasks()
        try:  # click-through, no focus stealing, hidden from alt-tab
            u = ctypes.windll.user32
            hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass
        self.fps_win = win

    # --------------------------------------------------------------- boost ---- #
    def boost(self):
        if self._boosting:
            return
        self._boosting = True
        self.cv.itemconfigure(self.btn_txt, text="BOOSTING...")
        self.cv.itemconfigure(self.btn_img, image=self.btn_imgs[False])
        self.cv.itemconfigure(self.status, text="Optimizing memory & power plan...", fill="#7DF3B0")

        def work():
            try:
                msg = boost_system()
            except Exception as exc:  # pragma: no cover
                msg = f"Boost failed: {exc}"
            self._boost_q.put(msg)

        threading.Thread(target=work, daemon=True, name="banko-boost").start()

    # ---------------------------------------------------------------- loop ---- #
    def tick(self):
        try:
            self._tick()
        finally:
            self.root.after(500, self.tick)

    def _tick(self):
        try:
            msg = self._boost_q.get_nowait()
            self._boosting = False
            self.cv.itemconfigure(self.btn_txt, text="BOOST SYSTEM")
            self.cv.itemconfigure(self.status, text="✔ " + msg, fill="#7DF3B0")
            self._status_until = time.time() + 12
        except queue.Empty:
            pass
        if self._status_until and time.time() > self._status_until:
            self.cv.itemconfigure(self.status, text="")
            self._status_until = 0

        snap = self.mon.get()
        if snap.stamp == self._last_stamp:
            return
        self._last_stamp = snap.stamp
        self.apply(snap)

    def apply(self, s):
        cv = self.cv
        gt = "--" if s.gpu_temp is None else f"{s.gpu_temp:.0f}"
        gl = "--" if s.gpu_load is None else f"{s.gpu_load:.0f}"
        ct = "N/A" if s.cpu_temp is None else f"{s.cpu_temp:.0f}°C"
        cl = f"{s.cpu_load:.0f}"
        total = s.ram_total_gb
        ram_pct = s.ram_percent

        if self.mini_win:
            m = self.mini_win
            m._cv.itemconfigure(m._items["gpu"], text=f"{gt}°C · {gl}%")
            m._cv.itemconfigure(m._items["cpu"], text=f"{cl}%" + ("" if s.cpu_temp is None else f" · {ct}"))
            m._cv.itemconfigure(m._items["ram"], text=f"{ram_pct:.0f}%")
        if self.fps_win:
            txt = "FPS --" if s.fps is None else f"FPS {s.fps:.0f}"
            self.fps_win._cv.itemconfigure(self.fps_win._t, text=txt)
            self.fps_win._cv.itemconfigure(self.fps_win._ts, text=txt)
            cv.itemconfigure(self.fps_text, text=txt.replace("FPS ", "FPS: "))
        if self.mini_on:
            return  # main window hidden; skip the expensive repaint

        put = self._put
        put(self.gpu_name, text=s.gpu_name)
        put(self.cpu_name, text=s.cpu_name)
        put(self.gpu_temp, text=f"{gt}°C")
        self.gpu_load.set("LOAD:", f"{gl}%")
        put(self.gpu_img, image=self._gpu_gauge((s.gpu_temp or 0) / 100.0))

        put(self.cpu_load, text=f"{cl}%")
        self.cpu_temp.set("TEMP:", ct)
        put(self.cpu_img, image=self._cpu_ring(s.cpu_load / 100.0))

        put(self.ram_total, text=f"{round(total):d}GB Total")
        self.ram_top.set(f"{s.ram_used_gb:.1f} GB", f"/ {total:.1f} GB")
        self.ram_bot.set(f"{ram_pct:.0f}% used", f"· {total - s.ram_used_gb:.1f} GB free")
        put(self.ram_img, image=self._ram_bar(ram_pct / 100.0))

    def _put(self, item, **kw):
        """itemconfigure only when something actually changed (avoids needless repaints)."""
        last = self._shadow.setdefault(item, {})
        changed = {k: v for k, v in kw.items() if last.get(k) is not v and last.get(k) != v}
        if changed:
            last.update(changed)
            self.cv.itemconfigure(item, **changed)

    # ------------------------------------------------------------ lifecycle ---- #
    def quit(self):
        try:
            self.mon.stop()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform != "win32":
        print("BANKO targets Windows.")
    App().run()


if __name__ == "__main__":
    main()
