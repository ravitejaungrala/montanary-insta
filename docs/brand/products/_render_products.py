"""
Render Pipelyt product-tier badges as PNGs for the Stripe product catalog.

Pillow can't read SVG natively, so we re-implement the same design with
Pillow's drawing primitives. Each badge is a 512x512 rounded-square card
with a brand-orange gradient, a tier-specific icon, and the plan label.

Run:
    python _render_products.py
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = Path(__file__).resolve().parent
SIZE = 512


# ---------- shared helpers ----------

def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """Return a square RGBA gradient (top-left → bottom-right diagonal)."""
    img = Image.new("RGB", (size, size), top)
    for y in range(size):
        # diagonal-ish: blend factor uses y for simplicity (we mask to a square,
        # then rotate slightly via composite if needed). The actual colour mix.
        t = y / max(1, size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            # Add a slight diagonal twist so the brighter zone appears top-left.
            tx = x / max(1, size - 1)
            mix = (t * 0.6) + (tx * 0.4)
            r2 = int(top[0] + (bottom[0] - top[0]) * mix)
            g2 = int(top[1] + (bottom[1] - top[1]) * mix)
            b2 = int(top[2] + (bottom[2] - top[2]) * mix)
            img.putpixel((x, y), (r2, g2, b2))
    return img.convert("RGBA")


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def add_corner_glow(card: Image.Image) -> Image.Image:
    """Soft white highlight in the upper-left corner — gives the card depth."""
    glow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = 160, 140, 220
    gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 24))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=24))
    return Image.alpha_composite(card, glow)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    # Try Inter (matches the SVG), then fall back to common Windows fonts,
    # then to Pillow's default if nothing else loads.
    candidates = [
        "C:/Windows/Fonts/Inter-Black.ttf",
        "C:/Windows/Fonts/Inter-Bold.ttf",
        "C:/Windows/Fonts/seguibl.ttf",      # Segoe UI Black
        "C:/Windows/Fonts/segoeuib.ttf",     # Segoe UI Bold
        "C:/Windows/Fonts/arialbd.ttf",      # Arial Bold
    ]
    for f in candidates:
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_label(card: Image.Image, text: str, color=(255, 255, 255, 255)):
    d = ImageDraw.Draw(card)
    f = load_font(56)
    # Measure
    bbox = d.textbbox((0, 0), text, font=f)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (SIZE - w) // 2 - bbox[0]
    y = SIZE - 80 - h // 2
    # subtle drop shadow
    d.text((x + 2, y + 4), text, font=f, fill=(0, 0, 0, 90))
    d.text((x, y), text, font=f, fill=color)


def make_card(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    bg = vertical_gradient(SIZE, top, bottom)
    mask = rounded_mask(SIZE, radius=96)
    card = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    card.paste(bg, (0, 0), mask=mask)
    card = add_corner_glow(card)
    return card


# ---------- tier-specific icons ----------

def draw_starter_bolt(card: Image.Image):
    """Lightning bolt — fast start."""
    d = ImageDraw.Draw(card)
    pts = [(278, 88), (154, 286), (240, 286), (218, 424), (362, 220), (268, 220), (298, 88)]
    # shadow first
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.polygon([(x, y + 6) for (x, y) in pts], fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=8))
    card.alpha_composite(shadow)
    # main bolt
    d = ImageDraw.Draw(card)
    d.polygon(pts, fill=(255, 255, 255, 255))


# ---------- per-tier renderers ----------

TIERS = {
    "starter": {
        "label": "STARTER",
        "top": (0xFF, 0x6A, 0x2D),     # FF6A2D
        "bottom": (0xE6, 0x3E, 0x00),  # E63E00
        "icon": draw_starter_bolt,
    },
}


def render(name: str):
    cfg = TIERS[name]
    card = make_card(cfg["top"], cfg["bottom"])
    cfg["icon"](card)
    draw_label(card, cfg["label"])
    out = OUT / f"pipelyt-{name}.png"
    card.save(out, format="PNG", optimize=True)
    print(f"Wrote {out}")


if __name__ == "__main__":
    for name in TIERS:
        render(name)
