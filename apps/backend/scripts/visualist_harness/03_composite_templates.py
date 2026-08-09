"""Test harness — Step 3.

Takes the 3 images generated in step 2 + the overlay_copy from step 1 +
a sample logo + primary brand color, and composites ALL 5 PwC-style
templates on each image.

Output: ./out/composites/T{1-5}_img{1-3}.png = 15 final preview variants.

Usage:
    python scripts/visualist_harness/03_composite_templates.py
"""
from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "out"
COMPS = OUT / "composites"
COMPS.mkdir(exist_ok=True)

CANVAS = 1024  # output is 1024x1024

# -----------------------------------------------------------------------------
# Font resolution — Roboto (bundled) for sans, Georgia/Times (system) for serif
# -----------------------------------------------------------------------------
BACKEND_DIR = HERE.parents[1]
FONT_DIR = BACKEND_DIR / "static" / "fonts"

_SANS_BOLD_CANDIDATES = [
    FONT_DIR / "Roboto-Bold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
_SANS_REG_CANDIDATES = [
    FONT_DIR / "Roboto-Regular.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
_SERIF_BOLD_CANDIDATES = [
    Path("C:/Windows/Fonts/georgiab.ttf"),
    Path("C:/Windows/Fonts/timesbd.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
    FONT_DIR / "Roboto-Bold.ttf",  # final fallback — still bold but sans
]


def _font(kind: str, size: int) -> ImageFont.ImageFont:
    choices = {
        "sans_bold": _SANS_BOLD_CANDIDATES,
        "sans_reg": _SANS_REG_CANDIDATES,
        "serif_bold": _SERIF_BOLD_CANDIDATES,
    }[kind]
    for p in choices:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


# -----------------------------------------------------------------------------
# Text helpers
# -----------------------------------------------------------------------------

def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        probe = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), probe, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    kind: str,
    max_size: int,
    min_size: int,
    max_w: int,
    max_h: int,
    max_lines: int = 4,
    line_spacing: float = 1.08,
) -> tuple[ImageFont.ImageFont, list[str]]:
    """Shrink font until wrapped text fits in max_w × max_h AND max_lines."""
    size = max_size
    while size >= min_size:
        f = _font(kind, size)
        lines = wrap_lines(draw, text, f, max_w)
        if len(lines) <= max_lines:
            line_h = (f.getbbox("Ag")[3] - f.getbbox("Ag")[1]) * line_spacing
            total = line_h * len(lines)
            if total <= max_h:
                return f, lines
        size -= 2
    f = _font(kind, min_size)
    return f, wrap_lines(draw, text, f, max_w)[:max_lines]


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    x: int,
    y: int,
    color: tuple[int, int, int],
    line_spacing: float = 1.08,
) -> int:
    cursor = y
    line_h = int((font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) * line_spacing)
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=color)
        cursor += line_h
    return cursor


# -----------------------------------------------------------------------------
# Logo helpers
# -----------------------------------------------------------------------------

def load_logo(path: Optional[Path], fit_w: int, fit_h: int) -> Image.Image:
    """Load logo or synthesize a simple wordmark placeholder."""
    if path and path.exists():
        logo = Image.open(path).convert("RGBA")
    else:
        # Simulate a wordmark logo using "pipelyt" for harness testing
        logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
        d = ImageDraw.Draw(logo)
        f = _font("sans_bold", 90)
        d.text((0, 0), "pipelyt", font=f, fill=(20, 20, 30, 255))
    # Scale to fit bounding box preserving aspect
    lw, lh = logo.size
    r = min(fit_w / lw, fit_h / lh)
    return logo.resize((max(1, int(lw * r)), max(1, int(lh * r))), Image.LANCZOS)


# -----------------------------------------------------------------------------
# Helpers for colors
# -----------------------------------------------------------------------------

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


# -----------------------------------------------------------------------------
# Template renderers — all take (bg_img, copy, logo, primary_color) → Image
# -----------------------------------------------------------------------------

def tpl_t1_top_band(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T1 — cream band top ~48% with logo+headline+sub+CTA, photo bottom 52%."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), (245, 241, 235))  # cream #F5F1EB
    band_h = int(CANVAS * 0.48)
    # Crop photo to landscape strip
    pw, ph = bg.size
    strip_h = CANVAS - band_h
    crop_box = (0, (ph - strip_h) // 2, pw, (ph - strip_h) // 2 + strip_h)
    strip = bg.crop(crop_box)
    canvas.paste(strip, (0, band_h))
    d = ImageDraw.Draw(canvas)

    # Logo top-left
    pad = 48
    logo_fit = load_logo(None, 160, 60) if logo is None else logo
    canvas.paste(logo_fit, (pad, pad), logo_fit)

    # Headline serif
    text_top = pad + logo_fit.size[1] + 30
    available_h = band_h - text_top - 140  # leave room for subhead + CTA
    available_w = CANVAS - 2 * pad
    f_head, head_lines = fit_font(
        d, copy["headline"], "serif_bold", 68, 36, available_w, available_h, max_lines=3
    )
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (18, 18, 28))

    # Subhead
    y_after += 12
    f_sub, sub_lines = fit_font(
        d, copy["subheading"], "sans_reg", 26, 18, available_w, 80, max_lines=2
    )
    y_after = draw_text_block(d, sub_lines, f_sub, pad, y_after, (70, 70, 80))

    # CTA button (bottom-left of band)
    cta_text = copy["cta"]
    f_cta = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), cta_text, font=f_cta)
    tw_px = tw[2] - tw[0]
    btn_w = tw_px + 80
    btn_h = 52
    btn_x = pad
    btn_y = band_h - btn_h - pad
    primary_rgb = hex_to_rgb(primary_hex)
    d.rounded_rectangle(
        [btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=4, fill=(255, 255, 255), outline=primary_rgb, width=2
    )
    d.text(
        (btn_x + 28, btn_y + (btn_h - (tw[3] - tw[1])) // 2 - 2),
        cta_text,
        font=f_cta,
        fill=(18, 18, 28),
    )
    # arrow
    ax = btn_x + btn_w - 24
    ay = btn_y + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return canvas


def _DEPRECATED_tpl_t2_overlay_left(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """DEPRECATED — covered too much of the photo and hid people's faces.
    Replaced by tpl_t2_vsplit_left (zone-separated)."""
    canvas = bg.resize((CANVAS, CANVAS), Image.LANCZOS).convert("RGB")

    # SOLID near-opaque white panel covering the text zone (approx
    # upper-left 58% width × 55% height). Stronger than a gradient —
    # matches PwC's solid-white text overlay approach.
    panel = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    panel_w = int(CANVAS * 0.60)
    panel_h = int(CANVAS * 0.52)
    # Core opaque zone
    pd.rectangle([0, 0, panel_w, panel_h], fill=(255, 255, 255, 235))
    # Feathered edge to the right (blend into photo)
    for i in range(60):
        alpha = int(235 * (1 - i / 60))
        pd.rectangle([panel_w + i, 0, panel_w + i + 1, panel_h], fill=(255, 255, 255, alpha))
    # Feathered edge at the bottom
    for i in range(50):
        alpha = int(235 * (1 - i / 50))
        pd.rectangle([0, panel_h + i, panel_w + 60, panel_h + i + 1], fill=(255, 255, 255, alpha))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), panel).convert("RGB")
    d = ImageDraw.Draw(canvas)

    pad = 48
    if logo is None:
        logo = load_logo(None, 180, 64)
    canvas.paste(logo, (pad, pad), logo)

    text_top = pad + logo.size[1] + 30
    max_w = int(CANVAS * 0.55)
    f_head, head_lines = fit_font(
        d, copy["headline"], "serif_bold", 62, 34, max_w, 300, max_lines=3
    )
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (18, 18, 28))

    y_after += 14
    f_sub, sub_lines = fit_font(
        d, copy["subheading"], "sans_reg", 22, 16, max_w, 70, max_lines=2
    )
    y_after = draw_text_block(d, sub_lines, f_sub, pad, y_after, (50, 50, 60))

    # Icon + CTA (arrow prefix)
    y_after += 20
    primary_rgb = hex_to_rgb(primary_hex)
    arrow_size = 32
    d.rectangle([pad, y_after, pad + arrow_size, y_after + arrow_size], fill=primary_rgb)
    d.polygon(
        [
            (pad + 8, y_after + 10),
            (pad + 22, y_after + 16),
            (pad + 8, y_after + 22),
        ],
        fill=(255, 255, 255),
    )
    f_cta = _font("sans_bold", 22)
    d.text(
        (pad + arrow_size + 14, y_after + (arrow_size - 24) // 2),
        copy["cta"],
        font=f_cta,
        fill=(18, 18, 28),
    )
    return canvas


def _DEPRECATED_tpl_t3_stripes(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """DEPRECATED — overlay layout hid photo content. Replaced by tpl_t3_vsplit_right."""
    canvas = bg.resize((CANVAS, CANVAS), Image.LANCZOS).convert("RGBA")

    # Solid near-opaque white panel for text legibility
    panel = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    panel_w = int(CANVAS * 0.60)
    panel_h = int(CANVAS * 0.48)
    pd.rectangle([0, 0, panel_w, panel_h], fill=(255, 255, 255, 235))
    for i in range(60):
        alpha = int(235 * (1 - i / 60))
        pd.rectangle([panel_w + i, 0, panel_w + i + 1, panel_h], fill=(255, 255, 255, alpha))
    for i in range(50):
        alpha = int(235 * (1 - i / 50))
        pd.rectangle([0, panel_h + i, panel_w + 60, panel_h + i + 1], fill=(255, 255, 255, alpha))
    canvas = Image.alpha_composite(canvas, panel)

    # Parallelogram stripes in middle-left area (below text, signature element)
    stripes = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stripes)
    primary_rgb = hex_to_rgb(primary_hex)
    # Two stripes
    base_y = int(CANVAS * 0.55)
    for i, (width_frac, offset, alpha) in enumerate([(0.28, 0, 220), (0.34, 30, 220)]):
        w = int(CANVAS * width_frac)
        y0 = base_y + i * 26
        sd.polygon(
            [
                (offset, y0),
                (offset + w, y0),
                (offset + w - 30, y0 + 40),
                (offset - 30, y0 + 40),
            ],
            fill=(*primary_rgb, alpha),
        )
    canvas = Image.alpha_composite(canvas, stripes).convert("RGB")
    d = ImageDraw.Draw(canvas)

    pad = 48
    if logo is None:
        logo = load_logo(None, 180, 64)
    canvas.paste(logo, (pad, pad), logo)

    text_top = pad + logo.size[1] + 30
    max_w = int(CANVAS * 0.55)
    f_head, head_lines = fit_font(
        d, copy["headline"], "serif_bold", 62, 34, max_w, 300, max_lines=3
    )
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (18, 18, 28))

    # Download-icon CTA below headline
    y_after += 30
    # Icon square
    d.rectangle([pad, y_after, pad + 36, y_after + 36], fill=primary_rgb)
    # Down-arrow
    d.polygon(
        [
            (pad + 10, y_after + 10),
            (pad + 26, y_after + 10),
            (pad + 18, y_after + 26),
        ],
        fill=(255, 255, 255),
    )
    f_cta = _font("sans_bold", 22)
    d.text((pad + 50, y_after + 6), copy["cta"], font=f_cta, fill=(18, 18, 28))
    return canvas


def _DEPRECATED_tpl_t4_dark(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """DEPRECATED — dark panel covered too much of the scene. Replaced by tpl_t4_accent_bar."""
    canvas = bg.resize((CANVAS, CANVAS), Image.LANCZOS).convert("RGBA")

    # Strong near-opaque navy panel in upper-left
    panel = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    panel_w = int(CANVAS * 0.62)
    panel_h = int(CANVAS * 0.55)
    pd.rectangle([0, 0, panel_w, panel_h], fill=(10, 15, 25, 240))
    for i in range(70):
        alpha = int(240 * (1 - i / 70))
        pd.rectangle([panel_w + i, 0, panel_w + i + 1, panel_h], fill=(10, 15, 25, alpha))
    for i in range(60):
        alpha = int(240 * (1 - i / 60))
        pd.rectangle([0, panel_h + i, panel_w + 70, panel_h + i + 1], fill=(10, 15, 25, alpha))
    canvas = Image.alpha_composite(canvas, panel).convert("RGB")
    d = ImageDraw.Draw(canvas)

    pad = 48
    if logo is None:
        logo = load_logo(None, 180, 64)
    # Ensure logo has light visibility — white-ink variant simulated
    # (for harness: just place dark logo; real production should swap to white logo when T4)
    canvas.paste(logo, (pad, pad), logo)

    text_top = pad + logo.size[1] + 36
    max_w = int(CANVAS * 0.62)
    f_head, head_lines = fit_font(
        d, copy["headline"], "serif_bold", 66, 36, max_w, 320, max_lines=3
    )
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (255, 255, 255))

    y_after += 14
    f_sub, sub_lines = fit_font(
        d, copy["subheading"], "sans_reg", 22, 16, max_w, 80, max_lines=2
    )
    y_after = draw_text_block(d, sub_lines, f_sub, pad, y_after, (230, 230, 235))
    return canvas


def tpl_t5_bottom_band(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T5 — photo top 62%, cream band bottom 38% with headline + CTA button."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), (245, 241, 235))
    photo_h = int(CANVAS * 0.62)
    # Crop photo to fit the top strip (we have 1:1 photos)
    pw, ph = bg.size
    crop_box = (0, 0, pw, int(ph * 0.78))  # top 78% of photo
    top = bg.crop(crop_box).resize((CANVAS, photo_h), Image.LANCZOS)
    canvas.paste(top, (0, 0))
    d = ImageDraw.Draw(canvas)

    pad = 48
    # Logo top-left (ON photo — could clash; OK for harness, prod would swap to white logo)
    if logo is None:
        logo = load_logo(None, 140, 50)
    canvas.paste(logo, (pad, pad), logo)

    # Headline in bottom band
    text_top = photo_h + 36
    max_w = CANVAS - 2 * pad
    f_head, head_lines = fit_font(
        d, copy["headline"], "serif_bold", 58, 32, max_w, 200, max_lines=2
    )
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (18, 18, 28))

    y_after += 14
    f_sub, sub_lines = fit_font(
        d, copy["subheading"], "sans_reg", 22, 16, max_w, 70, max_lines=2
    )
    y_after = draw_text_block(d, sub_lines, f_sub, pad, y_after, (70, 70, 80))

    # CTA button (bottom-left, white with orange arrow)
    y_after += 24
    cta_text = copy["cta"]
    f_cta = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), cta_text, font=f_cta)
    btn_w = (tw[2] - tw[0]) + 80
    btn_h = 52
    btn_x = pad
    btn_y = y_after
    primary_rgb = hex_to_rgb(primary_hex)
    d.rounded_rectangle(
        [btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=4, fill=(255, 255, 255), outline=primary_rgb, width=2
    )
    d.text((btn_x + 28, btn_y + (btn_h - (tw[3] - tw[1])) // 2 - 2), cta_text, font=f_cta, fill=(18, 18, 28))
    ax = btn_x + btn_w - 24
    ay = btn_y + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return canvas


# -----------------------------------------------------------------------------
# NEW zone-separated templates (replace deprecated overlay templates)
# -----------------------------------------------------------------------------

def _cta_button(d, x, y, text, primary_rgb):
    """Bordered white pill button with primary arrow. Returns (w, h) used."""
    f_cta = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), text, font=f_cta)
    tw_px = tw[2] - tw[0]
    btn_w = tw_px + 80
    btn_h = 52
    d.rounded_rectangle([x, y, x + btn_w, y + btn_h], radius=4, fill=(255, 255, 255), outline=primary_rgb, width=2)
    d.text((x + 28, y + (btn_h - (tw[3] - tw[1])) // 2 - 2), text, font=f_cta, fill=(18, 18, 28))
    ax = x + btn_w - 24
    ay = y + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return btn_w, btn_h


def tpl_t2_vsplit_left(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T2 — vertical split: cream + text on LEFT half, photo on RIGHT half.
    Classic editorial. Text never touches the photo."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), (245, 241, 235))  # cream
    left_w = CANVAS // 2

    # Photo in right half
    pw, ph = bg.size
    # crop a square-ish strip from the photo for the right half
    crop_w = int(ph * (left_w / CANVAS))  # preserve aspect
    crop_w = min(crop_w, pw)
    cx = (pw - crop_w) // 2
    strip = bg.crop((cx, 0, cx + crop_w, ph)).resize((left_w, CANVAS), Image.LANCZOS)
    canvas.paste(strip, (left_w, 0))

    d = ImageDraw.Draw(canvas)
    pad = 44

    # Logo top-left
    if logo is None:
        logo = load_logo(None, 160, 60)
    canvas.paste(logo, (pad, pad), logo)

    # Headline serif
    text_top = pad + logo.size[1] + 36
    max_w = left_w - 2 * pad
    f_head, head_lines = fit_font(d, copy["headline"], "serif_bold", 60, 32, max_w, 360, max_lines=4)
    y_after = draw_text_block(d, head_lines, f_head, pad, text_top, (18, 18, 28))

    y_after += 16
    f_sub, sub_lines = fit_font(d, copy["subheading"], "sans_reg", 22, 16, max_w, 110, max_lines=3)
    y_after = draw_text_block(d, sub_lines, f_sub, pad, y_after, (70, 70, 80))

    # CTA button below
    y_after += 26
    primary_rgb = hex_to_rgb(primary_hex)
    _cta_button(d, pad, y_after, copy["cta"], primary_rgb)
    return canvas


def tpl_t3_vsplit_right(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T3 — vertical split: photo LEFT, cream + text RIGHT. Mirrored T2."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), (245, 241, 235))
    right_start = CANVAS // 2

    pw, ph = bg.size
    crop_w = min(int(ph * (right_start / CANVAS)), pw)
    cx = (pw - crop_w) // 2
    strip = bg.crop((cx, 0, cx + crop_w, ph)).resize((right_start, CANVAS), Image.LANCZOS)
    canvas.paste(strip, (0, 0))

    d = ImageDraw.Draw(canvas)
    pad = 44
    text_x = right_start + pad

    if logo is None:
        logo = load_logo(None, 160, 60)
    canvas.paste(logo, (text_x, pad), logo)

    text_top = pad + logo.size[1] + 36
    max_w = CANVAS - text_x - pad
    f_head, head_lines = fit_font(d, copy["headline"], "serif_bold", 60, 32, max_w, 360, max_lines=4)
    y_after = draw_text_block(d, head_lines, f_head, text_x, text_top, (18, 18, 28))

    y_after += 16
    f_sub, sub_lines = fit_font(d, copy["subheading"], "sans_reg", 22, 16, max_w, 110, max_lines=3)
    y_after = draw_text_block(d, sub_lines, f_sub, text_x, y_after, (70, 70, 80))

    y_after += 26
    primary_rgb = hex_to_rgb(primary_hex)
    _cta_button(d, text_x, y_after, copy["cta"], primary_rgb)
    return canvas


def tpl_t4_accent_bar(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T4 — photo hero (~72%) with primary-color accent bar on LEFT edge
    holding the logo + brand strip, and a cream strip at the BOTTOM holding
    headline + CTA. Text never touches the photo area."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), (245, 241, 235))
    primary_rgb = hex_to_rgb(primary_hex)
    # Wider bar so wordmark logos fit fully without clipping
    bar_w = 130
    photo_h = int(CANVAS * 0.64)

    # Photo in the hero zone (right of bar, top of bottom strip)
    pw, ph = bg.size
    hero_w = CANVAS - bar_w
    crop_ratio = hero_w / photo_h
    crop_h = min(ph, int(pw / crop_ratio))
    crop_w = min(pw, int(crop_h * crop_ratio))
    cx = (pw - crop_w) // 2
    cy = (ph - crop_h) // 2
    hero = bg.crop((cx, cy, cx + crop_w, cy + crop_h)).resize((hero_w, photo_h), Image.LANCZOS)
    canvas.paste(hero, (bar_w, 0))

    d = ImageDraw.Draw(canvas)

    # Left accent bar (full height, primary color)
    d.rectangle([0, 0, bar_w, CANVAS], fill=primary_rgb)

    # Logo at top of accent bar with white chip behind for guaranteed visibility
    # Constrain logo to fit bar width with padding so it never clips
    if logo is None:
        logo = load_logo(None, bar_w - 24, 50)  # fit within bar horizontally
    else:
        # Re-fit provided logo to bar width
        max_logo_w = bar_w - 24
        lw, lh = logo.size
        if lw > max_logo_w:
            r = max_logo_w / lw
            logo = logo.resize((max_logo_w, max(1, int(lh * r))), Image.LANCZOS)
    chip_pad = 8
    chip_w = logo.size[0] + 2 * chip_pad
    chip_h = logo.size[1] + 2 * chip_pad
    chip_x = (bar_w - chip_w) // 2
    chip_y = 28
    d.rounded_rectangle([chip_x, chip_y, chip_x + chip_w, chip_y + chip_h], radius=6, fill=(255, 255, 255))
    canvas.paste(logo, (chip_x + chip_pad, chip_y + chip_pad), logo)

    # Bottom strip: headline + sub + CTA button
    pad = 44
    text_top = photo_h + 32
    max_w = CANVAS - bar_w - 2 * pad
    text_x = bar_w + pad
    f_head, head_lines = fit_font(d, copy["headline"], "serif_bold", 52, 30, max_w, 140, max_lines=2)
    y_after = draw_text_block(d, head_lines, f_head, text_x, text_top, (18, 18, 28))

    y_after += 10
    f_sub, sub_lines = fit_font(d, copy["subheading"], "sans_reg", 20, 15, max_w, 60, max_lines=2)
    y_after = draw_text_block(d, sub_lines, f_sub, text_x, y_after, (70, 70, 80))

    # CTA button aligned to the right bottom corner
    cta_text = copy["cta"]
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), cta_text, font=f_cta)
    btn_w = (tw[2] - tw[0]) + 64
    btn_h = 44
    btn_x = CANVAS - pad - btn_w
    btn_y = CANVAS - pad - btn_h
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=3, fill=primary_rgb)
    d.text((btn_x + 22, btn_y + (btn_h - (tw[3] - tw[1])) // 2 - 2), cta_text, font=f_cta, fill=(255, 255, 255))
    return canvas


# -----------------------------------------------------------------------------
# 10 MORE templates (T6 → T15)
# Every one is ZONE-SEPARATED so text never covers people's faces.
# -----------------------------------------------------------------------------

CREAM = (245, 241, 235)
INK = (18, 18, 28)
MUTED = (70, 70, 80)
DARK_INK = (10, 15, 25)


def tpl_t6_sandwich(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T6 — cream TOP (logo + headline), photo MIDDLE, cream BOTTOM (CTA)."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), CREAM)
    top_h = int(CANVAS * 0.32)
    bot_h = int(CANVAS * 0.18)
    photo_h = CANVAS - top_h - bot_h
    pw, ph = bg.size
    strip = bg.crop((0, (ph - int(pw * photo_h / CANVAS)) // 2, pw, (ph + int(pw * photo_h / CANVAS)) // 2)).resize(
        (CANVAS, photo_h), Image.LANCZOS
    )
    canvas.paste(strip, (0, top_h))
    d = ImageDraw.Draw(canvas)

    pad = 44
    if logo is None:
        logo = load_logo(None, 150, 55)
    canvas.paste(logo, (pad, pad), logo)

    # Headline in top band
    text_top = pad + logo.size[1] + 18
    max_w = CANVAS - 2 * pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 52, 32, max_w, top_h - text_top - 8, max_lines=2)
    draw_text_block(d, lines, f_head, pad, text_top, INK)

    # Bottom band: subhead + CTA
    sub_y = top_h + photo_h + 20
    f_sub, sub_lines = fit_font(d, copy["subheading"], "sans_reg", 20, 15, max_w, 60, max_lines=2)
    draw_text_block(d, sub_lines, f_sub, pad, sub_y, MUTED)
    _cta_button(d, CANVAS - pad - 180, CANVAS - pad - 52, copy["cta"], hex_to_rgb(primary_hex))
    return canvas


def tpl_t7_quote_card(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T7 — Solid brand-color background, huge serif headline, small photo inset top-right."""
    primary_rgb = hex_to_rgb(primary_hex)
    canvas = Image.new("RGB", (CANVAS, CANVAS), primary_rgb)
    d = ImageDraw.Draw(canvas)

    # Photo inset in top-right corner
    inset_w, inset_h = int(CANVAS * 0.36), int(CANVAS * 0.36)
    pw, ph = bg.size
    inset = bg.resize((inset_w, inset_h), Image.LANCZOS)
    ix, iy = CANVAS - inset_w - 48, 48
    # drop shadow
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rectangle([ix + 6, iy + 8, ix + inset_w + 6, iy + inset_h + 8], fill=(0, 0, 0, 100))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(8))).convert("RGB")
    canvas.paste(inset, (ix, iy))
    d = ImageDraw.Draw(canvas)

    pad = 48
    if logo is None:
        logo = load_logo(None, 140, 50)
    # White chip behind logo for contrast
    chip_pad = 10
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=6, fill=(255, 255, 255))
    canvas.paste(logo, (pad + chip_pad, pad + chip_pad), logo)

    # Huge serif headline — bottom half
    headline_y = int(CANVAS * 0.46)
    max_w = CANVAS - 2 * pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 84, 42, max_w, 350, max_lines=4)
    y_after = draw_text_block(d, lines, f_head, pad, headline_y, (255, 255, 255))

    y_after += 16
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 22, 16, max_w, 70, max_lines=2)
    y_after = draw_text_block(d, sub, f_sub, pad, y_after, (250, 245, 235))

    # White CTA button
    y_after += 20
    f_cta = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 72
    btn_h = 50
    d.rounded_rectangle([pad, y_after, pad + btn_w, y_after + btn_h], radius=4, fill=(255, 255, 255))
    d.text((pad + 24, y_after + 14), copy["cta"], font=f_cta, fill=primary_rgb)
    ax = pad + btn_w - 22
    ay = y_after + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return canvas


def tpl_t8_thirds(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T8 — Photo strip (top 38%), cream center strip with headline (24%), photo strip (bottom 38%)."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), CREAM)
    top_h = int(CANVAS * 0.38)
    mid_h = int(CANVAS * 0.24)
    bot_h = CANVAS - top_h - mid_h
    pw, ph = bg.size
    # top strip
    t_crop = bg.crop((0, 0, pw, int(ph * 0.5))).resize((CANVAS, top_h), Image.LANCZOS)
    canvas.paste(t_crop, (0, 0))
    # bottom strip (different part of the image)
    b_crop = bg.crop((0, int(ph * 0.5), pw, ph)).resize((CANVAS, bot_h), Image.LANCZOS)
    canvas.paste(b_crop, (0, top_h + mid_h))
    d = ImageDraw.Draw(canvas)

    pad = 44
    if logo is None:
        logo = load_logo(None, 120, 44)
    # Logo chip at top-left of top photo (white chip for visibility)
    chip_pad = 8
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=5, fill=(255, 255, 255, 230))
    canvas.paste(logo, (pad + chip_pad, pad + chip_pad), logo)

    # Headline in the center strip
    text_top = top_h + 24
    max_w = CANVAS - 2 * pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 44, 28, max_w, mid_h - 48, max_lines=2)
    y_after = draw_text_block(d, lines, f_head, pad, text_top, INK)

    # CTA right side of center strip
    primary_rgb = hex_to_rgb(primary_hex)
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 60
    btn_h = 40
    d.rounded_rectangle(
        [CANVAS - pad - btn_w, top_h + (mid_h - btn_h) // 2, CANVAS - pad, top_h + (mid_h + btn_h) // 2],
        radius=3,
        fill=primary_rgb,
    )
    d.text(
        (CANVAS - pad - btn_w + 22, top_h + (mid_h - btn_h) // 2 + 10),
        copy["cta"],
        font=f_cta,
        fill=(255, 255, 255),
    )
    return canvas


def tpl_t9_diagonal_band(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T9 — Photo full-bleed. Angled cream band at bottom ~40% with headline + CTA."""
    canvas = bg.resize((CANVAS, CANVAS), Image.LANCZOS).convert("RGB")
    primary_rgb = hex_to_rgb(primary_hex)

    # Angled cream band — top edge slopes down from left to right
    band = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    y_left = int(CANVAS * 0.55)
    y_right = int(CANVAS * 0.62)
    bd.polygon([(0, y_left), (CANVAS, y_right), (CANVAS, CANVAS), (0, CANVAS)], fill=(*CREAM, 245))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), band).convert("RGB")

    # Thin orange accent line along the angle
    d = ImageDraw.Draw(canvas)
    d.line([(0, y_left - 4), (CANVAS, y_right - 4)], fill=primary_rgb, width=3)

    pad = 44
    if logo is None:
        logo = load_logo(None, 140, 50)
    # Logo top-left on the photo (white chip for visibility)
    chip_pad = 8
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=5, fill=(255, 255, 255, 230))
    canvas.paste(logo, (pad + chip_pad, pad + chip_pad), logo)

    # Headline in band
    text_top = y_left + 40
    max_w = CANVAS - 2 * pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 52, 30, max_w, 160, max_lines=2)
    y_after = draw_text_block(d, lines, f_head, pad, text_top, INK)
    y_after += 10
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 20, 15, max_w, 60, max_lines=2)
    y_after = draw_text_block(d, sub, f_sub, pad, y_after, MUTED)
    _cta_button(d, pad, CANVAS - pad - 52, copy["cta"], primary_rgb)
    return canvas


def tpl_t10_corner_card(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T10 — Photo full-bleed, cream rounded card bottom-left with logo + headline + CTA."""
    canvas = bg.resize((CANVAS, CANVAS), Image.LANCZOS).convert("RGB")
    d = ImageDraw.Draw(canvas)

    # Rounded cream card covering bottom-left ~48% × ~55%
    card_w = int(CANVAS * 0.52)
    card_h = int(CANVAS * 0.55)
    card_x = 40
    card_y = CANVAS - card_h - 40
    card = Image.new("RGBA", (card_w + 40, card_h + 40), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    # shadow
    cd.rounded_rectangle([10, 14, card_w + 20, card_h + 24], radius=10, fill=(0, 0, 0, 90))
    card = card.filter(ImageFilter.GaussianBlur(10))
    # paste shadow
    canvas.paste(card, (card_x - 20, card_y - 20), card)
    # actual card
    card2 = Image.new("RGBA", (card_w, card_h), (*CREAM, 250))
    # rounded corners mask
    mask = Image.new("L", (card_w, card_h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, card_w, card_h], radius=14, fill=255)
    canvas.paste(card2, (card_x, card_y), mask)
    d = ImageDraw.Draw(canvas)

    inset = 32
    if logo is None:
        logo = load_logo(None, 130, 46)
    canvas.paste(logo, (card_x + inset, card_y + inset), logo)

    text_top = card_y + inset + logo.size[1] + 18
    max_w = card_w - 2 * inset
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 40, 26, max_w, 200, max_lines=3)
    y_after = draw_text_block(d, lines, f_head, card_x + inset, text_top, INK)
    y_after += 8
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 17, 14, max_w, 50, max_lines=2)
    y_after = draw_text_block(d, sub, f_sub, card_x + inset, y_after, MUTED)
    _cta_button(d, card_x + inset, y_after + 10, copy["cta"], hex_to_rgb(primary_hex))
    return canvas


def tpl_t11_frame(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T11 — Brand-color frame around a centered photo; logo + headline in top frame strip, CTA in bottom strip."""
    primary_rgb = hex_to_rgb(primary_hex)
    canvas = Image.new("RGB", (CANVAS, CANVAS), primary_rgb)
    top_frame_h = 130
    bot_frame_h = 130
    side_frame = 44
    photo_w = CANVAS - 2 * side_frame
    photo_h = CANVAS - top_frame_h - bot_frame_h
    photo = bg.resize((photo_w, photo_h), Image.LANCZOS)
    canvas.paste(photo, (side_frame, top_frame_h))
    d = ImageDraw.Draw(canvas)

    pad = 44
    if logo is None:
        logo = load_logo(None, 130, 44)
    # White chip behind logo for contrast against brand color
    chip_pad = 8
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, (top_frame_h - chip_h) // 2, pad + chip_w, (top_frame_h + chip_h) // 2], radius=5, fill=(255, 255, 255))
    canvas.paste(logo, (pad + chip_pad, (top_frame_h - logo.size[1]) // 2), logo)

    # Headline in top frame strip, right of logo
    head_x = pad + chip_w + 24
    max_w = CANVAS - head_x - pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 32, 22, max_w, top_frame_h - 32, max_lines=2)
    draw_text_block(d, lines, f_head, head_x, 30, (255, 255, 255))

    # Bottom frame: subhead + CTA
    sub_y = CANVAS - bot_frame_h + 28
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 19, 15, int(CANVAS * 0.6), 55, max_lines=2)
    draw_text_block(d, sub, f_sub, pad, sub_y, (255, 255, 255))
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 60
    btn_h = 42
    d.rounded_rectangle(
        [CANVAS - pad - btn_w, CANVAS - bot_frame_h + (bot_frame_h - btn_h) // 2,
         CANVAS - pad, CANVAS - bot_frame_h + (bot_frame_h + btn_h) // 2],
        radius=3, fill=(255, 255, 255),
    )
    d.text(
        (CANVAS - pad - btn_w + 20, CANVAS - bot_frame_h + (bot_frame_h - btn_h) // 2 + 10),
        copy["cta"], font=f_cta, fill=primary_rgb,
    )
    return canvas


def tpl_t12_photo_dominant(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T12 — Photo takes LEFT 72%, narrow cream column on RIGHT 28% with vertical stack of logo + headline + CTA."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), CREAM)
    photo_w = int(CANVAS * 0.72)
    pw, ph = bg.size
    crop_w = min(pw, int(ph * (photo_w / CANVAS)))
    cx = (pw - crop_w) // 2
    strip = bg.crop((cx, 0, cx + crop_w, ph)).resize((photo_w, CANVAS), Image.LANCZOS)
    canvas.paste(strip, (0, 0))
    d = ImageDraw.Draw(canvas)

    pad = 28
    text_x = photo_w + pad
    max_w = CANVAS - text_x - pad

    if logo is None:
        logo = load_logo(None, 110, 40)
    canvas.paste(logo, (text_x, pad + 10), logo)

    text_top = pad + 10 + logo.size[1] + 24
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 32, 22, max_w, 260, max_lines=5)
    y_after = draw_text_block(d, lines, f_head, text_x, text_top, INK)
    y_after += 10
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 16, 13, max_w, 90, max_lines=4)
    y_after = draw_text_block(d, sub, f_sub, text_x, y_after, MUTED)

    # Stacked/compact CTA at bottom of column
    primary_rgb = hex_to_rgb(primary_hex)
    f_cta = _font("sans_bold", 17)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 40
    btn_h = 38
    bx = text_x
    by = CANVAS - pad - btn_h
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h], radius=3, fill=primary_rgb)
    d.text((bx + 14, by + 11), copy["cta"], font=f_cta, fill=(255, 255, 255))
    return canvas


def tpl_t13_eyebrow_bar(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T13 — Dark navy eyebrow bar top (~10%), photo middle (~58%), cream bottom (~32%) with headline + CTA."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), CREAM)
    primary_rgb = hex_to_rgb(primary_hex)
    bar_h = int(CANVAS * 0.10)
    photo_h = int(CANVAS * 0.58)
    bot_h = CANVAS - bar_h - photo_h

    # Top dark bar
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, CANVAS, bar_h], fill=DARK_INK)

    # Photo
    pw, ph = bg.size
    crop_h = min(ph, int(pw * (photo_h / CANVAS)))
    cy = (ph - crop_h) // 2
    mid = bg.crop((0, cy, pw, cy + crop_h)).resize((CANVAS, photo_h), Image.LANCZOS)
    canvas.paste(mid, (0, bar_h))
    d = ImageDraw.Draw(canvas)

    # Logo on dark bar — needs white chip behind for contrast
    pad = 36
    if logo is None:
        logo = load_logo(None, 110, 36)
    chip_pad = 8
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    chip_y = (bar_h - chip_h) // 2
    d.rounded_rectangle([pad, chip_y, pad + chip_w, chip_y + chip_h], radius=5, fill=(255, 255, 255))
    canvas.paste(logo, (pad + chip_pad, chip_y + chip_pad), logo)
    # Orange tick at bar right
    d.rectangle([CANVAS - pad - 6, (bar_h - 22) // 2, CANVAS - pad, (bar_h + 22) // 2], fill=primary_rgb)

    # Bottom cream — headline + subhead + CTA
    text_top = bar_h + photo_h + 20
    max_w = CANVAS - 2 * pad
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 40, 26, max_w, bot_h - 90, max_lines=2)
    y_after = draw_text_block(d, lines, f_head, pad, text_top, INK)
    y_after += 8
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 18, 14, max_w, 50, max_lines=2)
    draw_text_block(d, sub, f_sub, pad, y_after, MUTED)
    _cta_button(d, CANVAS - pad - 180, CANVAS - pad - 46, copy["cta"], primary_rgb)
    return canvas


def tpl_t14_poster(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T14 — Poster style: small square photo top-center, large serif headline below, CTA bottom."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), CREAM)
    d = ImageDraw.Draw(canvas)

    pad = 48
    if logo is None:
        logo = load_logo(None, 140, 48)
    canvas.paste(logo, (pad, pad), logo)

    # Small square photo (approx 42% × 42%) centered in top half
    photo_side = int(CANVAS * 0.46)
    px = (CANVAS - photo_side) // 2
    py = pad + logo.size[1] + 16
    pw, ph = bg.size
    crop = min(pw, ph)
    cx = (pw - crop) // 2
    cy = (ph - crop) // 2
    photo = bg.crop((cx, cy, cx + crop, cy + crop)).resize((photo_side, photo_side), Image.LANCZOS)
    canvas.paste(photo, (px, py))

    # Huge serif headline below photo
    text_top = py + photo_side + 28
    max_w = CANVAS - 2 * pad
    available_h = CANVAS - text_top - 120
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 54, 30, max_w, available_h, max_lines=3)
    y_after = draw_text_block(d, lines, f_head, pad, text_top, INK)
    y_after += 6
    f_sub, sub = fit_font(d, copy["subheading"], "sans_reg", 18, 14, max_w, 50, max_lines=2)
    y_after = draw_text_block(d, sub, f_sub, pad, y_after, MUTED)
    _cta_button(d, pad, CANVAS - pad - 46, copy["cta"], hex_to_rgb(primary_hex))
    return canvas


def tpl_t15_cta_strip(bg: Image.Image, copy: dict, logo: Image.Image, primary_hex: str) -> Image.Image:
    """T15 — Photo fills top ~80%, solid brand-color strip on bottom 20% with headline + CTA in white."""
    primary_rgb = hex_to_rgb(primary_hex)
    canvas = Image.new("RGB", (CANVAS, CANVAS), primary_rgb)
    photo_h = int(CANVAS * 0.80)
    pw, ph = bg.size
    crop_h = min(ph, int(pw * (photo_h / CANVAS)))
    top = bg.crop((0, 0, pw, crop_h)).resize((CANVAS, photo_h), Image.LANCZOS)
    canvas.paste(top, (0, 0))
    d = ImageDraw.Draw(canvas)

    pad = 40
    if logo is None:
        logo = load_logo(None, 130, 44)
    # White chip behind logo on photo
    chip_pad = 8
    chip_w, chip_h = logo.size[0] + 2 * chip_pad, logo.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=5, fill=(255, 255, 255, 230))
    canvas.paste(logo, (pad + chip_pad, pad + chip_pad), logo)

    # Strip content
    strip_y = photo_h
    strip_h = CANVAS - photo_h
    max_w = CANVAS - 2 * pad - 220  # leave room for CTA on right
    f_head, lines = fit_font(d, copy["headline"], "serif_bold", 32, 22, max_w, strip_h - 30, max_lines=2)
    draw_text_block(d, lines, f_head, pad, strip_y + 18, (255, 255, 255))

    # White CTA button on right
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 56
    btn_h = 44
    bx = CANVAS - pad - btn_w
    by = strip_y + (strip_h - btn_h) // 2
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h], radius=3, fill=(255, 255, 255))
    d.text((bx + 20, by + 12), copy["cta"], font=f_cta, fill=primary_rgb)
    ax = bx + btn_w - 20
    ay = by + btn_h // 2
    d.polygon([(ax, ay - 5), (ax + 9, ay), (ax, ay + 5)], fill=primary_rgb)
    return canvas


TEMPLATES = {
    "T1":  tpl_t1_top_band,         # cream band TOP, photo BOTTOM
    "T2":  tpl_t2_vsplit_left,      # cream+text LEFT, photo RIGHT
    "T3":  tpl_t3_vsplit_right,     # photo LEFT, cream+text RIGHT
    "T4":  tpl_t4_accent_bar,       # accent bar + hero photo + bottom strip
    "T5":  tpl_t5_bottom_band,      # photo TOP, cream band BOTTOM
    "T6":  tpl_t6_sandwich,         # cream/photo/cream sandwich
    "T7":  tpl_t11_frame,           # brand-color frame around photo (was T11)
    "T8":  tpl_t12_photo_dominant,  # photo 72% + narrow text column (was T12)
    "T9":  tpl_t13_eyebrow_bar,     # dark top bar + photo + cream bottom (was T13)
    "T10": tpl_t14_poster,          # poster: square photo + huge headline (was T14)
    "T11": tpl_t15_cta_strip,       # photo + solid brand-color CTA strip (was T15)
}


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    data = json.loads((OUT / "visualist_output.json").read_text(encoding="utf-8"))
    copy = data["overlay_copy"]

    # Pull brand colors from visualist output (fed by business DNA).
    # Falls back to legacy hardcode only if JSON doesn't have brand block.
    brand = data.get("brand") or {}
    primary = brand.get("primary_color") or "#FF6B35"
    # (neutral color currently just informs the cream background; not
    # rewired template-by-template in this pass — follow-up work.)
    print(f"  brand.primary_color = {primary}")

    # No user logo available in harness — placeholder wordmark
    logo_for_light = load_logo(None, 160, 60)

    for i in range(1, 4):
        img_path = OUT / f"img_{i}.png"
        if not img_path.exists():
            print(f"  ! missing {img_path.name} - skipping")
            continue
        bg = Image.open(img_path).convert("RGB")

        for name, fn in TEMPLATES.items():
            out_path = COMPS / f"{name}_img{i}.png"
            try:
                composed = fn(bg, copy, logo_for_light, primary)
                composed.save(out_path, "PNG", quality=92)
                print(f"  OK {out_path.relative_to(OUT)}")
            except Exception as e:
                print(f"  FAIL {name}_img{i}: {e}")

    print()
    n = len(TEMPLATES) * 3
    print(f"All {n} composites ({len(TEMPLATES)} templates × 3 images) written to:", COMPS)


if __name__ == "__main__":
    main()
