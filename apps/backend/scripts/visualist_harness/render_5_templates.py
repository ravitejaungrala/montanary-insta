"""Render 5 refined Pipelyt social-media templates.

Design rules (per user feedback Apr 2026):
  - PURE WHITE background on every template
  - Product name rendered in PRIMARY color (not ink)
  - No overlapping text; consistent line-spacing; no dead whitespace
  - Background photo: out/img_3.png
  - Logo: apps/product-page/public/favicon.png
  - Copy: Pipelyt, social media management platform, v2 headline/sub/cta

Output: out/pipelyt_T{1..5}.png
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
BACKEND_DIR = HERE.parents[1]
sys.path.append(str(BACKEND_DIR))

from services.image_templates import (  # noqa: E402
    _smart_fit,
    _rounded_mask,
    _load_logo_image,
    _hex_to_rgb,
    _wrap_lines,
)

# --------------------------------------------------------------------------
# Typography — Montserrat Bold (display) + Inter (body). Chains prefer
# bundled TTFs so nothing depends on system fontconfig (works on Lambda).
# --------------------------------------------------------------------------
FONT_DIR = BACKEND_DIR / "static" / "fonts"

_DISPLAY_CHAIN = [          # headline / hero display — punchy geometric sans
    FONT_DIR / "Montserrat-Bold.ttf",
    FONT_DIR / "Inter-Bold.ttf",
    FONT_DIR / "Roboto-Bold.ttf",
]
_SANS_BOLD_CHAIN = [        # product name, tagline, CTA — digital-optimized sans
    FONT_DIR / "Inter-Bold.ttf",
    FONT_DIR / "Montserrat-Bold.ttf",
    FONT_DIR / "Roboto-Bold.ttf",
]
_SANS_REG_CHAIN = [         # subheading body
    FONT_DIR / "Inter-Regular.ttf",
    FONT_DIR / "Montserrat-Regular.ttf",
    FONT_DIR / "Roboto-Regular.ttf",
]


def _font(kind: str, size: int) -> ImageFont.ImageFont:
    chain = {
        "display": _DISPLAY_CHAIN,
        "sans_bold": _SANS_BOLD_CHAIN,
        "sans_reg": _SANS_REG_CHAIN,
    }[kind]
    for p in chain:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _fit_font(draw, text, kind, max_size, min_size, max_w, max_h,
              max_lines=4, spacing=1.22):
    size = max_size
    while size >= min_size:
        f = _font(kind, size)
        lines = _wrap_lines(draw, text, f, max_w)
        if len(lines) <= max_lines:
            line_h = (f.getbbox("Ag")[3] - f.getbbox("Ag")[1]) * spacing
            if line_h * len(lines) <= max_h:
                return f, lines
        size -= 2
    f = _font(kind, min_size)
    return f, _wrap_lines(draw, text, f, max_w)[:max_lines]


def _draw_lines(draw, lines, font, x, y, color, spacing=1.22):
    cursor = y
    line_h = int((font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) * spacing)
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=color)
        cursor += line_h
    return cursor

# --------------------------------------------------------------------------
# Design tokens
# --------------------------------------------------------------------------
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)           # pure black — body, headline, tagline, sub
CANVAS = 1024
# Richer, more saturated orange — pops against pure white.
# Previous #FF6B35 read washed-out; #FF5722 (Material Deep Orange) is clearly visible.
PRIMARY_HEX = "#FF5722"

COPY = {
    "product_name": "Pipelyt",
    "product_tagline": "social media management platform",
    "headline": "Scale Every Channel from One Workspace",
    "subheading": "AI-crafted posts, unified scheduling, and instant cross-platform analytics.",
    "cta": "Start Free",
    # Accent words rendered in PRIMARY (headline only) — rest stay pure black.
    # Subheading is intentionally all pure black (same palette as heading's
    # base words, no separate highlight).
    "highlight_words": ["Scale", "One"],
    # Social-media template extras
    "eyebrow": "NEW",             # T2 pill badge
    "attribution": "— The Pipelyt Team",  # T3 quote attribution
    "insight_label": "INSIGHT",   # T4 eyebrow
    "insight_number": "01",       # T4 big numeral
}


def sharpen(im: Image.Image) -> Image.Image:
    """Apply mild unsharp mask so cropped/resized photos keep their crunch
    against a pure-white canvas. Percent=140 is a sweet spot — more than that
    and JPEG-y halos appear around faces."""
    return im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=140, threshold=2))


def photo_fit(bg: Image.Image, w: int, h: int) -> Image.Image:
    """Face-biased fit + unsharp pass so the result doesn't read as blurry."""
    return sharpen(_smart_fit(bg, w, h))


# --------------------------------------------------------------------------
# Lockup — product name ALWAYS in primary color; tagline in muted.
# --------------------------------------------------------------------------
def render_lockup(logo: Image.Image, name: str, tagline: str,
                  max_w: int, max_h: int, primary_rgb: tuple) -> Image.Image:
    mark_h = max_h
    lw, lh = logo.size
    mark = logo.resize((max(1, int(lw * mark_h / lh)), mark_h), Image.LANCZOS)
    mark_w = mark.size[0]

    name_pt = max(22, min(58, int(max_h * 0.54)))
    tag_pt = max(14, min(28, int(max_h * 0.32)))
    name_f = _font("sans_bold", name_pt)
    # Tagline uses the SAME bold weight as the heading's base (black) text —
    # ensures pure black reads as black, not washed-out grey at small sizes.
    tag_f = _font("sans_bold", tag_pt)

    td = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    n_bbox = td.textbbox((0, 0), name, font=name_f)
    t_bbox = td.textbbox((0, 0), tagline, font=tag_f) if tagline else (0, 0, 0, 0)
    n_asc, n_desc = name_f.getmetrics()
    t_asc, t_desc = tag_f.getmetrics() if tagline else (0, 0)
    name_h = n_asc + n_desc
    tag_h = t_asc + t_desc if tagline else 0
    line_gap = max(4, int(max_h * 0.08)) if tagline else 0
    gap = max(12, int(max_h * 0.18))

    text_w = max(n_bbox[2] - n_bbox[0], t_bbox[2] - t_bbox[0])
    text_h = name_h + (tag_h + line_gap if tagline else 0)

    total_w = mark_w + gap + text_w
    total_h = max(mark_h, text_h)
    if total_w > max_w:
        s = max_w / total_w
        name_pt = max(16, int(name_pt * s))
        tag_pt = max(12, int(tag_pt * s))
        name_f = _font("sans_bold", name_pt)
        tag_f = _font("sans_bold", tag_pt)
        n_asc, n_desc = name_f.getmetrics()
        t_asc, t_desc = tag_f.getmetrics() if tagline else (0, 0)
        name_h = n_asc + n_desc
        tag_h = t_asc + t_desc if tagline else 0
        n_bbox = td.textbbox((0, 0), name, font=name_f)
        t_bbox = td.textbbox((0, 0), tagline, font=tag_f) if tagline else (0, 0, 0, 0)
        text_w = max(n_bbox[2] - n_bbox[0], t_bbox[2] - t_bbox[0])
        text_h = name_h + (tag_h + line_gap if tagline else 0)
        total_w = mark_w + gap + text_w
        total_h = max(mark_h, text_h)

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    mark_y = (total_h - mark_h) // 2
    canvas.paste(mark, (0, max(0, mark_y)), mark)
    d = ImageDraw.Draw(canvas)
    tx = mark_w + gap
    ty = (total_h - text_h) // 2
    # Product NAME in primary color, tagline in pure black
    d.text((tx, ty), name, font=name_f, fill=primary_rgb)
    if tagline:
        d.text((tx, ty + name_h + line_gap), tagline, font=tag_f, fill=BLACK)
    return canvas


def draw_highlighted(d: ImageDraw.ImageDraw, lines: list, font,
                     x: int, y: int, hi_words: list, primary_rgb: tuple,
                     spacing: float = 1.22, base_color: tuple = BLACK) -> int:
    """Draw text line-by-line, rendering any word in `hi_words`
    (case-insensitive, punctuation-stripped) in PRIMARY. Everything else
    uses `base_color` (default pure BLACK; pass WHITE for dark overlays)."""
    hi_set = {w.lower().strip(".,;:!?") for w in (hi_words or [])}
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * spacing)
    cy = y
    for line in lines:
        cx = x
        for word in line.split():
            clean = word.strip(".,;:!?-").lower()
            col = primary_rgb if clean in hi_set else base_color
            d.text((cx, cy), word, font=font, fill=col)
            wb = d.textbbox((0, 0), word + " ", font=font)
            cx += wb[2] - wb[0]
        cy += line_h
    return cy


# --------------------------------------------------------------------------
# CTA helpers
# --------------------------------------------------------------------------
def cta_solid(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
              primary_rgb: tuple, font_pt: int = 20) -> tuple:
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text, font=f)
    w = (tw[2] - tw[0]) + 60
    h = font_pt * 2 + 18
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=primary_rgb)
    asc, desc = f.getmetrics()
    d.text((x + 30, y + (h - (asc + desc)) // 2), text, font=f, fill=WHITE)
    return w, h


def cta_outline(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                primary_rgb: tuple, font_pt: int = 20) -> tuple:
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text, font=f)
    w = (tw[2] - tw[0]) + 72
    h = font_pt * 2 + 18
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                        fill=WHITE, outline=primary_rgb, width=2)
    asc, desc = f.getmetrics()
    d.text((x + 28, y + (h - (asc + desc)) // 2), text, font=f, fill=BLACK)
    ax, ay = x + w - 26, y + h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return w, h


# --------------------------------------------------------------------------
# T1 — Header band top, photo bottom
# --------------------------------------------------------------------------
def t1_header_band(bg, copy, logo, primary_rgb):
    """Text block at top, photo flows directly under — no dead band space."""
    C = CANVAS
    canvas = Image.new("RGB", (C, C), WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 48

    # 1. Logo lockup
    lockup = render_lockup(logo, copy["product_name"], copy["product_tagline"],
                           C - 2 * pad, 56, primary_rgb)
    canvas.paste(lockup, (pad, pad), lockup)

    # 2. Headline
    text_top = pad + lockup.size[1] + 22
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   38, 26, C - 2 * pad, 130,
                                   max_lines=2, spacing=1.16)
    y = draw_highlighted(d, head_lines, f_head, pad, text_top,
                         copy.get("highlight_words"), primary_rgb, spacing=1.16)

    # 3. Subheading
    y += 10
    f_sub, sub_lines = _fit_font(d, copy["subheading"], "sans_reg",
                                 24, 18, C - 2 * pad, 70,
                                 max_lines=2, spacing=1.30)
    y = _draw_lines(d, sub_lines, f_sub, pad, y, BLACK, spacing=1.30)

    # 4. CTA right under sub
    cta_y = y + 14
    cta_w, cta_h = cta_outline(d, pad, cta_y, copy["cta"], primary_rgb)

    # 5. Photo flows DIRECTLY under the CTA — no dead gap — all the way
    #    down to the bottom edge. Rounded top corners so it reads as a
    #    deliberate section break, not just a floating image.
    photo_top = cta_y + cta_h + 24
    photo_h = C - photo_top
    photo = photo_fit(bg, C, photo_h)
    canvas.paste(photo, (0, photo_top))
    return canvas


def _eyebrow_pill(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                  primary_rgb: tuple, font_pt: int = 16) -> tuple:
    """Small solid-primary pill used as eyebrow badge (e.g., 'NEW')."""
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text.upper(), font=f)
    w = (tw[2] - tw[0]) + 36
    h = font_pt * 2 + 10
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=primary_rgb)
    asc, desc = f.getmetrics()
    d.text((x + 18, y + (h - (asc + desc)) // 2), text.upper(), font=f, fill=WHITE)
    return w, h


# --------------------------------------------------------------------------
# T2 — FULL-BLEED BANNER (Instagram / Story style)
# Use case: high-impact announcements, campaigns, hero posts
# Layout: photo fills ENTIRE canvas → dark gradient overlay on bottom half →
#         logo chip top-left → big white headline + sub + solid CTA bottom
# --------------------------------------------------------------------------
def t2_announcement(bg, copy, logo, primary_rgb):
    C = CANVAS
    # Photo fills the entire canvas as the hero background
    canvas = photo_fit(bg, C, C).convert("RGB")

    # Dark-to-transparent gradient overlay on the bottom ~60% so text is
    # legible over any photo. RGBA layer, 0% opacity at the top of the
    # gradient → 82% black at the bottom.
    grad = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    grad_top = int(C * 0.38)
    grad_px = grad.load()
    for y in range(grad_top, C):
        t = (y - grad_top) / max(1, (C - grad_top))
        alpha = int(210 * t)  # ramps 0 → 210
        for x in range(C):
            grad_px[x, y] = (0, 0, 0, alpha)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), grad).convert("RGB")
    d = ImageDraw.Draw(canvas)
    pad = 52

    # 1. Logo MARK only on a small white chip. Product name + tagline render
    #    directly on the photo to the right of the chip (no white background).
    mark_h = 56
    mark_w = max(1, int(logo.size[0] * mark_h / logo.size[1]))
    mark = logo.resize((mark_w, mark_h), Image.LANCZOS)
    chip_pad = 10
    chip_w = mark_w + 2 * chip_pad
    chip_h = mark_h + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h],
                        radius=12, fill=WHITE)
    canvas.paste(mark, (pad + chip_pad, pad + chip_pad), mark)

    # Product name (primary) + tagline (white for readability on photo)
    # rendered beside the chip — NO white background.
    name_pt = 30
    tag_pt = 16
    f_name = _font("sans_bold", name_pt)
    f_tag = _font("sans_bold", tag_pt)
    name_asc, name_desc = f_name.getmetrics()
    tag_asc, tag_desc = f_tag.getmetrics()
    name_line_h = name_asc + name_desc
    tag_line_h = tag_asc + tag_desc
    total_text_h = name_line_h + 4 + tag_line_h
    text_x = pad + chip_w + 16
    text_y = pad + (chip_h - total_text_h) // 2
    d.text((text_x, text_y), copy["product_name"], font=f_name, fill=primary_rgb)
    d.text((text_x, text_y + name_line_h + 4), copy["product_tagline"],
           font=f_tag, fill=WHITE)

    # 2. Top-right: left intentionally EMPTY (no NEW pill per user feedback)

    # 3. Bottom stack: headline (white, large) → sub (larger) → CTA bottom-right
    cta_h = 54
    f_cta = _font("sans_bold", 20)
    twc = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (twc[2] - twc[0]) + 60

    cta_y = C - pad - cta_h
    sub_gap = 18
    head_gap = 16

    # Sub: bumped from 22→28 per user request
    f_sub, sub_lines = _fit_font(d, copy["subheading"], "sans_reg",
                                 28, 20, int(C * 0.78), 110,
                                 max_lines=2, spacing=1.30)
    sub_asc, sub_desc = f_sub.getmetrics()
    sub_line_h = int((sub_asc + sub_desc) * 1.30)
    sub_h = sub_line_h * len(sub_lines)

    # Headline
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   56, 34, C - 2 * pad, 260,
                                   max_lines=3, spacing=1.10)
    h_asc, h_desc = f_head.getmetrics()
    h_line_h = int((h_asc + h_desc) * 1.10)
    head_h = h_line_h * len(head_lines)

    sub_y = cta_y - sub_gap - sub_h
    head_y = sub_y - head_gap - head_h

    draw_highlighted(d, head_lines, f_head, pad, head_y,
                     copy.get("highlight_words"), primary_rgb,
                     spacing=1.10, base_color=WHITE)
    _draw_lines(d, sub_lines, f_sub, pad, sub_y, WHITE, spacing=1.30)

    # CTA bottom-RIGHT (moved per user request)
    cta_solid(d, C - pad - btn_w, cta_y, copy["cta"], primary_rgb)
    return canvas


# --------------------------------------------------------------------------
# T3 — QUOTE / STATEMENT CARD
# Use case: LinkedIn thought leadership, founder quotes, statements
# Layout: logo → big primary quote-mark → pull-quote headline → divider →
#         attribution → small rounded thumbnail bottom-right
# --------------------------------------------------------------------------
def t3_quote_card(bg, copy, logo, primary_rgb):
    """Quote card with the photo pulled UP to fill all remaining canvas —
    no white space below the text block."""
    C = CANVAS
    canvas = Image.new("RGB", (C, C), WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 56

    # 1. Logo lockup top-left
    lockup = render_lockup(logo, copy["product_name"], copy["product_tagline"],
                           int((C - 2 * pad) * 0.65), 52, primary_rgb)
    canvas.paste(lockup, (pad, pad), lockup)

    # 2. Big primary open-quote glyph — anchors the quote block (tighter)
    qf = _font("display", 150)
    q_y = pad + lockup.size[1] + 4
    d.text((pad - 10, q_y), "\u201C", font=qf, fill=primary_rgb)
    quote_bottom = q_y + 105

    # 3. Pull-quote headline — fits directly under the quote mark
    text_top = quote_bottom + 4
    max_w = C - 2 * pad
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   40, 26, max_w, 180,
                                   max_lines=3, spacing=1.16)
    y = draw_highlighted(d, head_lines, f_head, pad, text_top,
                         copy.get("highlight_words"), primary_rgb, spacing=1.16)

    # 4. Divider + attribution directly under headline
    y += 14
    d.rectangle([pad, y, pad + 60, y + 4], fill=primary_rgb)
    y += 14
    f_attr = _font("sans_bold", 20)
    asc, desc = f_attr.getmetrics()
    d.text((pad, y), copy.get("attribution", "— Pipelyt"),
           font=f_attr, fill=BLACK)
    attr_bottom = y + asc + desc

    # 5. Photo fills ALL remaining space — from just below attribution down
    #    to the canvas edge. Eliminates the white gap entirely.
    photo_top = attr_bottom + 24
    photo_h = C - photo_top
    photo = photo_fit(bg, C, photo_h)
    canvas.paste(photo, (0, photo_top))
    return canvas


# --------------------------------------------------------------------------
# T4 — NUMBERED INSIGHT / TIP
# Use case: carousel-style posts, "5 tips for…", educational series
# Layout: logo + "INSIGHT" eyebrow → HUGE primary numeral → headline →
#         sub → photo band at bottom → CTA on photo (solid white pill)
# --------------------------------------------------------------------------
def t4_insight(bg, copy, logo, primary_rgb):
    C = CANVAS
    canvas = Image.new("RGB", (C, C), WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 56

    # 1. Logo lockup top-left
    lockup = render_lockup(logo, copy["product_name"], copy["product_tagline"],
                           int((C - 2 * pad) * 0.65), 52, primary_rgb)
    canvas.paste(lockup, (pad, pad), lockup)

    # 2. "INSIGHT / 01" eyebrow top-right
    eb_label = copy.get("insight_label", "INSIGHT")
    eb_num = copy.get("insight_number", "01")
    f_eb = _font("sans_bold", 16)
    eb_text = f"{eb_label.upper()}  /  {eb_num}"
    tw = d.textbbox((0, 0), eb_text, font=f_eb)
    eb_y = pad + (lockup.size[1] - 16) // 2 - 4
    d.text((C - pad - (tw[2] - tw[0]), eb_y), eb_text,
           font=f_eb, fill=primary_rgb)

    # 3. HUGE primary numeral left + headline/sub right — keep tight
    num_f = _font("display", 240)
    num_bbox = d.textbbox((0, 0), eb_num, font=num_f)
    num_w = num_bbox[2] - num_bbox[0]
    num_asc, num_desc = num_f.getmetrics()
    num_h = num_asc + num_desc
    num_top = pad + lockup.size[1] + 18
    d.text((pad - 8, num_top), eb_num, font=num_f, fill=primary_rgb)

    # 4. Headline + sub beside numeral — vertically centered with it
    text_x = pad + num_w + 16
    text_w = C - text_x - pad
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   32, 22, text_w, 150,
                                   max_lines=3, spacing=1.15)
    f_sub, sub_lines = _fit_font(d, copy["subheading"], "sans_reg",
                                 19, 16, text_w, 80,
                                 max_lines=3, spacing=1.30)
    h_asc, h_desc = f_head.getmetrics()
    h_line_h = int((h_asc + h_desc) * 1.15)
    s_asc, s_desc = f_sub.getmetrics()
    s_line_h = int((s_asc + s_desc) * 1.30)
    text_block_h = h_line_h * len(head_lines) + 12 + s_line_h * len(sub_lines)
    text_top = num_top + max(0, (num_h - text_block_h) // 2) - 20

    y = draw_highlighted(d, head_lines, f_head, text_x, text_top,
                         copy.get("highlight_words"), primary_rgb, spacing=1.15)
    y += 12
    _draw_lines(d, sub_lines, f_sub, text_x, y, BLACK, spacing=1.30)

    # 5. Larger photo band at bottom — 42% of canvas — eliminates dead space
    photo_h = int(C * 0.42)
    photo_top = C - photo_h - pad
    photo = photo_fit(bg, C - 2 * pad, photo_h)
    mask = _rounded_mask(photo.size, 22)
    canvas.paste(photo, (pad, photo_top), mask)

    # 6. Solid primary pill CTA overlayed bottom-right of photo
    f_cta = _font("sans_bold", 19)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 54
    btn_h = 46
    bx = C - pad - btn_w - 18
    by = C - pad - btn_h - 18
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h],
                        radius=btn_h // 2, fill=primary_rgb)
    asc, desc = f_cta.getmetrics()
    d.text((bx + 26, by + (btn_h - (asc + desc)) // 2),
           copy["cta"], font=f_cta, fill=WHITE)
    return canvas


# --------------------------------------------------------------------------
# T5 — Modern brand card: white bg, big headline with primary highlights, photo band
# --------------------------------------------------------------------------
def t5_brand_card(bg, copy, logo, primary_rgb):
    C = CANVAS
    canvas = Image.new("RGB", (C, C), WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 60

    # 1. Header logo + product name
    lockup = render_lockup(logo, copy["product_name"], copy["product_tagline"],
                           C - 2 * pad, 58, primary_rgb)
    canvas.paste(lockup, (pad, pad), lockup)

    # 2. Heading + subheading RIGHT under the lockup
    text_top = pad + lockup.size[1] + 30
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   38, 26, C - 2 * pad, 120,
                                   max_lines=2, spacing=1.14)
    y = draw_highlighted(d, head_lines, f_head, pad, text_top,
                         copy.get("highlight_words"), primary_rgb, spacing=1.14)

    y += 6
    f_sub, sub_lines = _fit_font(d, copy["subheading"], "sans_reg",
                                 22, 17, int((C - 2 * pad) * 0.70), 60,
                                 max_lines=2, spacing=1.30)
    y = _draw_lines(d, sub_lines, f_sub, pad, y, BLACK, spacing=1.30)

    # 3. Photo BETWEEN text and CTA (rounded corners)
    cta_h = 50
    cta_reserve = cta_h + pad + 20
    photo_y = y + 24
    photo_w = C - 2 * pad
    photo_h = C - photo_y - cta_reserve
    photo = photo_fit(bg, photo_w, photo_h)
    mask = _rounded_mask(photo.size, 24)
    canvas.paste(photo, (pad, photo_y), mask)

    # 4. CTA bottom-right (solid primary pill)
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy["cta"], font=f_cta)
    btn_w = (tw[2] - tw[0]) + 60
    cta_solid(d, C - pad - btn_w, C - pad - cta_h, copy["cta"], primary_rgb)
    return canvas


TEMPLATES = [
    ("T1", t1_header_band),       # general post — header + photo below
    ("T2", t2_announcement),      # NEW feature / launch
    ("T3", t3_quote_card),        # pull-quote / thought leadership
    ("T4", t4_insight),           # numbered insight / carousel tip
    ("T5", t5_brand_card),        # general post — header + photo middle + CTA
]


def main() -> None:
    bg_path = HERE / "out" / "img_3.png"
    logo_path = BACKEND_DIR.parent / "product-page" / "public" / "favicon.png"
    if not bg_path.exists():
        raise SystemExit(f"Background not found: {bg_path}")
    if not logo_path.exists():
        raise SystemExit(f"Logo not found: {logo_path}")

    bg = Image.open(bg_path).convert("RGB")
    logo = _load_logo_image(logo_path.read_bytes(), 200, 200)
    primary_rgb = _hex_to_rgb(PRIMARY_HEX)

    print(f"Background : {bg_path.name}")
    print(f"Logo       : {logo_path.name}")
    print(f"Primary    : {PRIMARY_HEX} -> {primary_rgb}")
    print()

    for tid, fn in TEMPLATES:
        img = fn(bg, COPY, logo, primary_rgb)
        out_path = HERE / "out" / f"pipelyt_{tid}.png"
        img.save(out_path, "PNG")
        print(f"  OK  {out_path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
