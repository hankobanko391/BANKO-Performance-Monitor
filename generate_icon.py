"""Generate the flat triple-snake icon for BANKO v3.0.0."""

from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw

BG_OBSIDIAN = (17, 18, 20, 255)  # #111214
BORDER_STEEL = (49, 51, 56, 255)  # #313338
TACTICAL_GREEN = (35, 165, 90)  # #23A55A
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_emblem(px: int, color: tuple[int, int, int] = TACTICAL_GREEN, with_tile: bool = False) -> Image.Image:
    """Render the flat triple-snake BANKO emblem (no neon glow)."""
    ss = 4
    n_px = px * ss
    img = Image.new("RGBA", (n_px, n_px), BG_OBSIDIAN if with_tile else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if with_tile:
        bw = max(1, round(n_px * 0.02))
        d.rectangle([0, 0, n_px - 1, n_px - 1], outline=BORDER_STEEL, width=bw)

    c = n_px / 2.0
    big_r = n_px * (0.37 if with_tile else 0.42)
    steps = 200
    col = color + (255,)
    swirl = 2.0
    wob = 0.05
    freq = 2.5
    wmax = 0.036

    for arm in range(3):
        a0 = arm * 2 * math.pi / 3 - math.pi / 2
        base_pts = []
        for i in range(steps + 1):
            t = i / steps
            rr = big_r * (0.06 + 0.94 * t)
            th = a0 + swirl * t
            base_pts.append((c + rr * math.cos(th), c + rr * math.sin(th)))

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

        for x, y, t in pts:
            belly = math.sin(min(1.0, t * 1.1) * math.pi * 0.5)
            w = n_px * (0.008 + (wmax - 0.008) * belly ** 1.3)
            if t > 0.88:
                w *= 1.0 - (t - 0.88) * 3.0
            d.ellipse((x - w, y - w, x + w, y + w), fill=col)

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
            d.ellipse((ex - e, ey - e, ex + e, ey + e), fill=BG_OBSIDIAN)

    return img.resize((px, px), Image.LANCZOS)


def generate_assets(root_dir: Path) -> Path:
    ico_path = root_dir / "banko_icon.ico"
    master = make_emblem(256, with_tile=True)
    master.save(ico_path, format="ICO", sizes=ICON_SIZES)

    docs_dir = root_dir / "docs"
    if docs_dir.is_dir():
        make_emblem(128, with_tile=False).save(docs_dir / "emblem.png", format="PNG")
    return ico_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    out = generate_assets(root)
    print(f"Generated icon: {out} ({out.stat().st_size} bytes)")
