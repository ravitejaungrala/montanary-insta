"""Pipelyt Image Templates — 11 PwC-editorial-style compositing templates.

Each template takes a generated background photo + overlay copy + logo + brand
primary color and produces a final PIL Image with the overlay composited on top.

All templates are ZONE-SEPARATED — text is never overlaid on people's faces.
Primary color flows from business DNA (per tenant), so each customer's brand
color drives accent bars, CTA buttons, frames, and brand strips.

Entry point:
    compose(bg_bytes, copy, logo_bytes, primary_hex, template_id, canvas_size)
        → PIL.Image

Design lineage: prototyped in scripts/visualist_harness/03_composite_templates.py
and ported here after visual acceptance. See `docs/agent_post_agents.md` §4.
"""
from __future__ import annotations

import logging
import os
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: F401

logger = logging.getLogger("pipelyt.image_templates")

# -----------------------------------------------------------------------------
# Font resolution — Roboto bundled, Georgia/Times system fallback for serif
# -----------------------------------------------------------------------------
_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static",
    "fonts",
)
_SANS_BOLD = [
    # Preferred — Inter Bold, digital-optimized social-media sans (April 2026)
    os.path.join(_FONT_DIR, "Inter-Bold.ttf"),
    # Geometric display alternate — Montserrat
    os.path.join(_FONT_DIR, "Montserrat-Bold.ttf"),
    # Legacy bundled fallback
    os.path.join(_FONT_DIR, "Roboto-Bold.ttf"),
    # Windows (local dev)
    "C:/Windows/Fonts/arialbd.ttf",
    # Debian/Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Amazon Linux 2023 (Lambda base image — Dockerfile installs these)
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_SANS_REG = [
    # Preferred — Inter Regular
    os.path.join(_FONT_DIR, "Inter-Regular.ttf"),
    os.path.join(_FONT_DIR, "Montserrat-Regular.ttf"),
    os.path.join(_FONT_DIR, "Roboto-Regular.ttf"),
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu-sans/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
# Display / headline — punchy geometric sans for social-media posts
_DISPLAY = [
    os.path.join(_FONT_DIR, "Montserrat-Bold.ttf"),
    os.path.join(_FONT_DIR, "Inter-Bold.ttf"),
    os.path.join(_FONT_DIR, "Roboto-Bold.ttf"),
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_SERIF_BOLD = [
    # Bundled in repo — ALWAYS present, no Dockerfile fiddling needed
    os.path.join(_FONT_DIR, "DejaVuSerif-Bold.ttf"),
    # Windows (local dev) — Georgia is closest to editorial serif
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    # Debian/Ubuntu (Linux servers)
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    # Amazon Linux 2 (Lambda base image — Dockerfile's yum install puts here)
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/dejavu-serif/DejaVuSerif-Bold.ttf",
    # Last resort — NOT serif but beats load_default()'s 10pt bitmap
    os.path.join(_FONT_DIR, "Roboto-Bold.ttf"),
]


# ============================================================================
# Cold-start font probe — logs which fonts exist so we can diagnose render
# issues. Only runs once per Lambda container lifetime.
# ============================================================================
def _probe_fonts_on_boot():
    """Log which font paths are reachable. Helps debug prod render issues."""
    available = {"sans_bold": [], "sans_reg": [], "serif_bold": []}
    for p in _SANS_BOLD:
        if os.path.exists(p):
            available["sans_bold"].append(p)
    for p in _SANS_REG:
        if os.path.exists(p):
            available["sans_reg"].append(p)
    for p in _SERIF_BOLD:
        if os.path.exists(p):
            available["serif_bold"].append(p)
    logger.info(f"[fonts] available: {available}")
    # Critical warning — if no SERIF is available, editorial templates look broken
    has_true_serif = any("serif" in p.lower() or "georgia" in p.lower() or "times" in p.lower()
                         for p in available["serif_bold"])
    if not has_true_serif:
        logger.warning(
            "[fonts] ⚠️ No serif font found! Editorial headlines will render "
            "with Roboto-Bold as fallback. Install fonts-liberation or "
            "dejavu-serif-fonts in the Lambda container."
        )


try:
    _probe_fonts_on_boot()
except Exception as _font_probe_err:
    logger.warning(f"[fonts] probe failed: {_font_probe_err}")

DEFAULT_CANVAS = 1024
CREAM = (245, 241, 235)
INK = (18, 18, 28)
MUTED = (70, 70, 80)
DARK_INK = (10, 15, 25)


def _font(kind: str, size: int) -> ImageFont.ImageFont:
    paths = {
        "sans_bold": _SANS_BOLD,
        "sans_reg": _SANS_REG,
        "serif_bold": _SERIF_BOLD,
        "display": _DISPLAY,
    }[kind]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _hex_to_rgb(h: str) -> tuple:
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (255, 107, 53)  # default brand orange fallback


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    words = (text or "").split()
    lines, cur = [], ""
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


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    kind: str,
    max_size: int,
    min_size: int,
    max_w: int,
    max_h: int,
    max_lines: int = 4,
    spacing: float = 1.22,
):
    """Shrink font until text fits in max_w × max_h × max_lines."""
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


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list,
    font,
    x: int,
    y: int,
    color: tuple,
    spacing: float = 1.22,
) -> int:
    cursor = y
    line_h = int((font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) * spacing)
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=color)
        cursor += line_h
    return cursor


def _cta_button(d: ImageDraw.ImageDraw, x: int, y: int, text: str, primary_rgb: tuple) -> tuple:
    """White bordered pill CTA with primary-color arrow. Returns (w, h)."""
    f = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), text, font=f)
    btn_w = (tw[2] - tw[0]) + 80
    btn_h = 52
    d.rounded_rectangle([x, y, x + btn_w, y + btn_h], radius=4, fill=(255, 255, 255), outline=primary_rgb, width=2)
    d.text((x + 28, y + (btn_h - (tw[3] - tw[1])) // 2 - 2), text, font=f, fill=INK)
    ax = x + btn_w - 24
    ay = y + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return btn_w, btn_h


def _smart_fit(bg: Image.Image, target_w: int, target_h: int, vertical_bias: float = 0.38) -> Image.Image:
    """Fit bg into (target_w, target_h) with a vertical bias so faces at
    upper-middle are preserved. Equivalent to ImageOps.fit with centering.

    vertical_bias: 0.0 = top-anchored, 0.5 = center, 1.0 = bottom-anchored.
    Default 0.38 keeps upper-middle (where faces usually sit).
    """
    src_w, src_h = bg.size
    src_ratio = src_w / src_h
    tgt_ratio = target_w / target_h

    if abs(src_ratio - tgt_ratio) < 0.01:
        return bg.resize((target_w, target_h), Image.LANCZOS)

    if src_ratio > tgt_ratio:
        # Source is wider than target — crop width
        new_w = int(src_h * tgt_ratio)
        new_h = src_h
        x = (src_w - new_w) // 2
        y = 0
    else:
        # Source is taller than target — crop height with vertical bias
        new_w = src_w
        new_h = int(src_w / tgt_ratio)
        x = 0
        y = int((src_h - new_h) * vertical_bias)

    cropped = bg.crop((x, y, x + new_w, y + new_h))
    return cropped.resize((target_w, target_h), Image.LANCZOS)


def _strip_logo_background(im: Image.Image, tolerance: int = 42) -> Image.Image:
    """Remove a uniform solid-color background from a logo (black or white
    squares are the common case). Leaves alpha untouched if the logo already
    has meaningful transparency.

    Strategy: color-key removal using a reference color sampled from the four
    corners. If the 4 corners agree on a color (within tolerance), every
    pixel within tolerance of that color gets alpha=0. Pixels fully in-subject
    stay opaque. Pixels near the edge get feathered alpha for smooth blending.

    Returns a new RGBA image. No-op if:
      • Input is not RGBA/RGB
      • Logo already has >5% transparent pixels (already has alpha work done)
      • Corners disagree (logo has a photographic/gradient background we can't key)
    """
    try:
        im = im.convert("RGBA")
    except Exception:
        return im

    px = im.load()
    w, h = im.size
    if w < 8 or h < 8:
        return im

    # If logo already has meaningful alpha channel, skip (assume the designer
    # already delivered a transparent PNG and we shouldn't mess with it).
    try:
        alpha_hist = im.getchannel("A").histogram()
        transparent_count = sum(alpha_hist[:200])  # alpha < 200 considered transparent
        total_pixels = w * h
        if transparent_count / total_pixels > 0.05:
            return im  # already has transparency
    except Exception:
        pass

    # Sample MANY edge points so JPEG noise / slight color drift doesn't fail
    # the "corners agree" heuristic. 5 points along each edge = 20 samples.
    edge_pts = []
    for i in range(5):
        t = i / 4
        x = int(t * (w - 1))
        y = int(t * (h - 1))
        edge_pts.append(px[x, 0])           # top edge
        edge_pts.append(px[x, h - 1])       # bottom edge
        edge_pts.append(px[0, y])           # left edge
        edge_pts.append(px[w - 1, y])       # right edge
    # Use the median-ish reference color (mode of rounded-RGB) to be robust
    from collections import Counter
    rounded = [(p[0] // 16 * 16, p[1] // 16 * 16, p[2] // 16 * 16) for p in edge_pts]
    mode_color, mode_count = Counter(rounded).most_common(1)[0]
    if mode_count < len(edge_pts) * 0.6:
        return im  # edge pixels don't agree → photographic bg, don't key
    ref = mode_color

    # Build new image with color-keyed alpha
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out_px = out.load()
    # Feathered threshold: fully transparent within tolerance, fully opaque
    # beyond 2.5x tolerance, linear feather between.
    inner = tolerance
    outer = int(tolerance * 2.5)
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            dist = abs(r - ref[0]) + abs(g - ref[1]) + abs(b - ref[2])
            if dist <= inner:
                out_px[x, y] = (r, g, b, 0)
            elif dist >= outer:
                out_px[x, y] = (r, g, b, a)
            else:
                # Feather the alpha
                t = (dist - inner) / max(1, outer - inner)
                out_px[x, y] = (r, g, b, int(a * t))
    return out


def _load_logo_image(
    logo_bytes: Optional[bytes], fit_w: int, fit_h: int
) -> Image.Image:
    """Load raw logo mark from bytes (transparent-bg), or placeholder."""
    if logo_bytes:
        try:
            im = Image.open(BytesIO(logo_bytes)).convert("RGBA")
            im = _strip_logo_background(im)
        except Exception as exc:
            logger.warning("Logo load failed: %s — using placeholder", exc)
            im = None
    else:
        im = None
    if im is None:
        # Placeholder: simple circle + letter (no "brand" text — user provides the real name)
        im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([8, 8, 152, 152], fill=(255, 107, 53, 255))
    lw, lh = im.size
    r = min(fit_w / lw, fit_h / lh)
    return im.resize((max(1, int(lw * r)), max(1, int(lh * r))), Image.LANCZOS)


def _render_logo_lockup(
    logo_mark: Image.Image,
    product_name: Optional[str],
    tagline: Optional[str],
    max_w: int,
    max_h: int,
    name_color: tuple = INK,
    tagline_color: tuple = MUTED,
) -> Image.Image:
    """Build a horizontal lockup: [logo_mark | PRODUCT_NAME / tagline].

    Always transparent-backed. Sized so the FINAL lockup fits (max_w, max_h)
    WITHOUT a post-render LANCZOS downscale — previous version rendered at a
    fixed 72px mark + 36pt name + 18pt tagline and then downscaled, which
    was the source of the "text looks blurry in templates" complaint.
    Returns a new RGBA PIL Image.
    """
    name = (product_name or "").strip()
    tag = (tagline or "").strip()

    # No name → just return the logo mark resized to fit.
    if not name:
        lw, lh = logo_mark.size
        r = min(max_w / lw, max_h / lh, 1.0)
        return logo_mark.resize((max(1, int(lw * r)), max(1, int(lh * r))), Image.LANCZOS)

    # ---- 1. Pick a mark height we can actually render at ----
    # The mark takes the full height budget (max_h). The text block will be
    # centered beside it and allowed to flow to a natural height based on the
    # chosen font sizes — we size fonts PROPORTIONALLY to max_h instead of
    # hardcoded 36/18pt so the lockup is crisp across all max_h budgets.
    mark_h = max_h
    lw, lh = logo_mark.size
    mr = mark_h / lh
    mark = logo_mark.resize((max(1, int(lw * mr)), mark_h), Image.LANCZOS)
    mark_w = mark.size[0]

    # Name font = 52% of max_h, tagline = 34% of max_h (user feedback:
    # tagline was too small relative to the product name). Closer to a
    # 1.5:1 ratio reads as "headline + label" which matches the refs.
    name_pt = max(20, min(60, int(max_h * 0.52)))
    tag_pt = max(14, min(32, int(max_h * 0.34)))
    name_font = _font("sans_bold", name_pt)
    tag_font = _font("sans_reg", tag_pt)

    # ---- 2. Measure text ----
    # Use font.getmetrics() → (ascent, descent) for the REAL line height
    # (includes space for descenders like g/p/y). The previous
    # bbox[3]-bbox[1] was just the glyph extent and under-counted by
    # 15-25% — that's why the tagline visually hugged the product name.
    tmp = Image.new("RGBA", (1, 1))
    td = ImageDraw.Draw(tmp)
    name_bbox = td.textbbox((0, 0), name, font=name_font)
    name_text_w = name_bbox[2] - name_bbox[0]
    n_asc, n_desc = name_font.getmetrics()
    name_line_h = n_asc + n_desc
    if tag:
        tag_bbox = td.textbbox((0, 0), tag, font=tag_font)
        tag_text_w = tag_bbox[2] - tag_bbox[0]
        t_asc, t_desc = tag_font.getmetrics()
        tag_line_h = t_asc + t_desc
    else:
        tag_text_w = 0
        tag_line_h = 0

    # Horizontal gap between logo mark and product-name text block.
    # User feedback April 2026: 32% of max_h (~23px @ max_h=72) was too
    # wide — name/tagline felt orphaned from the mark. Tightened to 16%
    # (~12px @ max_h=72) with a 12-px floor. Still enough breathing room
    # to avoid overlap, but reads as a connected lockup.
    gap_mark_text = max(12, int(max_h * 0.16))
    # Vertical gap between the name baseline-bottom and the tagline top.
    # max_h × 0.16 at a 72-px budget gives ~12 px of breathing room.
    line_gap = max(8, int(max_h * 0.16)) if tag else 0
    text_block_w = max(name_text_w, tag_text_w)
    text_block_h = name_line_h + (tag_line_h + line_gap if tag else 0)

    total_w = mark_w + gap_mark_text + text_block_w
    total_h = max(mark_h, text_block_h)

    # ---- 3. If the natural lockup is wider than max_w, shrink fonts and
    # remeasure instead of LANCZOS-downscaling the final raster. Keeps text
    # crisp at the cost of one re-measurement pass.
    if total_w > max_w:
        shrink = max_w / total_w
        name_pt = max(16, int(name_pt * shrink))
        tag_pt = max(11, int(tag_pt * shrink))
        name_font = _font("sans_bold", name_pt)
        tag_font = _font("sans_reg", tag_pt)
        name_bbox = td.textbbox((0, 0), name, font=name_font)
        name_text_w = name_bbox[2] - name_bbox[0]
        n_asc, n_desc = name_font.getmetrics()
        name_line_h = n_asc + n_desc
        if tag:
            tag_bbox = td.textbbox((0, 0), tag, font=tag_font)
            tag_text_w = tag_bbox[2] - tag_bbox[0]
            t_asc, t_desc = tag_font.getmetrics()
            tag_line_h = t_asc + t_desc
        text_block_w = max(name_text_w, tag_text_w)
        text_block_h = name_line_h + (tag_line_h + line_gap if tag else 0)
        total_w = mark_w + gap_mark_text + text_block_w
        total_h = max(mark_h, text_block_h)

    # ---- 4. Compose at exactly the target size — no downscale ----
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))

    # Paste mark (vertically centered)
    mark_y = (total_h - mark_h) // 2
    canvas.paste(mark, (0, max(0, mark_y)), mark)

    # Paste text block (vertically centered). Using name_line_h (ascent +
    # descent) to advance means the tagline starts below the name's
    # descender line, not through the middle of a 'g' or 'y'.
    text_x = mark_w + gap_mark_text
    text_top = (total_h - text_block_h) // 2
    cd = ImageDraw.Draw(canvas)
    cd.text((text_x, text_top), name, font=name_font, fill=name_color)
    if tag:
        cd.text(
            (text_x, text_top + name_line_h + line_gap),
            tag,
            font=tag_font,
            fill=tagline_color,
        )

    return canvas


def _paste_lockup(
    canvas: Image.Image,
    logo: Optional[Image.Image],
    copy: dict,
    x: int,
    y: int,
    max_w: int,
    max_h: int,
    on_dark: bool = False,
) -> tuple:
    """Render the [mark | product_name / tagline] lockup and paste into canvas.

    Returns the size (w, h) of the pasted lockup so callers can position
    things below it. Returns (0, 0) if there's no logo to show.
    """
    if logo is None:
        return (0, 0)
    name = (copy or {}).get("product_name") or ""
    tagline = (copy or {}).get("product_tagline") or ""
    text_color = (255, 255, 255) if on_dark else INK
    tag_color = (235, 235, 240) if on_dark else MUTED
    lockup = _render_logo_lockup(logo, name, tagline, max_w, max_h, text_color, tag_color)
    canvas.paste(lockup, (x, y), lockup)
    return lockup.size


# =============================================================================
# Template renderers — each takes (bg, copy, logo, primary_rgb, C) → PIL.Image
#   bg           : base PIL Image (the Gemini-generated photo)
#   copy         : dict with "headline", "subheading", "cta"
#   logo         : PIL.Image (already alpha-resized) or None
#   primary_rgb  : tuple(r,g,b)
#   C            : canvas size (square)
# =============================================================================


# Helpers unique to the brand_card template (T12). Live here so future
# templates can reuse them.
def _rounded_mask(size: tuple, radius: int) -> Image.Image:
    """Grayscale mask with a rounded rectangle — rounds photo corners."""
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    return m


def _draw_highlighted_lines(d, lines, font, x, y, base_color, hi_color, hi_words, spacing: float = 1.12):
    """Render headline word-by-word so only `hi_words` use `hi_color`. All
    other words use `base_color`. Case-insensitive match; strips punctuation."""
    hi_set = {w.lower() for w in (hi_words or [])}
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * spacing)
    cursor = y
    for line in lines:
        cx = x
        for word in line.split():
            clean = word.strip(".,;:!?").lower()
            col = hi_color if clean in hi_set else base_color
            d.text((cx, cursor), word, font=font, fill=col)
            wbbox = d.textbbox((0, 0), word + " ", font=font)
            cx += wbbox[2] - wbbox[0]
        cursor += line_h
    return cursor


def _cta_pill_white(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                    accent_rgb: tuple = None, font_pt: int = 18) -> tuple:
    """White-fill pill CTA with dark text. No arrow (LinkedIn-ref style).
    `accent_rgb` is accepted for signature parity with other pill helpers
    but unused on this button — pass `None` or the brand color."""
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text, font=f)
    tw_w = tw[2] - tw[0]
    btn_w = tw_w + 56
    btn_h = 44
    d.rounded_rectangle([x, y, x + btn_w, y + btn_h], radius=btn_h // 2, fill=(255, 255, 255))
    d.text((x + 28, y + (btn_h - (tw[3] - tw[1])) // 2 - 2), text, font=f, fill=(10, 20, 40))
    return btn_w, btn_h


def t12_brand_card(bg: Image.Image, copy: dict, logo: Image.Image, primary_rgb: tuple, C: int) -> Image.Image:
    """Brand Card — LinkedIn-style template.

    Layout:
      - Full canvas painted in tenant brand color (primary_rgb).
      - Photo band at the bottom (~48% height), inset horizontally, with
        rounded corners — visually "cuts into" the brand panel.
      - Logo + product name lockup on a white rounded chip top-left.
      - Large sans-bold headline; words listed in copy["highlight_words"]
        render in a lighter shade of the brand color (reference: LinkedIn
        "powered by your network" in softer blue).
      - White-fill pill CTA below the headline, above the photo.

    Prototyped in template_lab.py. Tuned KNOBS ported in as constants.
    """
    pad = 56
    photo_frac = 0.48
    photo_corner_radius = 28
    photo_inset = 56
    chip_radius = 14
    chip_inner_pad = 12
    lockup_max_h = 60
    headline_max_pt = 74
    headline_min_pt = 40
    headline_max_lines = 3
    headline_top_gap = 40
    headline_highlight_lightness = 0.55
    cta_bottom_gap_from_photo = 44

    canvas = Image.new("RGB", (C, C), primary_rgb)
    d = ImageDraw.Draw(canvas)

    # Photo band — rounded corners, inset, sits at bottom.
    photo_h = int(C * photo_frac)
    photo_w = C - 2 * photo_inset
    photo = _smart_fit(bg, photo_w, photo_h)
    px = photo_inset
    py = C - photo_h - pad
    mask = _rounded_mask(photo.size, radius=photo_corner_radius)
    canvas.paste(photo, (px, py), mask)

    # Logo + product name lockup on a white chip top-left.
    lockup_img = _render_logo_lockup(
        logo or _load_logo_image(None, 130, 44),
        copy.get("product_name"),
        copy.get("product_tagline"),
        int(C * 0.5),
        lockup_max_h,
        INK,
        MUTED,
    )
    chip_w = lockup_img.size[0] + 2 * chip_inner_pad
    chip_h = lockup_img.size[1] + 2 * chip_inner_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=chip_radius, fill=(255, 255, 255, 200))
    canvas.paste(lockup_img, (pad + chip_inner_pad, pad + chip_inner_pad), lockup_img)
    lh = chip_h

    # Headline with highlight-word tinting.
    text_top = pad + lh + headline_top_gap
    max_w = C - 2 * pad
    head_budget_h = py - text_top - 90  # leave room for CTA above photo
    f_head, lines = _fit_font(
        d, copy.get("headline", ""), "sans_bold",
        headline_max_pt, headline_min_pt,
        max_w, head_budget_h, max_lines=headline_max_lines,
    )
    soft = tuple(int(c + (255 - c) * headline_highlight_lightness) for c in primary_rgb)
    last_y = _draw_highlighted_lines(
        d, lines, f_head, pad, text_top,
        (255, 255, 255), soft, copy.get("highlight_words") or [],
        spacing=1.3
    )

    # White pill CTA, sits just above the photo with a configurable gap.
    cta = copy.get("cta") or "Learn more"
    y_cta = min(last_y + 32, py - cta_bottom_gap_from_photo - 44)
    _cta_pill_white(d, pad, y_cta, cta, primary_rgb)

    return canvas

def t1_top_band(bg: Image.Image, copy: dict, logo: Image.Image, primary_rgb: tuple, C: int) -> Image.Image:
    canvas = Image.new("RGB", (C, C), CREAM)
    band_h = int(C * 0.48)
    strip_h = C - band_h
    strip = _smart_fit(bg, C, strip_h)  # face-biased fit, no hard crop
    canvas.paste(strip, (0, band_h))
    d = ImageDraw.Draw(canvas)
    pad = 48
    lw, lh = _paste_lockup(canvas, logo, copy, pad, pad, C - 2 * pad, 72)
    text_top = pad + lh + max(48, int(lh * 0.45))  # breathing room under lockup
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 68, 36, C - 2 * pad, band_h - text_top - 140, max_lines=3)
    y = _draw_lines(d, lines, f_head, pad, text_top, INK)
    y += 32
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 32, 22, C - 2 * pad, 80, max_lines=2)
    _draw_lines(d, sub, f_sub, pad, y, MUTED)
    _cta_button(d, pad, band_h - 52 - pad, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t2_vsplit_left(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    left_w = C // 2
    strip = _smart_fit(bg, left_w, C)  # face-biased fit
    canvas.paste(strip, (left_w, 0))
    d = ImageDraw.Draw(canvas)
    pad = 44
    lw, lh = _paste_lockup(canvas, logo, copy, pad, pad, left_w - 2 * pad, 68)
    text_top = pad + lh + max(48, int(lh * 0.45))  # breathing room under lockup
    max_w = left_w - 2 * pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 60, 32, max_w, 360, max_lines=4)
    y = _draw_lines(d, lines, f_head, pad, text_top, INK)
    y += 32
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 28, 20, max_w, 110, max_lines=3)
    y = _draw_lines(d, sub, f_sub, pad, y, MUTED)
    _cta_button(d, pad, y + 36, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t3_vsplit_right(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    right_start = C // 2
    strip = _smart_fit(bg, right_start, C)  # face-biased fit
    canvas.paste(strip, (0, 0))
    d = ImageDraw.Draw(canvas)
    pad = 44
    text_x = right_start + pad
    lw, lh = _paste_lockup(canvas, logo, copy, text_x, pad, C - text_x - pad, 68)
    text_top = pad + lh + max(32, int(lh * 0.35))  # breathing room under lockup
    max_w = C - text_x - pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 60, 32, max_w, 360, max_lines=4)
    y = _draw_lines(d, lines, f_head, text_x, text_top, INK)
    y += 16
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 28, 20, max_w, 110, max_lines=3)
    y = _draw_lines(d, sub, f_sub, text_x, y, MUTED)
    _cta_button(d, text_x, y + 26, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t4_accent_bar(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    bar_w = 130
    photo_h = int(C * 0.64)
    hero_w = C - bar_w
    hero = _smart_fit(bg, hero_w, photo_h)
    canvas.paste(hero, (bar_w, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, bar_w, C], fill=primary_rgb)
    # Just the mark (no lockup) inside the narrow vertical bar — fits better.
    if logo is not None:
        mark = logo
        if mark.size[0] > bar_w - 24:
            r = (bar_w - 24) / mark.size[0]
            mark = mark.resize((bar_w - 24, max(1, int(mark.size[1] * r))), Image.LANCZOS)
        chip_pad = 8
        chip_w, chip_h = mark.size[0] + 2 * chip_pad, mark.size[1] + 2 * chip_pad
        chip_x = (bar_w - chip_w) // 2
        chip_y = 28
        d.rounded_rectangle([chip_x, chip_y, chip_x + chip_w, chip_y + chip_h], radius=6, fill=(255, 255, 255))
        canvas.paste(mark, (chip_x + chip_pad, chip_y + chip_pad), mark)
    # Bottom strip
    pad = 44
    text_top = photo_h + 32
    max_w = C - bar_w - 2 * pad
    text_x = bar_w + pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 52, 30, max_w, 140, max_lines=2)
    y = _draw_lines(d, lines, f_head, text_x, text_top, INK)
    y += 10
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 26, 18, max_w, 60, max_lines=2)
    _draw_lines(d, sub, f_sub, text_x, y, MUTED)
    # Solid brand CTA bottom-right
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 64
    btn_h = 44
    btn_x = C - pad - btn_w
    btn_y = C - pad - btn_h
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=3, fill=primary_rgb)
    d.text((btn_x + 22, btn_y + (btn_h - (tw[3] - tw[1])) // 2 - 2), copy.get("cta", "Learn more"), font=f_cta, fill=(255, 255, 255))
    return canvas


def t5_bottom_band(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    photo_h = int(C * 0.62)
    top = _smart_fit(bg, C, photo_h)
    canvas.paste(top, (0, 0))
    d = ImageDraw.Draw(canvas)
    pad = 48
    lw, lh = _paste_lockup(canvas, logo, copy, pad, pad, int(C * 0.5), 64)
    text_top = photo_h + 36
    max_w = C - 2 * pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 58, 32, max_w, 200, max_lines=2)
    y = _draw_lines(d, lines, f_head, pad, text_top, INK)
    y += 14
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 28, 20, max_w, 70, max_lines=2)
    y = _draw_lines(d, sub, f_sub, pad, y, MUTED)
    _cta_button(d, pad, y + 24, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t6_sandwich(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    top_h = int(C * 0.32)
    bot_h = int(C * 0.18)
    photo_h = C - top_h - bot_h
    strip = _smart_fit(bg, C, photo_h)
    canvas.paste(strip, (0, top_h))
    d = ImageDraw.Draw(canvas)
    pad = 44
    lw, lh = _paste_lockup(canvas, logo, copy, pad, pad, int(C * 0.55), 60)
    text_top = pad + lh + max(32, int(lh * 0.35))  # breathing room under lockup
    max_w = C - 2 * pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 52, 32, max_w, top_h - text_top - 8, max_lines=2)
    _draw_lines(d, lines, f_head, pad, text_top, INK)
    sub_y = top_h + photo_h + 20
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 26, 18, max_w, 60, max_lines=2)
    _draw_lines(d, sub, f_sub, pad, sub_y, MUTED)
    _cta_button(d, C - pad - 180, C - pad - 52, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t7_frame(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), primary_rgb)
    top_frame_h = 130
    bot_frame_h = 130
    side_frame = 44
    photo_w = C - 2 * side_frame
    photo_h = C - top_frame_h - bot_frame_h
    canvas.paste(_smart_fit(bg, photo_w, photo_h), (side_frame, top_frame_h))
    d = ImageDraw.Draw(canvas)
    pad = 44
    # White chip holding the lockup so text reads over brand-color frame
    lockup = _render_logo_lockup(
        logo or _load_logo_image(None, 130, 44),
        copy.get("product_name"),
        copy.get("product_tagline"),
        int(C * 0.55),
        top_frame_h - 24,
        INK,
        MUTED,
    )
    chip_pad = 10
    chip_w, chip_h = lockup.size[0] + 2 * chip_pad, lockup.size[1] + 2 * chip_pad
    chip_y = (top_frame_h - chip_h) // 2
    d.rounded_rectangle([pad, chip_y, pad + chip_w, chip_y + chip_h], radius=6, fill=(255, 255, 255))
    canvas.paste(lockup, (pad + chip_pad, chip_y + chip_pad), lockup)
    head_x = pad + chip_w + 20
    max_w = C - head_x - pad
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 32, 22, max_w, top_frame_h - 32, max_lines=2)
    _draw_lines(d, lines, f_head, head_x, 30, (255, 255, 255))
    sub_y = C - bot_frame_h + 28
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 24, 17, int(C * 0.6), 55, max_lines=2)
    _draw_lines(d, sub, f_sub, pad, sub_y, (255, 255, 255))
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 60
    btn_h = 42
    d.rounded_rectangle(
        [C - pad - btn_w, C - bot_frame_h + (bot_frame_h - btn_h) // 2,
         C - pad, C - bot_frame_h + (bot_frame_h + btn_h) // 2],
        radius=3, fill=(255, 255, 255),
    )
    d.text(
        (C - pad - btn_w + 20, C - bot_frame_h + (bot_frame_h - btn_h) // 2 + 10),
        copy.get("cta", "Learn more"), font=f_cta, fill=primary_rgb,
    )
    return canvas


def t8_photo_dominant(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    photo_w = int(C * 0.72)
    strip = _smart_fit(bg, photo_w, C)
    canvas.paste(strip, (0, 0))
    d = ImageDraw.Draw(canvas)
    pad = 28
    text_x = photo_w + pad
    max_w = C - text_x - pad
    lw, lh = _paste_lockup(canvas, logo, copy, text_x, pad + 10, max_w, 52)
    text_top = pad + 10 + lh + 24
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 32, 22, max_w, 260, max_lines=5)
    y = _draw_lines(d, lines, f_head, text_x, text_top, INK)
    y += 10
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 21, 15, max_w, 90, max_lines=4)
    _draw_lines(d, sub, f_sub, text_x, y, MUTED)
    f_cta = _font("sans_bold", 17)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 40
    btn_h = 38
    bx, by = text_x, C - pad - btn_h
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h], radius=3, fill=primary_rgb)
    d.text((bx + 14, by + 11), copy.get("cta", "Learn more"), font=f_cta, fill=(255, 255, 255))
    return canvas


def t9_eyebrow_bar(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    bar_h = int(C * 0.10)
    photo_h = int(C * 0.58)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, C, bar_h], fill=DARK_INK)
    mid = _smart_fit(bg, C, photo_h)
    canvas.paste(mid, (0, bar_h))
    d = ImageDraw.Draw(canvas)
    pad = 36
    # Lockup on dark bar — white chip behind it
    lockup = _render_logo_lockup(
        logo or _load_logo_image(None, 110, 36),
        copy.get("product_name"),
        copy.get("product_tagline"),
        int(C * 0.55),
        bar_h - 16,
        INK,
        MUTED,
    )
    chip_pad = 6
    chip_w, chip_h = lockup.size[0] + 2 * chip_pad, lockup.size[1] + 2 * chip_pad
    chip_y = (bar_h - chip_h) // 2
    d.rounded_rectangle([pad, chip_y, pad + chip_w, chip_y + chip_h], radius=5, fill=(255, 255, 255))
    canvas.paste(lockup, (pad + chip_pad, chip_y + chip_pad), lockup)
    d.rectangle([C - pad - 6, (bar_h - 22) // 2, C - pad, (bar_h + 22) // 2], fill=primary_rgb)
    text_top = bar_h + photo_h + 20
    max_w = C - 2 * pad
    bot_h = C - bar_h - photo_h
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 40, 26, max_w, bot_h - 90, max_lines=2)
    y = _draw_lines(d, lines, f_head, pad, text_top, INK)
    y += 8
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 23, 17, max_w, 50, max_lines=2)
    _draw_lines(d, sub, f_sub, pad, y, MUTED)
    _cta_button(d, C - pad - 180, C - pad - 46, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t10_poster(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), CREAM)
    d = ImageDraw.Draw(canvas)
    pad = 48
    lw, lh = _paste_lockup(canvas, logo, copy, pad, pad, int(C * 0.55), 68)
    photo_side = int(C * 0.46)
    px = (C - photo_side) // 2
    py = pad + lh + 16
    canvas.paste(_smart_fit(bg, photo_side, photo_side), (px, py))
    text_top = py + photo_side + 28
    max_w = C - 2 * pad
    available_h = C - text_top - 120
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 54, 30, max_w, available_h, max_lines=3)
    y = _draw_lines(d, lines, f_head, pad, text_top, INK)
    y += 6
    f_sub, sub = _fit_font(d, copy.get("subheading", ""), "sans_reg", 23, 17, max_w, 50, max_lines=2)
    _draw_lines(d, sub, f_sub, pad, y, MUTED)
    _cta_button(d, pad, C - pad - 46, copy.get("cta", "Learn more"), primary_rgb)
    return canvas


def t11_cta_strip(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), primary_rgb)
    photo_h = int(C * 0.80)
    top = _smart_fit(bg, C, photo_h)
    canvas.paste(top, (0, 0))
    d = ImageDraw.Draw(canvas)
    pad = 40
    # Lockup on white chip (photo may be busy behind it)
    lockup = _render_logo_lockup(
        logo or _load_logo_image(None, 130, 44),
        copy.get("product_name"),
        copy.get("product_tagline"),
        int(C * 0.55),
        56,
        INK,
        MUTED,
    )
    chip_pad = 8
    chip_w, chip_h = lockup.size[0] + 2 * chip_pad, lockup.size[1] + 2 * chip_pad
    d.rounded_rectangle([pad, pad, pad + chip_w, pad + chip_h], radius=8, fill=(255, 255, 255, 200))
    canvas.paste(lockup, (pad + chip_pad, pad + chip_pad), lockup)
    strip_y = photo_h
    strip_h = C - photo_h
    max_w = C - 2 * pad - 220
    # T11 has no subheading — headline is the only text on the strip, so
    # give it more room to breathe. Bumped max 32→44, min 22→26.
    f_head, lines = _fit_font(d, copy["headline"], "serif_bold", 44, 26, max_w, strip_h - 30, max_lines=2)
    _draw_lines(d, lines, f_head, pad, strip_y + 18, (255, 255, 255))
    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 56
    btn_h = 44
    bx = C - pad - btn_w
    by = strip_y + (strip_h - btn_h) // 2
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h], radius=3, fill=(255, 255, 255))
    d.text((bx + 20, by + 12), copy.get("cta", "Learn more"), font=f_cta, fill=primary_rgb)
    ax, ay = bx + btn_w - 20, by + btn_h // 2
    d.polygon([(ax, ay - 5), (ax + 9, ay), (ax, ay + 5)], fill=primary_rgb)
    return canvas


# =============================================================================
# V2 SOCIAL-MEDIA TEMPLATES (April 2026) — ported from render_5_templates.py
# Five templates optimized for actual social posting use cases:
#   T1 = general header + photo below
#   T2 = full-bleed hero banner (Instagram story feel)
#   T3 = quote / thought-leadership card
#   T4 = numbered insight / carousel tip
#   T5 = header + text + photo middle + CTA (brand card)
#
# Design rules:
#   • Pure WHITE canvas (no cream)
#   • Product name in PRIMARY color; tagline pure BLACK (bold)
#   • Headlines render with primary-color highlighted accent words
#   • Subheadings pure BLACK (no separate color treatment)
#   • UnsharpMask pass on every photo so cropped bg doesn't read blurry
#   • Fonts: Montserrat Bold (display/headline) + Inter (body/lockup/CTA)
# =============================================================================

V2_WHITE = (255, 255, 255)
V2_BLACK = (0, 0, 0)

# Style override context — set by `apply_overrides()` from `compose()` and
# read by the V2 helpers below. Keeps the per-template renderers stateless
# while still letting saved user templates customize fonts/colors/CTA-style
# without a full API change.
import contextvars
_STYLE_CTX: contextvars.ContextVar[dict] = contextvars.ContextVar("_STYLE_CTX", default={})


def _style(key: str, default=None):
    return _STYLE_CTX.get().get(key, default)


def _style_color(key: str, fallback: tuple) -> tuple:
    v = _style(key)
    if v and isinstance(v, str) and v.startswith("#"):
        try:
            return _hex_to_rgb(v)
        except Exception:
            return fallback
    return fallback


def apply_overrides(overrides: dict | None) -> dict:
    """Normalize an override blob. Returns a dict the helpers consult via
    `_STYLE_CTX`. Unknown keys are ignored so the schema can evolve without
    breaking old saved templates.

    `positions` sub-dict holds per-element x/y/w/h overrides as PERCENTAGES
    (0–100) of the canvas side, so the editor's canvas-size choice is
    independent of render resolution. Any missing element (or missing field
    on an element) falls back to the base layout's built-in default.

    Example:
        "positions": {
            "logo":       {"x": 5,  "y": 5},
            "headline":   {"x": 5,  "y": 18, "w": 90},
            "subheading": {"x": 5,  "y": 32, "w": 90},
            "photo":      {"x": 5,  "y": 48, "w": 90, "h": 45},
            "cta":        {"x": 60, "y": 92}
        }
    """
    o = dict(overrides or {})
    positions = o.get("positions") or {}
    if not isinstance(positions, dict):
        positions = {}
    return {
        "headline_color":    o.get("headline_color"),
        "subheading_color":  o.get("subheading_color"),
        "tagline_color":     o.get("tagline_color"),
        "headline_font":     o.get("headline_font") or "display",
        "subheading_font":   o.get("subheading_font") or "sans_reg",
        "cta_style":         o.get("cta_style") or "solid",
        "cta_position":      o.get("cta_position"),
        "accent_words":      o.get("accent_words"),       # headline tints
        "sub_accent_words":  o.get("sub_accent_words"),   # subheading tints
        "bg_corner_radius":  o.get("bg_corner_radius"),
        "primary_color":     o.get("primary_color"),
        "positions":         positions,
    }


def _pos(element: str, C: int, defaults: dict) -> dict:
    """Resolve absolute pixel coordinates for one positioned element.

    `defaults` = pixel fallbacks keyed by 'x', 'y', 'w', 'h'. Any key missing
    from the override (or the whole element) uses the pixel default. The
    override is expected as percentages (0–100).
    """
    positions = _style("positions") or {}
    p = positions.get(element) or {}
    out = {}
    for key, default_px in defaults.items():
        val = p.get(key)
        if isinstance(val, (int, float)):
            out[key] = int(round(val * C / 100.0))
        else:
            out[key] = default_px
    return out


def _v2_sharpen(im: Image.Image) -> Image.Image:
    """Identity pass — the UnsharpMask filter was removed per user feedback
    so the AI-generated photo is shown exactly as produced, no post-effects.
    Kept as a function so call sites stay stable."""
    return im


def _v2_photo_fit(bg: Image.Image, w: int, h: int) -> Image.Image:
    return _smart_fit(bg, w, h)


def _v2_render_lockup(logo: Image.Image, name: str, tagline: str,
                      max_w: int, max_h: int, primary_rgb: tuple) -> Image.Image:
    """Lockup: [logo mark | NAME (primary) / tagline (black bold)]."""
    mark_h = max_h
    lw, lh = logo.size
    mark = logo.resize((max(1, int(lw * mark_h / lh)), mark_h), Image.LANCZOS)
    mark_w = mark.size[0]

    name_pt = max(22, min(58, int(max_h * 0.54)))
    tag_pt = max(14, min(28, int(max_h * 0.32)))
    name_f = _font("sans_bold", name_pt)
    tag_f = _font("sans_bold", tag_pt)  # bold so black reads as black

    td = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    n_bbox = td.textbbox((0, 0), name, font=name_f)
    t_bbox = td.textbbox((0, 0), tagline, font=tag_f) if tagline else (0, 0, 0, 0)
    n_asc, n_desc = name_f.getmetrics()
    t_asc, t_desc = tag_f.getmetrics() if tagline else (0, 0)
    name_h = n_asc + n_desc
    tag_h = (t_asc + t_desc) if tagline else 0
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
        tag_h = (t_asc + t_desc) if tagline else 0
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
    d.text((tx, ty), name, font=name_f, fill=primary_rgb)
    if tagline:
        tag_color = _style_color("tagline_color", V2_BLACK)
        d.text((tx, ty + name_h + line_gap), tagline, font=tag_f, fill=tag_color)
    return canvas


def _v2_draw_highlighted(d: ImageDraw.ImageDraw, lines: list, font,
                         x: int, y: int, hi_words: list, primary_rgb: tuple,
                         spacing: float = 1.16, base_color: tuple = None) -> int:
    # If the caller passed an explicit base_color (e.g. WHITE for dark-overlay
    # templates) honour it. Otherwise consult the style override, falling back
    # to pure black.
    if base_color is None:
        base_color = _style_color("headline_color", V2_BLACK)
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


def _v2_sub_color() -> tuple:
    """Resolved subheading color — honours `subheading_color` override or
    defaults to pure black."""
    return _style_color("subheading_color", V2_BLACK)


def _v2_draw_sub(d: ImageDraw.ImageDraw, lines: list, font,
                 x: int, y: int, primary_rgb: tuple, spacing: float = 1.30,
                 base_color: tuple = None) -> int:
    """Draw subheading in a single color — ALWAYS pure black by default
    (per user feedback: no two-tone mix on the subheading). The
    `sub_accent_words` override is intentionally ignored now; sub stays
    one clean color so it reads as body copy, not an accent line."""
    color = base_color if base_color is not None else _v2_sub_color()
    return _draw_lines(d, lines, font, x, y, color, spacing=spacing)


def _v2_cta_solid(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                  primary_rgb: tuple, font_pt: int = 20) -> tuple:
    """When style override cta_style = 'outline' is set, delegate to the
    outline variant so templates that call `_v2_cta_solid` automatically
    respect the user's CTA preference."""
    if _style("cta_style") == "outline":
        return _v2_cta_outline(d, x, y, text, primary_rgb, font_pt)
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text, font=f)
    w = (tw[2] - tw[0]) + 60
    h = font_pt * 2 + 18
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=primary_rgb)
    asc, desc = f.getmetrics()
    d.text((x + 30, y + (h - (asc + desc)) // 2), text, font=f, fill=V2_WHITE)
    return w, h


def _v2_cta_outline(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                    primary_rgb: tuple, font_pt: int = 20) -> tuple:
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text, font=f)
    w = (tw[2] - tw[0]) + 72
    h = font_pt * 2 + 18
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                        fill=V2_WHITE, outline=primary_rgb, width=2)
    asc, desc = f.getmetrics()
    d.text((x + 28, y + (h - (asc + desc)) // 2), text, font=f, fill=V2_BLACK)
    ax, ay = x + w - 26, y + h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=primary_rgb)
    return w, h


def _v2_eyebrow_pill(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                     primary_rgb: tuple, font_pt: int = 16) -> tuple:
    f = _font("sans_bold", font_pt)
    tw = d.textbbox((0, 0), text.upper(), font=f)
    w = (tw[2] - tw[0]) + 36
    h = font_pt * 2 + 10
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=primary_rgb)
    asc, desc = f.getmetrics()
    d.text((x + 18, y + (h - (asc + desc)) // 2), text.upper(), font=f, fill=V2_WHITE)
    return w, h


# -----------------------------------------------------------------------------
# T1 V2 — HEADER BAND + PHOTO FLUSH BELOW
# -----------------------------------------------------------------------------
def t1_v2_header_band(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 48

    lockup = _v2_render_lockup(logo, copy.get("product_name"), copy.get("product_tagline"),
                               C - 2 * pad, 56, primary_rgb)
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    default_text_top = logo_pos["y"] + lockup.size[1] + 22
    head_pos = _pos("headline", C, {
        "x": pad, "y": default_text_top, "w": C - 2 * pad,
    })
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   38, 26, head_pos["w"], 130,
                                   max_lines=2, spacing=1.16)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.16)

    sub_pos = _pos("subheading", C, {
        "x": pad, "y": y + 10, "w": C - 2 * pad,
    })
    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 30, 22, sub_pos["w"], 96,
                                 max_lines=2, spacing=1.30)
    y = _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                     primary_rgb, spacing=1.30)

    default_cta_y = y + 14
    cta_pos = _pos("cta", C, {"x": pad, "y": default_cta_y})
    cta_w, cta_h = _v2_cta_solid(d, cta_pos["x"], cta_pos["y"],
                                 copy.get("cta", "Learn more"), primary_rgb)

    default_photo_top = cta_pos["y"] + cta_h + 24
    photo_pos = _pos("photo", C, {
        "x": 0, "y": default_photo_top,
        "w": C, "h": C - default_photo_top,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]))
    return canvas


# -----------------------------------------------------------------------------
# T2 V2 — FULL-BLEED BANNER (Instagram-style)
# -----------------------------------------------------------------------------
def t2_v2_full_bleed(bg, copy, logo, primary_rgb, C):
    canvas = _v2_photo_fit(bg, C, C).convert("RGB")
    grad = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    grad_top = int(C * 0.38)
    grad_px = grad.load()
    for y in range(grad_top, C):
        t = (y - grad_top) / max(1, (C - grad_top))
        alpha = int(210 * t)
        for x in range(C):
            grad_px[x, y] = (0, 0, 0, alpha)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), grad).convert("RGB")
    d = ImageDraw.Draw(canvas)
    pad = 52

    # Logo MARK only on small white chip
    mark_h = 56
    mark_w = max(1, int(logo.size[0] * mark_h / logo.size[1]))
    mark = logo.resize((mark_w, mark_h), Image.LANCZOS)
    chip_pad = 10
    chip_w = mark_w + 2 * chip_pad
    chip_h = mark_h + 2 * chip_pad
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    d.rounded_rectangle([logo_pos["x"], logo_pos["y"],
                         logo_pos["x"] + chip_w, logo_pos["y"] + chip_h],
                        radius=12, fill=V2_WHITE)
    canvas.paste(mark, (logo_pos["x"] + chip_pad, logo_pos["y"] + chip_pad), mark)

    # Product name (primary) + tagline (white) — pinned right of the chip.
    name_pt, tag_pt = 30, 16
    f_name = _font("sans_bold", name_pt)
    f_tag = _font("sans_bold", tag_pt)
    n_asc, n_desc = f_name.getmetrics()
    t_asc, t_desc = f_tag.getmetrics()
    name_line_h = n_asc + n_desc
    tag_line_h = t_asc + t_desc
    total_text_h = name_line_h + 4 + tag_line_h
    text_x = logo_pos["x"] + chip_w + 16
    text_y = logo_pos["y"] + (chip_h - total_text_h) // 2
    d.text((text_x, text_y), copy.get("product_name", ""), font=f_name, fill=primary_rgb)
    d.text((text_x, text_y + name_line_h + 4), copy.get("product_tagline", ""),
           font=f_tag, fill=V2_WHITE)

    # Bottom stack
    cta_h = 54
    f_cta = _font("sans_bold", 20)
    twc = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (twc[2] - twc[0]) + 60

    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 28, 20, int(C * 0.78), 110,
                                 max_lines=2, spacing=1.30)
    sub_asc, sub_desc = f_sub.getmetrics()
    sub_line_h = int((sub_asc + sub_desc) * 1.30)
    sub_h = sub_line_h * len(sub_lines)

    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   56, 34, C - 2 * pad, 260,
                                   max_lines=3, spacing=1.10)
    h_asc, h_desc = f_head.getmetrics()
    h_line_h = int((h_asc + h_desc) * 1.10)
    head_h = h_line_h * len(head_lines)

    default_cta_y = C - pad - cta_h
    default_sub_y = default_cta_y - 18 - sub_h
    default_head_y = default_sub_y - 16 - head_h

    head_pos = _pos("headline", C, {"x": pad, "y": default_head_y, "w": C - 2 * pad})
    sub_pos  = _pos("subheading", C, {"x": pad, "y": default_sub_y, "w": int(C * 0.78)})
    cta_pos  = _pos("cta", C, {"x": C - pad - btn_w, "y": default_cta_y})

    _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                         copy.get("highlight_words"), primary_rgb,
                         spacing=1.10, base_color=V2_WHITE)
    _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                 primary_rgb, spacing=1.30, base_color=V2_WHITE)
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"], copy.get("cta", "Learn more"), primary_rgb)
    return canvas


# -----------------------------------------------------------------------------
# T3 V2 — QUOTE / STATEMENT CARD
# -----------------------------------------------------------------------------
def t3_v2_quote_card(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 56

    lockup = _v2_render_lockup(logo, copy.get("product_name"), copy.get("product_tagline"),
                               int((C - 2 * pad) * 0.65), 52, primary_rgb)
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    qf = _font("display", 150)
    q_y = logo_pos["y"] + lockup.size[1] + 4
    d.text((logo_pos["x"] - 10, q_y), "\u201C", font=qf, fill=primary_rgb)
    quote_bottom = q_y + 105

    default_text_top = quote_bottom + 4
    head_pos = _pos("headline", C, {"x": pad, "y": default_text_top, "w": C - 2 * pad})
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   40, 26, head_pos["w"], 180,
                                   max_lines=3, spacing=1.16)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.16)
    y += 14
    d.rectangle([head_pos["x"], y, head_pos["x"] + 60, y + 4], fill=primary_rgb)
    y += 14
    # Attribution is exposed under the "subheading" override slot so it's
    # draggable from the editor alongside the main text elements.
    sub_pos = _pos("subheading", C, {"x": head_pos["x"], "y": y, "w": C - 2 * pad})
    f_attr = _font("sans_bold", 20)
    asc, desc = f_attr.getmetrics()
    d.text((sub_pos["x"], sub_pos["y"]),
           copy.get("attribution", f"— {copy.get('product_name', 'The Team')} Team"),
           font=f_attr, fill=_v2_sub_color())
    attr_bottom = sub_pos["y"] + asc + desc

    default_photo_top = attr_bottom + 24
    photo_pos = _pos("photo", C, {
        "x": 0, "y": default_photo_top,
        "w": C, "h": C - default_photo_top,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]))
    return canvas


# -----------------------------------------------------------------------------
# T4 V2 — NUMBERED INSIGHT / TIP CARD
# -----------------------------------------------------------------------------
def t4_v2_insight(bg, copy, logo, primary_rgb, C):
    """Clean editorial with full-width headline + subheading above a rounded
    photo band and solid primary CTA overlay. INSIGHT/01 eyebrow + huge
    numeral removed per user feedback — just logo, text, photo, CTA now."""
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 56

    lockup = _v2_render_lockup(logo, copy.get("product_name"), copy.get("product_tagline"),
                               int((C - 2 * pad) * 0.80), 56, primary_rgb)
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # Headline — full width below logo
    head_top = logo_pos["y"] + lockup.size[1] + 28
    head_pos = _pos("headline", C, {"x": pad, "y": head_top, "w": C - 2 * pad})
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   44, 26, head_pos["w"], 180,
                                   max_lines=3, spacing=1.16)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.16)
    y += 12
    sub_pos = _pos("subheading", C, {"x": pad, "y": y, "w": C - 2 * pad})
    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 30, 22, sub_pos["w"], 110,
                                 max_lines=3, spacing=1.32)
    _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                 primary_rgb, spacing=1.32)

    default_photo_h = int(C * 0.42)
    default_photo_top = C - default_photo_h - pad
    photo_pos = _pos("photo", C, {
        "x": pad, "y": default_photo_top,
        "w": C - 2 * pad, "h": default_photo_h,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    mask = _rounded_mask(photo.size, 22)
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]), mask)

    f_cta = _font("sans_bold", 19)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 54
    btn_h = 46
    cta_pos = _pos("cta", C, {
        "x": C - pad - btn_w - 18, "y": C - pad - btn_h - 18,
    })
    bx, by = cta_pos["x"], cta_pos["y"]
    d.rounded_rectangle([bx, by, bx + btn_w, by + btn_h],
                        radius=btn_h // 2, fill=primary_rgb)
    asc, desc = f_cta.getmetrics()
    d.text((bx + 26, by + (btn_h - (asc + desc)) // 2),
           copy.get("cta", "Learn more"), font=f_cta, fill=V2_WHITE)
    return canvas


# -----------------------------------------------------------------------------
# T5 V2 — BRAND CARD (header → text → photo middle → CTA)
# -----------------------------------------------------------------------------
def t5_v2_brand_card(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 60

    # Default pixel positions — each _pos() call honours an override if set.
    lockup = _v2_render_lockup(logo, copy.get("product_name"), copy.get("product_tagline"),
                               C - 2 * pad, 58, primary_rgb)
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    default_text_top = logo_pos["y"] + lockup.size[1] + 30
    head_pos = _pos("headline", C, {
        "x": pad, "y": default_text_top, "w": C - 2 * pad,
    })
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   38, 26, head_pos["w"], 120,
                                   max_lines=2, spacing=1.14)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.14)

    sub_pos = _pos("subheading", C, {
        "x": pad, "y": y + 6, "w": int((C - 2 * pad) * 0.70),
    })
    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 28, 20, sub_pos["w"], 90,
                                 max_lines=2, spacing=1.30)
    y = _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                     primary_rgb, spacing=1.30)

    cta_h = 50
    default_photo_y = y + 24
    default_photo_h = C - default_photo_y - cta_h - pad - 20
    # Photo is FULL-BLEED horizontally (x=0, w=C) so the background image
    # flows edge-to-edge. Previously it was inset by `pad` on each side which
    # left visible white margins around the photo — user feedback flagged
    # this as "background image not placed correctly". Rounded mask dropped
    # because rounded corners don't make sense when the photo touches the
    # canvas edges (they'd carve white notches out of the edges).
    photo_pos = _pos("photo", C, {
        "x": 0, "y": default_photo_y,
        "w": C, "h": default_photo_h,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]))

    f_cta = _font("sans_bold", 20)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 60
    cta_pos = _pos("cta", C, {
        "x": C - pad - btn_w, "y": C - pad - cta_h,
    })
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"], copy.get("cta", "Learn more"), primary_rgb)
    return canvas


# -----------------------------------------------------------------------------
# T6 V2 — WEBINAR / EVENT (Wylto × Meta style, stripped)
# Use case: webinar invites, live event announcements, co-branded moments
# Layout: logo top-left, "LIVE WEBINAR" primary-pill eyebrow below, left
#         column takes ~55% with headline + subheading + solid primary CTA,
#         right column 45% holds a portrait-oriented rounded photo.
# -----------------------------------------------------------------------------
def t6_v2_webinar(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 56

    # 1. Logo lockup top-left
    lockup = _v2_render_lockup(
        logo, copy.get("product_name"), copy.get("product_tagline"),
        int((C - 2 * pad) * 0.55), 58, primary_rgb,
    )
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # 2. Right column — portrait photo, rounded (LIVE WEBINAR pill removed)
    right_x = int(C * 0.56)
    photo_y = int(C * 0.20)
    photo_w = C - right_x - pad
    photo_h = int(C * 0.66)
    photo_pos = _pos("photo", C, {
        "x": right_x, "y": photo_y, "w": photo_w, "h": photo_h,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    mask = _rounded_mask(photo.size, 28)
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]), mask)

    # 3. Left column — headline + subheading, bounded by right column's x.
    # Anchored to the VERTICAL CENTER of the right-column photo so the
    # text block visually aligns with the subject (user feedback: text
    # felt too high up, out of sync with the image on the right).
    text_max_w = right_x - pad - 24
    # Photo spans photo_y to photo_y + photo_h (~20% to 86% of canvas).
    # Headline top target: 28% of canvas — places text vertically in the
    # upper-third of the photo area rather than at the very top.
    head_top = int(C * 0.28)
    head_pos = _pos("headline", C, {"x": pad, "y": head_top, "w": text_max_w})
    f_head, head_lines = _fit_font(
        d, copy.get("headline", ""), "display",
        56, 32, head_pos["w"], 260, max_lines=3, spacing=1.12,
    )
    y = _v2_draw_highlighted(
        d, head_lines, f_head, head_pos["x"], head_pos["y"],
        copy.get("highlight_words"), primary_rgb, spacing=1.12,
    )

    # Subheading
    y += 18
    sub_pos = _pos("subheading", C, {"x": pad, "y": y, "w": text_max_w})
    f_sub, sub_lines = _fit_font(
        d, copy.get("subheading", ""), "sans_reg",
        30, 22, sub_pos["w"], 150, max_lines=3, spacing=1.30,
    )
    y = _v2_draw_sub(
        d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"], primary_rgb, spacing=1.30,
    )

    # 5. Solid primary CTA bottom-left
    cta_h = 56
    cta_pos = _pos("cta", C, {"x": pad, "y": C - pad - cta_h})
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"],
                  copy.get("cta", "Register Now"), primary_rgb)
    return canvas


# -----------------------------------------------------------------------------
# T7 V2 — CENTERED POSTER (Nike / Apple / Airbnb style)
# Use case: high-impact brand posts where the photo IS the message. The AI-
# generated background is shown in full (no crops or side panels), with a
# soft bottom-up gradient so centered text stays legible. Most-shared format
# on Instagram / LinkedIn feeds.
# -----------------------------------------------------------------------------
def t7_v2_poster(bg, copy, logo, primary_rgb, C):
    # 1. Photo — top ~58% of canvas. The bottom 42% is a solid white card
    #    holding the logo, headline, sub, and CTA. Previously the whole
    #    canvas was the photo with a dark gradient scrim behind the text —
    #    user feedback flagged that scrim as a "gray strip" they didn't want
    #    on any template. White card keeps legibility without any tint.
    photo_h = int(C * 0.58)
    photo = _v2_photo_fit(bg, C, photo_h)
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    canvas.paste(photo, (0, 0))
    d = ImageDraw.Draw(canvas)

    pad = 52
    card_top = photo_h  # white card begins where the photo ends

    # 2. Logo lockup (V2 style — mark + primary name + black bold tagline)
    #    sits at the top of the white card, left-aligned.
    lockup = _v2_render_lockup(
        logo, copy.get("product_name"), copy.get("product_tagline"),
        C - 2 * pad, 56, primary_rgb,
    )
    logo_pos = _pos("logo", C, {"x": pad, "y": card_top + 28})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # 3. CENTERED headline + subheading + CTA stack in the white card.
    cta_h = 54
    f_cta = _font("sans_bold", 20)
    twc = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    cta_btn_w = (twc[2] - twc[0]) + 60

    f_sub, sub_lines = _fit_font(
        d, copy.get("subheading", ""), "sans_reg",
        26, 18, int(C * 0.80), 90,
        max_lines=2, spacing=1.28,
    )
    sub_asc, sub_desc = f_sub.getmetrics()
    sub_line_h = int((sub_asc + sub_desc) * 1.28)
    sub_h = sub_line_h * len(sub_lines)

    # Headline size tuned for the ~42% white card — was 52/32 when it owned
    # the whole canvas, now bounded by the card height.
    f_head, head_lines = _fit_font(
        d, copy.get("headline", ""), "display",
        38, 24, int(C * 0.82), 150,
        max_lines=2, spacing=1.14,
    )
    h_asc, h_desc = f_head.getmetrics()
    h_line_h = int((h_asc + h_desc) * 1.14)
    head_h = h_line_h * len(head_lines)

    # Bottom-anchored stack: CTA at bottom of canvas, sub above, headline above.
    cta_y = C - pad - cta_h
    sub_y = cta_y - 20 - sub_h
    head_y = sub_y - 14 - head_h

    # 4. Headline — CENTER-aligned, base BLACK with primary-color highlights
    #    (reads cleanly on the white card instead of white-on-dark-scrim).
    def _draw_centered_highlighted(lines, font, y, pri_rgb, base=V2_BLACK, spacing=1.14):
        asc, desc = font.getmetrics()
        line_h = int((asc + desc) * spacing)
        hi_set = {w.lower().strip(".,;:!?") for w in (copy.get("highlight_words") or [])}
        cy = y
        for line in lines:
            lw = d.textbbox((0, 0), line, font=font)[2]
            cx = (C - lw) // 2
            for word in line.split():
                clean = word.strip(".,;:!?-").lower()
                col = pri_rgb if clean in hi_set else base
                d.text((cx, cy), word, font=font, fill=col)
                wb = d.textbbox((0, 0), word + " ", font=font)
                cx += wb[2] - wb[0]
            cy += line_h
        return cy

    head_pos = _pos("headline", C, {"x": 0, "y": head_y, "w": C})
    _draw_centered_highlighted(head_lines, f_head, head_pos["y"], primary_rgb, spacing=1.14)

    # 5. Subheading — CENTER-aligned, black with optional primary accents.
    sub_pos = _pos("subheading", C, {"x": 0, "y": sub_y, "w": C})
    sub_cy = sub_pos["y"]
    sub_accents = {w.lower().strip(".,;:!?") for w in (_style("sub_accent_words") or [])}
    asc, desc = f_sub.getmetrics()
    lh = int((asc + desc) * 1.28)
    for line in sub_lines:
        lw = d.textbbox((0, 0), line, font=f_sub)[2]
        cx = (C - lw) // 2
        for word in line.split():
            clean = word.strip(".,;:!?-").lower()
            col = primary_rgb if clean in sub_accents else V2_BLACK
            d.text((cx, sub_cy), word, font=f_sub, fill=col)
            wb = d.textbbox((0, 0), word + " ", font=f_sub)
            cx += wb[2] - wb[0]
        sub_cy += lh

    # 6. Centered solid-primary CTA pill.
    cta_pos = _pos("cta", C, {"x": (C - cta_btn_w) // 2, "y": cta_y})
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"],
                  copy.get("cta", "Learn more"), primary_rgb)
    return canvas


# -----------------------------------------------------------------------------
# T8 V2 — FULL-BLEED PHOTO + TEXT CARD (Instagram / LinkedIn native)
# Use case: max legibility with the AI-generated photo covering the entire
# canvas. Bottom ~44% is a near-opaque white card that carries every text
# element cleanly (logo lockup, headline, sub, CTA). The upper 56% of the
# photo is unobstructed. Nothing gets blended with a busy subject.
# -----------------------------------------------------------------------------
def t8_v2_photo_card(bg, copy, logo, primary_rgb, C):
    # 1. Full-bleed photo — fills ENTIRE canvas.
    canvas = _v2_photo_fit(bg, C, C).convert("RGB")
    d = ImageDraw.Draw(canvas)

    # 2. White card on the bottom — 44% height, near-opaque (~94%) so the
    #    photo faintly bleeds through the top edge of the card while every
    #    text element reads crisply against a solid background.
    card_h = int(C * 0.44)
    card_top = C - card_h
    card = Image.new("RGBA", (C, card_h), (255, 255, 255, 242))
    canvas.paste(card, (0, card_top), card)
    # Very soft shadow above the card to separate it from the photo
    shadow = Image.new("RGBA", (C, 8), (0, 0, 0, 0))
    for i in range(8):
        alpha = int(32 * (1 - i / 8))
        ImageDraw.Draw(shadow).line([(0, i), (C, i)], fill=(0, 0, 0, alpha))
    canvas.paste(shadow, (0, card_top - 8), shadow)
    d = ImageDraw.Draw(canvas)

    pad = 52

    # 3. Logo lockup — top of the white card
    lockup = _v2_render_lockup(
        logo, copy.get("product_name"), copy.get("product_tagline"),
        C - 2 * pad, 52, primary_rgb,
    )
    logo_pos = _pos("logo", C, {"x": pad, "y": card_top + 24})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # 4. Headline — inside the white card, left-aligned, bold
    head_top = logo_pos["y"] + lockup.size[1] + 18
    head_pos = _pos("headline", C, {"x": pad, "y": head_top, "w": C - 2 * pad})
    f_head, head_lines = _fit_font(
        d, copy.get("headline", ""), "display",
        36, 22, head_pos["w"], 130, max_lines=2, spacing=1.14,
    )
    y = _v2_draw_highlighted(
        d, head_lines, f_head, head_pos["x"], head_pos["y"],
        copy.get("highlight_words"), primary_rgb, spacing=1.14,
    )

    # 5. Subheading — right below headline
    y += 8
    sub_pos = _pos("subheading", C, {"x": pad, "y": y, "w": int((C - 2 * pad) * 0.68)})
    f_sub, sub_lines = _fit_font(
        d, copy.get("subheading", ""), "sans_reg",
        20, 15, sub_pos["w"], 70, max_lines=2, spacing=1.28,
    )
    _v2_draw_sub(
        d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"], primary_rgb, spacing=1.28,
    )

    # 6. Solid primary CTA — bottom-right corner of the card
    cta_h = 48
    f_cta = _font("sans_bold", 19)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 52
    cta_pos = _pos("cta", C, {
        "x": C - pad - btn_w,
        "y": C - pad - cta_h,
    })
    _v2_cta_solid(
        d, cta_pos["x"], cta_pos["y"], copy.get("cta", "Learn more"),
        primary_rgb, font_pt=19,
    )
    return canvas


# -----------------------------------------------------------------------------
# T9 V2 — ACCENT BAR (ported from v1 t4_accent_bar to V2 palette)
# Use case: branded hero with a strong vertical color anchor. Primary-color
# bar on the left holds the logo mark on a white chip; the rest of the
# canvas is pure white with product name + tagline, a rounded photo band,
# V2 two-tone headline/sub, and a solid CTA. Text areas stay white per
# user request so the lockup reads consistently with the other V2 templates.
# -----------------------------------------------------------------------------
def t9_v2_accent_bar(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)

    # 1. Vertical primary-color bar on the LEFT (decorative, full height).
    #    The logo mark NO LONGER sits in the bar — it goes into the white
    #    area next to product name + tagline, matching the V2 lockup style
    #    used by every other template (per user feedback).
    bar_w = 64
    d.rectangle([0, 0, bar_w, C], fill=primary_rgb)

    # 2. Right column — standard V2 lockup (mark + product_name + tagline)
    #    on white, then headline + sub + photo + CTA below.
    pad = 44
    content_x = bar_w + pad
    content_w = C - bar_w - pad * 2

    lockup = _v2_render_lockup(
        logo, copy.get("product_name"), copy.get("product_tagline"),
        content_w, 56, primary_rgb,
    )
    logo_pos = _pos("logo", C, {"x": content_x, "y": 42})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)
    lockup_bottom = logo_pos["y"] + lockup.size[1] + 18

    # Photo hero — rounded
    photo_top = lockup_bottom + 18
    photo_h = int(C * 0.38)
    photo_pos = _pos("photo", C, {
        "x": content_x, "y": photo_top, "w": content_w, "h": photo_h,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    mask = _rounded_mask(photo.size, 18)
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]), mask)

    # Headline — below photo
    head_top = photo_pos["y"] + photo_pos["h"] + 22
    head_pos = _pos("headline", C, {"x": content_x, "y": head_top, "w": content_w})
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   34, 22, head_pos["w"], 130,
                                   max_lines=2, spacing=1.14)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.14)
    y += 8
    sub_pos = _pos("subheading", C, {"x": content_x, "y": y,
                                     "w": int(content_w * 0.82)})
    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 26, 18, sub_pos["w"], 90,
                                 max_lines=2, spacing=1.28)
    _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                 primary_rgb, spacing=1.28)

    # Solid primary CTA bottom-right
    cta_h = 44
    f_cta = _font("sans_bold", 18)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 48
    cta_pos = _pos("cta", C, {
        "x": C - pad - btn_w, "y": C - pad - cta_h,
    })
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"],
                  copy.get("cta", "Learn more"), primary_rgb, font_pt=18)
    return canvas


# -----------------------------------------------------------------------------
# T10 V2 — FRAME (ported from v1 t7_frame to V2 palette)
# Use case: premium "framed" editorial — product launches, milestones,
# report covers. A thin primary-color border wraps the entire canvas;
# inside stays pure white so logo / product name / tagline / headline /
# sub / CTA all read cleanly against the V2 palette. Gives a certificate
# or magazine-cover feel without hiding the photo behind a heavy color.
# -----------------------------------------------------------------------------
def t10_v2_frame(bg, copy, logo, primary_rgb, C):
    # Start with a primary-color canvas, then overlay a white inset so only
    # a thin border of primary remains. Cleaner than drawing 4 rects.
    canvas = Image.new("RGB", (C, C), primary_rgb)
    border = 18
    white_inset = Image.new("RGB", (C - 2 * border, C - 2 * border), V2_WHITE)
    canvas.paste(white_inset, (border, border))
    d = ImageDraw.Draw(canvas)
    pad = border + 44  # outer padding = border + internal gutter

    # 1. Logo lockup (V2 style — mark + primary name + black tagline)
    lockup = _v2_render_lockup(
        logo, copy.get("product_name"), copy.get("product_tagline"),
        C - 2 * pad, 58, primary_rgb,
    )
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # 2. Headline right below the logo
    head_top = logo_pos["y"] + lockup.size[1] + 24
    head_pos = _pos("headline", C, {"x": pad, "y": head_top, "w": C - 2 * pad})
    f_head, head_lines = _fit_font(d, copy.get("headline", ""), "display",
                                   38, 24, head_pos["w"], 140,
                                   max_lines=2, spacing=1.14)
    y = _v2_draw_highlighted(d, head_lines, f_head, head_pos["x"], head_pos["y"],
                             copy.get("highlight_words"), primary_rgb, spacing=1.14)
    y += 10
    sub_pos = _pos("subheading", C, {"x": pad, "y": y, "w": int((C - 2 * pad) * 0.80)})
    f_sub, sub_lines = _fit_font(d, copy.get("subheading", ""), "sans_reg",
                                 27, 20, sub_pos["w"], 95,
                                 max_lines=2, spacing=1.30)
    y = _v2_draw_sub(d, sub_lines, f_sub, sub_pos["x"], sub_pos["y"],
                     primary_rgb, spacing=1.30)

    # 3. Photo middle — spans the FULL WIDTH of the white inset (flush with
    #    the inner edge of the orange border). Previously it was inset by
    #    another `pad` (44px) inside the frame which left visible white
    #    margins on each side — user wanted "no gray strip" around photos
    #    across all templates. Rounded mask dropped because rounded corners
    #    would carve white notches at the frame's inner edge.
    cta_h = 48
    photo_top = y + 20
    photo_bottom_limit = C - pad - cta_h - 20
    photo_h = photo_bottom_limit - photo_top
    photo_w = C - 2 * border
    photo_pos = _pos("photo", C, {
        "x": border, "y": photo_top, "w": photo_w, "h": photo_h,
    })
    photo = _v2_photo_fit(bg, photo_pos["w"], photo_pos["h"])
    canvas.paste(photo, (photo_pos["x"], photo_pos["y"]))

    # 4. Solid primary CTA bottom-right (inside the frame)
    f_cta = _font("sans_bold", 18)
    tw = d.textbbox((0, 0), copy.get("cta", "Learn more"), font=f_cta)
    btn_w = (tw[2] - tw[0]) + 48
    cta_pos = _pos("cta", C, {
        "x": C - pad - btn_w, "y": C - pad - cta_h,
    })
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"],
                  copy.get("cta", "Learn more"), primary_rgb, font_pt=18)
    return canvas


# -----------------------------------------------------------------------------
# T11 V2 — BIG STAT HERO
# Stat-driven announcement. Extracts the most prominent number/percent/dollar
# amount from headline or subheading and renders it as a massive hero numeral.
# References: Stripe ARR posts, HubSpot State-of-Marketing, YC fundraise posts.
# -----------------------------------------------------------------------------
import re as _re_t11


def _t11_extract_stat(text: str) -> tuple:
    """Find the most prominent 'stat' in `text`. Returns (stat_str, remainder).
    `remainder` is `text` with the stat removed so it can be rendered as the
    context line under the hero numeral.

    Priority (first match wins, scanning left-to-right):
      1. Currency w/ suffix:  $2.3M, $500K, $1.2B, $50,000
      2. Percentage:          92%, 3.5%
      3. Multiplier:          3x, 3.2x, 10×
      4. Integer w/ suffix:   10K, 5M users, 1B
      5. Integer > 99:        500, 10000

    No regex match → returns ("", text) so caller can fall back.
    """
    if not text:
        return "", ""
    patterns = [
        r"\$\s*\d+(?:[.,]\d+)?\s*[KkMmBb]?",      # $2.3M, $500K
        r"\d+(?:\.\d+)?\s*%",                       # 92%
        r"\d+(?:\.\d+)?\s*[xX×]",                   # 3.2x
        r"\d+(?:[.,]\d+)?\s*[KkMmBb]\b",          # 10K, 5M
        r"\b\d{3,}\b",                              # 500, 10000
    ]
    for pat in patterns:
        m = _re_t11.search(pat, text)
        if m:
            stat = m.group(0).strip()
            remainder = (text[:m.start()] + text[m.end():]).strip(" ,.;:—-")
            return stat, remainder
    return "", text


def t11_v2_big_stat(bg, copy, logo, primary_rgb, C):
    """T11 — Big Stat Hero. Massive numeral center, context label below,
    headline as small kicker on top, logo + CTA framed at edges.
    """
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    # Optional: paste the AI background as a very faint texture (8% opacity).
    # Keeps the canvas mostly clean white — the stat is the hero, not the bg.
    try:
        if bg is not None:
            bg_layer = _v2_photo_fit(bg, C, C).convert("RGBA")
            tint = Image.new("RGBA", (C, C), (255, 255, 255, 230))  # 90% white veil
            bg_layer = Image.alpha_composite(bg_layer, tint).convert("RGB")
            canvas = bg_layer
    except Exception:
        pass

    d = ImageDraw.Draw(canvas)
    pad = 56

    # Lockup top-left (brand name + tagline + logo mark)
    lockup = _v2_render_lockup(logo, copy.get("product_name"), copy.get("product_tagline"),
                               C - 2 * pad, 56, primary_rgb)
    logo_pos = _pos("logo", C, {"x": pad, "y": pad})
    canvas.paste(lockup, (logo_pos["x"], logo_pos["y"]), lockup)

    # Source the stat: headline first, then subheading.
    headline = (copy.get("headline") or "").strip()
    subheading = (copy.get("subheading") or "").strip()

    stat, hero_remainder = _t11_extract_stat(headline)
    context_line = hero_remainder
    secondary_line = subheading
    if not stat:
        stat, hero_remainder = _t11_extract_stat(subheading)
        if stat:
            # We pulled stat from subheading — use headline as the secondary
            # line (it never had a number) and the subheading remainder as
            # the context.
            context_line = hero_remainder
            secondary_line = headline
    if not stat:
        # No numeric stat anywhere — fall back to first 1-2 punchy words of
        # headline as the hero text. Template still renders cleanly but loses
        # its "stat" identity.
        words = headline.split()
        stat = " ".join(words[:2]) if words else "—"
        context_line = " ".join(words[2:])
        secondary_line = subheading

    # Hero stat — center, massive
    f_stat, _ = _fit_font(d, stat, "display",
                          340, 140, C - 2 * pad, int(C * 0.50),
                          max_lines=1, spacing=1.0)
    stat_bbox = d.textbbox((0, 0), stat, font=f_stat)
    stat_w = stat_bbox[2] - stat_bbox[0]
    stat_h = stat_bbox[3] - stat_bbox[1]
    stat_x = (C - stat_w) // 2 - stat_bbox[0]
    stat_y = int(C * 0.30)
    d.text((stat_x, stat_y), stat, font=f_stat, fill=primary_rgb)

    cursor = stat_y + stat_h + 18

    # Context line directly under the stat (small, MUTED, centered)
    if context_line:
        f_ctx, ctx_lines = _fit_font(d, context_line, "sans_reg",
                                     30, 20, int(C * 0.80), 80,
                                     max_lines=2, spacing=1.30)
        for line in ctx_lines:
            lb = d.textbbox((0, 0), line, font=f_ctx)
            lx = (C - (lb[2] - lb[0])) // 2 - lb[0]
            d.text((lx, cursor), line, font=f_ctx, fill=MUTED)
            cursor += int((f_ctx.getbbox("Ag")[3] - f_ctx.getbbox("Ag")[1]) * 1.30)

    # Secondary line (the subheading, or the headline if stat came from sub)
    if secondary_line:
        cursor += 18
        f_sec, sec_lines = _fit_font(d, secondary_line, "sans_bold",
                                     32, 22, int(C * 0.82), 90,
                                     max_lines=2, spacing=1.26)
        for line in sec_lines:
            lb = d.textbbox((0, 0), line, font=f_sec)
            lx = (C - (lb[2] - lb[0])) // 2 - lb[0]
            d.text((lx, cursor), line, font=f_sec, fill=INK)
            cursor += int((f_sec.getbbox("Ag")[3] - f_sec.getbbox("Ag")[1]) * 1.26)

    # CTA pill bottom-center
    cta_text = copy.get("cta") or "Learn more"
    f_cta = _font("sans_bold", 22)
    tw = d.textbbox((0, 0), cta_text, font=f_cta)
    cta_btn_w = (tw[2] - tw[0]) + 60
    cta_x = (C - cta_btn_w) // 2
    cta_y = C - pad - (22 * 2 + 18)
    cta_pos = _pos("cta", C, {"x": cta_x, "y": cta_y})
    _v2_cta_solid(d, cta_pos["x"], cta_pos["y"], cta_text, primary_rgb, font_pt=22)

    return canvas


# -----------------------------------------------------------------------------
# T31 V2 — ACADEMIC / EDTECH HERO
# Top white band (logo + bold colored headline + thin subheading), center
# full-bleed photo with bottom darkening + centered all-caps CTA, footer rule
# split into two color bands. Reference layout: university/business-school
# program promos (e.g. Schulich MBAN, Wharton EMBA, INSEAD MIM).
# -----------------------------------------------------------------------------
def _t31_lighten(rgb: tuple, amount: float = 0.45) -> tuple:
    """Mix `rgb` with white by `amount` (0=no change, 1=pure white)."""
    return tuple(int(c + (255 - c) * amount) for c in rgb)


def t31_v2_academic_hero(bg, copy, logo, primary_rgb, C):
    """T31 — Academic/EdTech hero. Top white band carries logo + bold colored
    headline + thin subheading. Middle 60% is the full-bleed photo with a
    bottom darkening gradient and a centered all-caps CTA. A two-band footer
    rule (dark/lightened brand color) caps the canvas.
    """
    canvas = Image.new("RGB", (C, C), V2_WHITE)
    d = ImageDraw.Draw(canvas)

    # ---------- Layout regions ----------
    pad = 56
    top_band_h = int(C * 0.30)        # white area
    footer_h   = int(C * 0.045)       # thin two-band rule at bottom
    photo_top  = top_band_h
    photo_bot  = C - footer_h
    photo_h    = photo_bot - photo_top

    # ---------- Center photo ----------
    if bg is not None:
        photo = _v2_photo_fit(bg, C, photo_h)
        canvas.paste(photo, (0, photo_top))
    else:
        d.rectangle([0, photo_top, C, photo_bot], fill=(40, 40, 40))

    # Bottom darkening gradient over photo so CTA reads cleanly.
    grad = Image.new("RGBA", (C, photo_h), (0, 0, 0, 0))
    grad_px = grad.load()
    grad_start = int(photo_h * 0.55)
    for yy in range(grad_start, photo_h):
        t = (yy - grad_start) / max(1, (photo_h - grad_start))
        a = int(190 * t)
        for xx in range(C):
            grad_px[xx, yy] = (0, 0, 0, a)
    photo_layer = canvas.crop((0, photo_top, C, photo_bot)).convert("RGBA")
    photo_layer = Image.alpha_composite(photo_layer, grad).convert("RGB")
    canvas.paste(photo_layer, (0, photo_top))
    d = ImageDraw.Draw(canvas)  # rebind after paste

    # ---------- Two-band footer rule ----------
    light = _t31_lighten(primary_rgb, 0.40)
    split_x = int(C * 0.42)
    d.rectangle([0, photo_bot, split_x, C], fill=primary_rgb)
    d.rectangle([split_x, photo_bot, C, C], fill=light)

    # ---------- Top band content ----------
    # Logo centered at the top (preserve aspect; cap at ~84px tall).
    if logo is not None:
        logo_max_h = 84
        try:
            lw, lh = logo.size
            new_w = max(1, int(lw * logo_max_h / lh))
            lg = logo.resize((new_w, logo_max_h), Image.LANCZOS)
            lx = (C - new_w) // 2
            ly = 28
            canvas.paste(lg, (lx, ly), lg if lg.mode == "RGBA" else None)
        except Exception:
            pass

    # Headline — bold, centered, primary color, fits within top white band.
    head = (copy.get("headline") or "").upper()
    if head:
        head_max_w = C - 2 * pad
        head_max_h = int(top_band_h * 0.42)
        f_head, head_lines = _fit_font(d, head, "display",
                                       46, 28, head_max_w, head_max_h,
                                       max_lines=3, spacing=1.12)
        line_h = int((f_head.getbbox("Ag")[3] - f_head.getbbox("Ag")[1]) * 1.12)
        head_total_h = line_h * len(head_lines)
        # Position headline below logo, centered horizontally.
        y_cursor = 28 + 84 + 18
        for line in head_lines:
            lb = d.textbbox((0, 0), line, font=f_head)
            lx = (C - (lb[2] - lb[0])) // 2 - lb[0]
            d.text((lx, y_cursor), line, font=f_head, fill=primary_rgb)
            y_cursor += line_h

        # Subheading directly under headline.
        sub = (copy.get("subheading") or "")
        if sub:
            f_sub, sub_lines = _fit_font(d, sub, "sans_reg",
                                         24, 16, head_max_w, 80,
                                         max_lines=2, spacing=1.30)
            for line in sub_lines:
                lb = d.textbbox((0, 0), line, font=f_sub)
                lx = (C - (lb[2] - lb[0])) // 2 - lb[0]
                d.text((lx, y_cursor + 6), line, font=f_sub, fill=INK)
                y_cursor += int((f_sub.getbbox("Ag")[3] - f_sub.getbbox("Ag")[1]) * 1.30)

    # ---------- CTA — white all-caps centered over photo darkening ----------
    cta = (copy.get("cta") or "").upper()
    if cta:
        cta_max_w = C - 2 * pad
        f_cta, cta_lines = _fit_font(d, cta, "display",
                                     38, 22, cta_max_w, 90,
                                     max_lines=2, spacing=1.16)
        line_h_c = int((f_cta.getbbox("Ag")[3] - f_cta.getbbox("Ag")[1]) * 1.16)
        cta_total_h = line_h_c * len(cta_lines)
        cta_y = photo_bot - 36 - cta_total_h
        for line in cta_lines:
            lb = d.textbbox((0, 0), line, font=f_cta)
            lx = (C - (lb[2] - lb[0])) // 2 - lb[0]
            d.text((lx, cta_y), line, font=f_cta, fill=V2_WHITE)
            cta_y += line_h_c

    return canvas


# -----------------------------------------------------------------------------
# T32 V2 — SPLIT CARD WITH PHOTO RIGHT (Rimberio-style)
# Light grey left zone with logo / bold headline / subheading / gradient CTA
# pill + circular arrow. Right zone holds a rounded-corner photo. Far-right
# edge has a thin vertical gradient strip in the brand primary color.
# Reference: Canva "Split card with photo" / Rimberio expense-AI promo.
# -----------------------------------------------------------------------------
def _t32_paste_rounded(canvas, photo, x, y, w, h, radius=24):
    """Paste `photo` (already-fit PIL Image) onto canvas at (x, y) with a
    rounded-corner mask of `radius` px. `photo` must be RGB or RGBA."""
    if photo.size != (w, h):
        photo = photo.resize((w, h), Image.LANCZOS)
    photo_rgba = photo.convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    canvas.paste(photo_rgba, (x, y), mask)


def t32_v2_split_card_photo(bg, copy, logo, primary_rgb, C):
    """T32 — Split card layout with logo + headline + subheading + gradient
    CTA pill on the left grey zone, rounded photo on the right, and a thin
    vertical primary-color gradient strip on the far-right edge."""
    BG_GREY = (240, 241, 243)
    canvas = Image.new("RGB", (C, C), BG_GREY)
    d = ImageDraw.Draw(canvas)

    pad = 48
    # ---------- Right edge gradient strip ----------
    strip_w = int(C * 0.045)
    strip_x0 = C - strip_w
    light_primary = _t31_lighten(primary_rgb, 0.55)
    for yy in range(C):
        t = yy / max(1, C - 1)
        r = int(primary_rgb[0] * (1 - t) + light_primary[0] * t)
        g = int(primary_rgb[1] * (1 - t) + light_primary[1] * t)
        b = int(primary_rgb[2] * (1 - t) + light_primary[2] * t)
        d.line([(strip_x0, yy), (C, yy)], fill=(r, g, b))

    # ---------- Right photo zone (rounded) ----------
    photo_w = int(C * 0.38)
    photo_h = int(C * 0.74)
    photo_x = C - strip_w - 28 - photo_w
    photo_y = (C - photo_h) // 2
    if bg is not None:
        photo = _v2_photo_fit(bg, photo_w, photo_h)
        _t32_paste_rounded(canvas, photo, photo_x, photo_y, photo_w, photo_h, radius=22)
    d = ImageDraw.Draw(canvas)  # rebind after paste

    # Left zone right edge (where text wraps)
    left_w = photo_x - pad - 24

    # ---------- Logo lockup top-left ----------
    if logo is not None:
        logo_max_h = 56
        try:
            lw, lh = logo.size
            new_w = max(1, int(lw * logo_max_h / lh))
            lg = logo.resize((new_w, logo_max_h), Image.LANCZOS)
            canvas.paste(lg, (pad, pad), lg if lg.mode == "RGBA" else None)
            # Brand wordmark to the right of the logo mark, if product_name present
            name = (copy.get("product_name") or "").strip()
            if name:
                f_name = _font("sans_bold", 30)
                d.text((pad + new_w + 16, pad + (logo_max_h - 32) // 2),
                       name, font=f_name, fill=DARK_INK)
        except Exception:
            pass

    # ---------- Headline (bold sans display) ----------
    headline = (copy.get("headline") or "").strip()
    head_y = pad + 56 + 84  # below logo with breathing room
    if headline:
        f_head, head_lines = _fit_font(d, headline, "display",
                                       62, 36, left_w, int(C * 0.45),
                                       max_lines=4, spacing=1.10)
        line_h = int((f_head.getbbox("Ag")[3] - f_head.getbbox("Ag")[1]) * 1.10)
        for line in head_lines:
            d.text((pad, head_y), line, font=f_head, fill=DARK_INK)
            head_y += line_h

    # ---------- Subheading ----------
    sub = (copy.get("subheading") or "").strip()
    sub_y = head_y + 18
    if sub:
        f_sub, sub_lines = _fit_font(d, sub, "sans_reg",
                                     22, 16, left_w, 110,
                                     max_lines=3, spacing=1.36)
        line_h_s = int((f_sub.getbbox("Ag")[3] - f_sub.getbbox("Ag")[1]) * 1.36)
        for line in sub_lines:
            d.text((pad, sub_y), line, font=f_sub, fill=MUTED)
            sub_y += line_h_s

    # ---------- Gradient CTA pill + circular arrow ----------
    cta = (copy.get("cta") or "Learn more").strip()
    f_cta = _font("sans_bold", 18)
    cta_bbox = d.textbbox((0, 0), cta, font=f_cta)
    pill_w = (cta_bbox[2] - cta_bbox[0]) + 60
    pill_h = 48
    pill_x = pad
    pill_y = sub_y + 30
    # Bound the pill to the left zone
    if pill_x + pill_w + 64 > photo_x - 16:
        pill_w = (photo_x - 16) - pill_x - 64

    # Build a horizontal gradient pill: dark primary → lightened primary
    grad_img = Image.new("RGB", (pill_w, pill_h), primary_rgb)
    gd = ImageDraw.Draw(grad_img)
    end_color = _t31_lighten(primary_rgb, 0.30)
    for xx in range(pill_w):
        t = xx / max(1, pill_w - 1)
        r = int(primary_rgb[0] * (1 - t) + end_color[0] * t)
        g = int(primary_rgb[1] * (1 - t) + end_color[1] * t)
        b = int(primary_rgb[2] * (1 - t) + end_color[2] * t)
        gd.line([(xx, 0), (xx, pill_h)], fill=(r, g, b))
    # Round the pill via mask
    pill_mask = Image.new("L", (pill_w, pill_h), 0)
    pm = ImageDraw.Draw(pill_mask)
    pm.rounded_rectangle([0, 0, pill_w, pill_h], radius=pill_h // 2, fill=255)
    canvas.paste(grad_img, (pill_x, pill_y), pill_mask)
    d = ImageDraw.Draw(canvas)
    # CTA text
    asc, desc = f_cta.getmetrics()
    d.text((pill_x + 24, pill_y + (pill_h - (asc + desc)) // 2),
           cta, font=f_cta, fill=V2_WHITE)

    # Circular arrow button next to the pill
    btn_d = pill_h
    btn_x = pill_x + pill_w + 12
    btn_y = pill_y
    arrow_color = _t31_lighten(primary_rgb, 0.10)
    d.ellipse([btn_x, btn_y, btn_x + btn_d, btn_y + btn_d], fill=arrow_color)
    cx = btn_x + btn_d // 2
    cy = btn_y + btn_d // 2
    arrow_size = btn_d // 4
    d.polygon(
        [(cx - arrow_size // 2, cy - arrow_size // 2),
         (cx + arrow_size // 2, cy),
         (cx - arrow_size // 2, cy + arrow_size // 2)],
        fill=V2_WHITE,
    )

    return canvas


# Active templates: V2 social-media set (April 2026).
# Old t1_top_band / t2_vsplit_left / t4_accent_bar / t11_cta_strip /
# t12_brand_card renderers remain above as dead code — re-enable any by
# listing in the registry.
# =============================================================================
# Batch 1 — Freeform-ported PIL templates (May 2026)
# Layout-friendly ports of the freeform AGENT_SYSTEM library: TFP (T-P
# meme), TFK (T-K editorial UI), TFL (T-L consultancy cover), TFQ (T-Q
# dark launch w/ laptop). Each takes the same (bg, copy, logo, primary_rgb,
# C) signature as the rest of the V2 PIL templates so compose_all picks
# them up automatically. PIL builds layout + text + logo; bg comes from
# the upstream Gemini image-gen.
# =============================================================================


def _ff_paste_logo_corner(canvas, logo, x, y, max_h=64):
    """Paste logo image as a small attribution badge at (x, y).
    Preserves aspect ratio. Used by TFP for the corner attribution badge.
    """
    if logo is None:
        return
    try:
        lw, lh = logo.size
        if lh <= 0:
            return
        new_w = max(1, int(lw * max_h / lh))
        lg = logo.resize((new_w, max_h), Image.LANCZOS)
        canvas.paste(lg, (x, y), lg if lg.mode == "RGBA" else None)
    except Exception:
        pass


def _ff_outline_text(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font,
                    fill=(255, 255, 255), stroke=(0, 0, 0), stroke_w=3):
    """Draw text with a black drop-shadow stroke — for meme-style
    white-on-anything labels (TFP)."""
    d.text((x, y), text, font=font, fill=fill, stroke_width=stroke_w,
           stroke_fill=stroke)


def tfp_two_panel_meme(bg, copy, logo, primary_rgb, C):
    """TFP — Two-panel meme. Top panel + bottom panel, each gets the SAME
    background photo (or two halves of it), with bold uppercase
    white-with-black-stroke labels at the top of each panel. Logo is a
    tiny attribution badge at the bottom-right of the bottom panel.

    `copy["headline"]` → top label (e.g. "WITHOUT SPENZO")
    `copy["subheading"]` → bottom label (e.g. "WITH SPENZO")
    """
    canvas = Image.new("RGB", (C, C), V2_BLACK)
    half = C // 2
    # Slot the bg photo in BOTH panels (top + bottom). The Gemini-rendered
    # background should show the contrast naturally; PIL just overlays
    # the labels and the brand badge.
    if bg is not None:
        photo = _v2_photo_fit(bg, C, C)
        # Top half = top half of photo, bottom half = bottom half.
        canvas.paste(photo.crop((0, 0, C, half)), (0, 0))
        canvas.paste(photo.crop((0, half, C, C)), (0, half))
    d = ImageDraw.Draw(canvas)
    # Thin divider line between panels
    d.line([(0, half), (C, half)], fill=(0, 0, 0), width=2)

    # Top label (uppercase, centered, white with black stroke)
    top_label = (copy.get("headline") or "WITHOUT").upper()
    bot_label = (copy.get("subheading") or "WITH").upper()
    f_label, _ = _fit_font(d, top_label, "display", 56, 28,
                           int(C * 0.85), 80, max_lines=1, spacing=1.0)
    f_label2, _ = _fit_font(d, bot_label, "display", 56, 28,
                            int(C * 0.85), 80, max_lines=1, spacing=1.0)

    def _center_label(label, font, y):
        b = d.textbbox((0, 0), label, font=font)
        x = (C - (b[2] - b[0])) // 2 - b[0]
        _ff_outline_text(d, x, y, label, font, fill=V2_WHITE,
                         stroke=V2_BLACK, stroke_w=4)

    _center_label(top_label, f_label, 18)
    _center_label(bot_label, f_label2, half + 18)

    # Brand attribution badge — bottom-right of bottom panel
    _ff_paste_logo_corner(canvas, logo, x=C - 72 - 24, y=C - 72 - 24, max_h=72)
    return canvas


def tfk_editorial_ui_showcase(bg, copy, logo, primary_rgb, C):
    """TFK — Editorial UI showcase (cream canvas, "New" eyebrow, bold
    headline, photo zone bottom 65%). Decorative line in brand primary
    cuts across the layout.

    `copy["headline"]` → big top-zone headline
    `copy["subheading"]` → small line under headline (optional)
    """
    CREAM = (250, 248, 244)
    canvas = Image.new("RGB", (C, C), CREAM)
    d = ImageDraw.Draw(canvas)
    pad = 56
    top_band_h = int(C * 0.32)

    # Logo + wordmark top-left
    if logo is not None:
        try:
            lw, lh = logo.size
            new_w = max(1, int(lw * 56 / lh))
            lg = logo.resize((new_w, 56), Image.LANCZOS)
            canvas.paste(lg, (pad, pad), lg if lg.mode == "RGBA" else None)
            name = (copy.get("product_name") or "").strip()
            if name:
                f_name = _font("sans_bold", 26)
                d.text((pad + new_w + 14, pad + 12),
                       name, font=f_name, fill=DARK_INK)
        except Exception:
            pass

    # "New" eyebrow in primary color (italic-ish)
    f_eb = _font("serif_bold", 18)
    d.text((pad, pad + 80), "New", font=f_eb, fill=primary_rgb)

    # Big headline below the eyebrow
    headline = (copy.get("headline") or "").strip()
    head_y = pad + 110
    f_head, head_lines = _fit_font(d, headline, "display",
                                   54, 32, C - 2 * pad,
                                   top_band_h - 110, max_lines=2, spacing=1.10)
    line_h = int((f_head.getbbox("Ag")[3] - f_head.getbbox("Ag")[1]) * 1.10)
    for line in head_lines:
        d.text((pad, head_y), line, font=f_head, fill=DARK_INK)
        head_y += line_h

    # Subheading directly under headline
    sub = (copy.get("subheading") or "").strip()
    if sub and head_y + 50 < top_band_h:
        f_sub, sub_lines = _fit_font(d, sub, "sans_reg",
                                     20, 14, C - 2 * pad, 50,
                                     max_lines=2, spacing=1.30)
        for line in sub_lines:
            d.text((pad, head_y + 8), line, font=f_sub, fill=MUTED)
            head_y += int((f_sub.getbbox("Ag")[3] - f_sub.getbbox("Ag")[1]) * 1.30)

    # Decorative wavy line accent across the canvas
    wave_y = top_band_h + 12
    d.line([(0, wave_y), (C, wave_y)], fill=primary_rgb, width=3)

    # Bottom photo zone (~65%) full-width
    photo_top = top_band_h + 24
    photo_h = C - photo_top
    if bg is not None:
        photo = _v2_photo_fit(bg, C, photo_h)
        canvas.paste(photo, (0, photo_top))
    return canvas


def _ff_draw_sketchy_decor(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
                           ink=(20, 20, 30)):
    """Draw a few sketchy dark-ink decorative marks (small 4-point stars +
    short curved arrows + dots) in the (x, y, w, h) rectangle. Used as
    the top-right "doodle" decoration in TFB / editorial templates.
    """
    import random as _r
    _r.seed(7)  # deterministic across renders
    # 6-8 small 4-point stars scattered in the box
    for _ in range(7):
        cx = x + _r.randint(0, w)
        cy = y + _r.randint(0, h)
        s = _r.choice([3, 4, 5, 6])
        d.line([(cx - s, cy), (cx + s, cy)], fill=ink, width=2)
        d.line([(cx, cy - s), (cx, cy + s)], fill=ink, width=2)
    # A thin curved arrow line (short arc)
    arc_x = x + w // 4
    arc_y = y + h // 2
    d.arc([arc_x, arc_y, arc_x + 60, arc_y + 50], start=0, end=180,
          fill=ink, width=2)
    # 4-5 small dots
    for _ in range(5):
        px = x + _r.randint(0, w)
        py = y + _r.randint(0, h)
        d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=ink)


def tfb_editorial_product_ui(bg, copy, logo, primary_rgb, C):
    """TFB — Editorial Product UI showcase. Cream canvas with brand
    lockup top-left (logo + product name + tagline beside it), "New"
    italic eyebrow, big bold UPPERCASE headline, subhead, sketchy
    chart/arrow/gear decoration top-right, and the AI-generated macOS
    window screenshot embedded as a photo zone at the bottom (the bg
    image itself already contains the mac window — we just inset it).

    `copy["product_name"]`    → bold wordmark beside the logo
    `copy["product_tagline"]` → small grey line under the wordmark
    `copy["headline"]`        → big uppercase headline
    `copy["subheading"]`      → small body line under the headline
    """
    CREAM = (250, 244, 234)
    canvas = Image.new("RGB", (C, C), CREAM)
    d = ImageDraw.Draw(canvas)
    pad = 56

    # ── Logo + product-name + tagline lockup top-left ────────────────
    logo_h = 72
    logo_w = 0
    if logo is not None:
        try:
            lw, lh = logo.size
            logo_w = max(1, int(lw * logo_h / lh))
            lg = logo.resize((logo_w, logo_h), Image.LANCZOS)
            canvas.paste(lg, (pad, pad), lg if lg.mode == "RGBA" else None)
        except Exception:
            pass

    name = (copy.get("product_name") or "").strip()
    tagline = (copy.get("product_tagline") or "").strip()
    text_x = pad + logo_w + 16
    if name:
        f_name = _font("sans_bold", 30)
        d.text((text_x, pad + 6), name, font=f_name, fill=DARK_INK)
    if tagline:
        f_tag = _font("sans_reg", 16)
        # truncate to one line if needed
        max_w = C - text_x - pad - int(C * 0.30)
        tag_line = tagline
        while tag_line and d.textbbox((0, 0), tag_line, font=f_tag)[2] > max_w:
            tag_line = tag_line[:-1]
        if tag_line != tagline and len(tag_line) > 3:
            tag_line = tag_line[:-1] + "…"
        d.text((text_x, pad + 42), tag_line, font=f_tag, fill=(80, 80, 95))

    # ── Top-right sketchy decoration (charts + arrows + gears) ───────
    decor_x = int(C * 0.62)
    decor_y = pad
    decor_w = C - decor_x - pad
    decor_h = int(C * 0.22)
    _ff_draw_sketchy_decor(d, decor_x, decor_y, decor_w, decor_h)

    # ── "New" italic eyebrow below the lockup ─────────────────────────
    f_eb = _font("serif_bold", 22)
    eb_y = pad + logo_h + 12
    d.text((pad, eb_y), "New", font=f_eb, fill=(40, 40, 50))

    # ── BIG UPPERCASE headline ───────────────────────────────────────
    headline = (copy.get("headline") or "").upper().strip()
    head_y = eb_y + 32
    head_max_h = int(C * 0.16)
    f_head, head_lines = _fit_font(d, headline, "display",
                                   66, 36, C - 2 * pad,
                                   head_max_h, max_lines=2, spacing=1.05)
    line_h = int((f_head.getbbox("Ag")[3] - f_head.getbbox("Ag")[1]) * 1.05)
    for line in head_lines:
        d.text((pad, head_y), line, font=f_head, fill=DARK_INK)
        head_y += line_h

    # ── Subheading directly under headline ───────────────────────────
    sub = (copy.get("subheading") or "").strip()
    if sub:
        f_sub, sub_lines = _fit_font(d, sub, "sans_reg",
                                     22, 14, C - 2 * pad, 70,
                                     max_lines=2, spacing=1.30)
        for line in sub_lines:
            d.text((pad, head_y + 8), line, font=f_sub, fill=(60, 60, 75))
            head_y += int((f_sub.getbbox("Ag")[3] - f_sub.getbbox("Ag")[1]) * 1.30)

    # ── Bottom photo zone — the bg already contains the mac window ──
    photo_top = max(head_y + 28, int(C * 0.46))
    photo_h = C - photo_top - 24
    photo_w = C - 24
    if bg is not None and photo_h > 80:
        photo = _v2_photo_fit(bg, photo_w, photo_h)
        mask = Image.new("L", (photo_w, photo_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, photo_w, photo_h], radius=18, fill=255)
        canvas.paste(photo, (12, photo_top), mask)

    return canvas


def tfl_consultancy_cover(bg, copy, logo, primary_rgb, C):
    """TFL — Consultancy report cover (cream/warm-white, kicker + big
    serif title, photo zone bottom 50%).

    `copy["headline"]` → KICKER context line (e.g. "How telecoms can win in the age of AI")
    `copy["subheading"]` → BIG SERIF report title (multi-line)
    """
    CREAM = (244, 242, 238)
    canvas = Image.new("RGB", (C, C), CREAM)
    d = ImageDraw.Draw(canvas)
    pad = 56

    # Logo + product-name wordmark top-left (mandatory pair — wherever
    # the logo appears, the product name must appear too).
    logo_h = 64
    logo_w = 0
    if logo is not None:
        try:
            lw, lh = logo.size
            logo_w = max(1, int(lw * logo_h / lh))
            lg = logo.resize((logo_w, logo_h), Image.LANCZOS)
            canvas.paste(lg, (pad, pad), lg if lg.mode == "RGBA" else None)
        except Exception:
            pass
    name = (copy.get("product_name") or "").strip()
    if name:
        f_name = _font("sans_bold", 26)
        d.text((pad + logo_w + 14, pad + 18), name, font=f_name, fill=DARK_INK)

    # Kicker (sans, smaller, dark slate)
    kicker = (copy.get("headline") or "").strip()
    kicker_y = pad + 100
    if kicker:
        f_k, k_lines = _fit_font(d, kicker, "sans_bold",
                                 28, 18, C - 2 * pad, 80,
                                 max_lines=2, spacing=1.20)
        for line in k_lines:
            d.text((pad, kicker_y), line, font=f_k, fill=DARK_INK)
            kicker_y += int((f_k.getbbox("Ag")[3] - f_k.getbbox("Ag")[1]) * 1.20)

    # Big serif title (multi-line)
    title = (copy.get("subheading") or "").strip()
    if title:
        title_y = kicker_y + 14
        max_h = int(C * 0.50) - title_y
        f_t, t_lines = _fit_font(d, title, "serif_bold",
                                 60, 32, C - 2 * pad, max_h,
                                 max_lines=4, spacing=1.10)
        line_h = int((f_t.getbbox("Ag")[3] - f_t.getbbox("Ag")[1]) * 1.10)
        for line in t_lines:
            d.text((pad, title_y), line, font=f_t, fill=DARK_INK)
            title_y += line_h

    # Bottom 50% photo zone, full-width
    photo_top = int(C * 0.50)
    photo_h = C - photo_top
    if bg is not None:
        photo = _v2_photo_fit(bg, C, photo_h)
        canvas.paste(photo, (0, photo_top))
    return canvas


def tfq_dark_launch_laptop(bg, copy, logo, primary_rgb, C):
    """TFQ — Dark landscape product launch with laptop (dark canvas, brand
    lockup top-left, INTRODUCING eyebrow + headline + sub on the left
    ~40% of the canvas, photo zone right ~55-60%).

    `copy["headline"]` → big launch product name (e.g. "Builders Club")
    `copy["subheading"]` → small grey description line
    """
    DARK = (10, 10, 14)
    GREY = (156, 163, 175)
    canvas = Image.new("RGB", (C, C), DARK)
    d = ImageDraw.Draw(canvas)
    pad = 48
    left_w = int(C * 0.42)

    # Right-side photo zone (right 55-60%, with a left margin so the
    # photo is fully visible inside the canvas, no edge bleed).
    photo_left = left_w + 20
    photo_w = C - photo_left - 24
    photo_h = int(C * 0.78)
    photo_top = (C - photo_h) // 2
    if bg is not None:
        photo = _v2_photo_fit(bg, photo_w, photo_h)
        # Round corners via mask
        mask = Image.new("L", (photo_w, photo_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, photo_w, photo_h], radius=18, fill=255)
        canvas.paste(photo, (photo_left, photo_top), mask)
    d = ImageDraw.Draw(canvas)

    # Logo + wordmark top-left of LEFT zone
    name = (copy.get("product_name") or "").strip()
    logo_h = 56
    logo_w = 0
    if logo is not None:
        try:
            lw, lh = logo.size
            logo_w = max(1, int(lw * logo_h / lh))
            lg = logo.resize((logo_w, logo_h), Image.LANCZOS)
            canvas.paste(lg, (pad, pad), lg if lg.mode == "RGBA" else None)
        except Exception:
            pass
    if name:
        f_name = _font("sans_bold", 28)
        d.text((pad + logo_w + 14, pad + 14),
               name, font=f_name, fill=V2_WHITE)

    # INTRODUCING eyebrow
    eb_y = pad + logo_h + 60
    f_eb = _font("sans_bold", 14)
    d.text((pad, eb_y), "INTRODUCING", font=f_eb, fill=V2_WHITE)

    # Big white headline
    head = (copy.get("headline") or "").strip()
    head_y = eb_y + 28
    if head:
        f_h, h_lines = _fit_font(d, head, "display",
                                 50, 30, left_w - pad, int(C * 0.30),
                                 max_lines=3, spacing=1.10)
        line_h = int((f_h.getbbox("Ag")[3] - f_h.getbbox("Ag")[1]) * 1.10)
        for line in h_lines:
            d.text((pad, head_y), line, font=f_h, fill=V2_WHITE)
            head_y += line_h

    # Small grey description
    sub = (copy.get("subheading") or "").strip()
    if sub:
        f_s, s_lines = _fit_font(d, sub, "sans_reg",
                                 18, 13, left_w - pad, 110,
                                 max_lines=4, spacing=1.30)
        for line in s_lines:
            d.text((pad, head_y + 12), line, font=f_s, fill=GREY)
            head_y += int((f_s.getbbox("Ag")[3] - f_s.getbbox("Ag")[1]) * 1.30)

    return canvas


TEMPLATE_REGISTRY = {
    "T1": t1_v2_header_band,
    "T2": t2_v2_full_bleed,
    "T3": t3_v2_quote_card,
    "T5": t5_v2_brand_card,
    "T6": t6_v2_webinar,
    "T7": t7_v2_poster,
    "T9": t9_v2_accent_bar,
    "T10": t10_v2_frame,
    # Batch 1 — Freeform-ported PIL templates (May 2026). 4 new layouts
    # that match the freeform AGENT_SYSTEM library's design intent for
    # T-K (editorial UI showcase), T-L (consultancy cover), T-P (meme),
    # T-Q (dark launch laptop). Each takes the same (bg, copy, logo,
    # primary_rgb, C) signature so compose_all picks them up.
    "TFB": tfb_editorial_product_ui,
    "TFK": tfk_editorial_ui_showcase,
    "TFL": tfl_consultancy_cover,
    # T11 (big stat hero), T31 (academic hero), T32 (split card photo),
    # TFP (two-panel meme), TFQ (dark launch laptop) removed from active
    # set May 2026 — definitions still above so they can be re-enabled
    # by re-adding registry lines.
}
TEMPLATE_IDS = list(TEMPLATE_REGISTRY.keys())


# =============================================================================
# Public entrypoint
# =============================================================================

def compose(
    bg_bytes: bytes,
    copy: dict,
    logo_bytes: Optional[bytes],
    primary_hex: str,
    template_id: str,
    canvas_size: int = DEFAULT_CANVAS,
    overrides: Optional[dict] = None,
) -> bytes:
    """Compose one finished template image.

    `overrides` is an optional user-template customization dict — see
    `apply_overrides()` for the schema. When provided, keys like
    `primary_color`, `accent_words`, `headline_color`, `subheading_color`,
    `cta_style` swap into the render. Missing keys fall through to each
    template's defaults.

    Returns JPEG bytes. Raises ValueError on unknown template_id.
    """
    if template_id not in TEMPLATE_REGISTRY:
        raise ValueError(f"Unknown template_id: {template_id}. Available: {TEMPLATE_IDS}")

    style = apply_overrides(overrides) if overrides else {}
    # primary_color override wins over the arg
    if style.get("primary_color"):
        primary_hex = style["primary_color"]
    # Merge accent_words into the copy dict so `_v2_draw_highlighted` tints
    # the right words without needing to know about user templates.
    #
    # IMPORTANT: template's static accent_words (e.g. ["Scale", "One"]) only
    # apply when at least one of those words actually appears in the current
    # campaign's headline — otherwise they'd be silently dropped and no tint
    # would show. When there's no match, we fall back to:
    #   (a) the LLM's own `highlight_words` from overlay_copy, if set
    #   (b) an auto-pick of the first + longest word as a last resort
    # so users who save a template with fixed accents still see SOME tint
    # on every campaign rather than a dead layout.
    # User-requested behaviour (April 2026): every headline + subheading gets
    # a two-tone mix where each non-trivial word is EITHER the primary color
    # OR the base (black on light bg, white on dark) — picked pseudo-randomly
    # but SEEDED by the text itself so the same copy always renders the same
    # pattern (no flickering on re-render). Template's explicit accent_words
    # still force those words to primary; the rest mix 50/50 between primary
    # and base.
    import random as _rand
    import hashlib as _hashlib

    def _auto_highlight_words(text: str, fixed_accents: list | None, ratio: float = 0.5) -> list[str]:
        if not text:
            return list(fixed_accents or [])
        raw = [w for w in text.split() if w.strip()]
        seed = int(_hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = _rand.Random(seed)
        forced = {w.strip(".,;:!?-").lower() for w in (fixed_accents or [])}
        picks: list[str] = []
        for w in raw:
            clean = w.strip(".,;:!?-")
            lw = clean.lower()
            if not clean:
                continue
            # Force explicit template accents.
            if lw in forced:
                picks.append(clean)
                continue
            # Skip tiny function words (a, an, the, of, to, ...) from tinting
            if len(clean) < 3:
                continue
            if rng.random() < ratio:
                picks.append(clean)
        return picks

    copy = dict(copy or {})
    copy["highlight_words"] = _auto_highlight_words(
        copy.get("headline") or "", style.get("accent_words"), ratio=0.35,
    )
    # Subheading auto-tinting DISABLED — user prefers sub in single pure
    # black color, no primary mix. `_v2_draw_sub` now always renders black.
    style.pop("sub_accent_words", None)

    bg = Image.open(BytesIO(bg_bytes)).convert("RGB")
    primary_rgb = _hex_to_rgb(primary_hex)
    logo_img = _load_logo_image(logo_bytes, 160, 60)
    fn = TEMPLATE_REGISTRY[template_id]
    token = _STYLE_CTX.set(style)
    try:
        result = fn(bg, copy, logo_img, primary_rgb, canvas_size)
    finally:
        _STYLE_CTX.reset(token)
    buf = BytesIO()
    result.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def compose_all(
    bg_bytes: bytes,
    copy: dict,
    logo_bytes: Optional[bytes],
    primary_hex: str,
    canvas_size: int = DEFAULT_CANVAS,
    overrides: Optional[dict] = None,
    template_ids: Optional[list] = None,
) -> dict:
    """Render templates for one background.

    If `template_ids` is provided, only those templates are rendered (in
    the given order). Unknown ids are skipped silently. If None, falls
    back to the full active TEMPLATE_IDS set.
    Returns {template_id: jpeg_bytes}.
    """
    tids = [t for t in (template_ids or TEMPLATE_IDS) if t in TEMPLATE_REGISTRY]
    out = {}
    for tid in tids:
        try:
            out[tid] = compose(bg_bytes, copy, logo_bytes, primary_hex, tid,
                               canvas_size, overrides=overrides)
        except Exception as exc:
            logger.exception("Template %s failed: %s", tid, exc)
    return out