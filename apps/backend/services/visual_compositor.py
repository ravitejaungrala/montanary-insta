"""
Pipelyt Visual Compositor
Composites logo, heading, sub-heading, and CTA pill onto a background image.
All rendering is deterministic (PIL font engine) — zero AI-generated typos.

Supports 4 text zones:
  TOP_CENTER_BAND  — full-width top strip
  TOP_LEFT_PANEL   — left ~50% top area
  TOP_RIGHT_PANEL  — right ~50% top area
  LEFT_GLASS_CARD  — tall frosted glass panel on the left side
"""
import os
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("pipelyt.compositor")

# Font resolution — Roboto bundled in static/fonts, system fonts as fallback
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "Roboto-Bold.ttf")
_FONT_REG_PATH = os.path.join(_FONT_DIR, "Roboto-Regular.ttf")

_SYSTEM_FONTS_BOLD = [
    "/c/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_SYSTEM_FONTS_REG = [
    "/c/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

CANVAS_W = 1024
CANVAS_H = 1024


def _get_font(style: str, size: int) -> ImageFont.ImageFont:
    candidates = [_FONT_BOLD_PATH] + _SYSTEM_FONTS_BOLD if style == 'bold' else [_FONT_REG_PATH] + _SYSTEM_FONTS_REG
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    try:
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (255, 69, 0)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except AttributeError:
        return int(font.getlength(text))


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if _text_w(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _shadow_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, fill: tuple, shadow_offset: int = 3):
    """Draw text with a drop shadow for readability over any background."""
    shadow = (0, 0, 0, 180)
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow)
    draw.text((x - shadow_offset, y + shadow_offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_heading(
    draw: ImageDraw.ImageDraw,
    x: int, y: int,
    heading: str,
    highlight_words: list,
    font,
    primary_rgb: tuple,
    line_h: int,
    max_width: int,
) -> int:
    """Draw heading word-by-word, coloring highlight_words in primary_rgb.
    Returns the y position immediately after the last heading line."""
    highlight_set = {w.upper() for w in (highlight_words or [])}
    words = heading.upper().split()

    # Word-wrap
    lines: list[list[str]] = []
    current_line: list[str] = []
    for word in words:
        test = " ".join(current_line + [word])
        if _text_w(draw, test, font) <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(current_line)
            current_line = [word]
    if current_line:
        lines.append(current_line)

    cy = y
    for line_words in lines:
        cx = x
        for word in line_words:
            color = (*primary_rgb, 255) if word in highlight_set else (255, 255, 255, 255)
            _shadow_text(draw, cx, cy, word, font, fill=color)
            cx += _text_w(draw, word + " ", font)
        cy += line_h

    return cy


def _get_zone_spec(text_zone: str) -> dict:
    """Return panel geometry and text anchor for the given text_zone."""
    specs = {
        "TOP_CENTER_BAND": {
            "panel": (0, 0, CANVAS_W, 300),
            "radius": 0,
            "text_x": 60,
            "text_y": 44,
            "text_max_w": CANVAS_W - 120,
        },
        "TOP_LEFT_PANEL": {
            "panel": (0, 0, 530, 340),
            "radius": 0,
            "text_x": 44,
            "text_y": 44,
            "text_max_w": 460,
        },
        "TOP_RIGHT_PANEL": {
            "panel": (494, 0, CANVAS_W, 340),
            "radius": 0,
            "text_x": 514,
            "text_y": 44,
            "text_max_w": 460,
        },
        "LEFT_GLASS_CARD": {
            "panel": (0, 160, 480, 820),
            "radius": 20,
            "text_x": 44,
            "text_y": 200,
            "text_max_w": 400,
        },
    }
    return specs.get(text_zone, specs["TOP_CENTER_BAND"])


def composite_overlay(
    image_bytes: bytes,
    heading: str,
    sub_heading: str,
    highlight_words: list,
    domain: str,
    primary_color: str = "#FF4500",
    text_zone: str = "TOP_CENTER_BAND",
    cta_corner: str = "BOTTOM_RIGHT",
    logo_bytes: bytes = None,
    logo_corner: str = "TOP_LEFT",
) -> bytes:
    """
    Composite heading, sub-heading, logo, and CTA pill onto a background image.

    Args:
        image_bytes:    Raw JPEG/PNG background image.
        heading:        Campaign heading (rendered by font engine — exact, no typos).
        sub_heading:    Campaign sub-heading.
        highlight_words: Up to 2 words from heading that render in primary_color.
        domain:         CTA domain text (e.g. "spenzo.io").
        primary_color:  Brand primary hex color.
        text_zone:      Where heading+sub-heading live: TOP_CENTER_BAND |
                        TOP_LEFT_PANEL | TOP_RIGHT_PANEL | LEFT_GLASS_CARD.
        cta_corner:     BOTTOM_LEFT | BOTTOM_RIGHT.
        logo_bytes:     Raw logo image bytes (optional).
        logo_corner:    TOP_LEFT | TOP_RIGHT.

    Returns:
        JPEG bytes of the fully composited image.
    """
    r_p, g_p, b_p = _hex_to_rgb(primary_color)
    text_zone = (text_zone or "TOP_CENTER_BAND").upper()
    cta_corner = (cta_corner or "BOTTOM_RIGHT").upper()
    logo_corner = (logo_corner or "TOP_LEFT").upper()

    # Open and resize background
    with Image.open(BytesIO(image_bytes)).convert("RGBA") as bg:
        bg = bg.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
        canvas = bg.copy()

    # --- Frosted panel behind text zone ---
    spec = _get_zone_spec(text_zone)
    px1, py1, px2, py2 = spec["panel"]
    text_x = spec["text_x"]
    text_y = spec["text_y"]
    text_max_w = spec["text_max_w"]
    panel_radius = spec["radius"]

    # Panel color: very dark tint of primary (not pure black, not heavy)
    pr = max(0, r_p // 5)
    pg = max(0, g_p // 5)
    pb = max(0, b_p // 5)

    frosted = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    f_draw = ImageDraw.Draw(frosted)
    f_draw.rounded_rectangle(
        [px1, py1, px2, py2],
        radius=panel_radius,
        fill=(pr, pg, pb, 165),
    )
    canvas = Image.alpha_composite(canvas, frosted)
    draw = ImageDraw.Draw(canvas)

    # --- Logo ---
    logo_bottom_y = py1 + 16
    if logo_bytes:
        try:
            with Image.open(BytesIO(logo_bytes)).convert("RGBA") as logo_img:
                logo_img.thumbnail((180, 65), Image.LANCZOS)
                lw, lh = logo_img.size
                pad = 8
                logo_pad = Image.new("RGBA", (lw + pad * 2, lh + pad * 2), (0, 0, 0, 110))
                logo_pad.paste(logo_img, (pad, pad), logo_img)

                ly = 24
                if logo_corner == "TOP_RIGHT":
                    lx = CANVAS_W - logo_pad.width - 28
                else:
                    lx = 28
                canvas.paste(logo_pad, (lx, ly), logo_pad)
                logo_bottom_y = ly + logo_pad.height + 14
        except Exception as e:
            logger.warning(f"Logo paste failed: {e}")

    # Adjust text_y to not overlap logo when both share the same top area
    if text_zone in ("TOP_CENTER_BAND", "TOP_LEFT_PANEL", "TOP_RIGHT_PANEL"):
        # Only push down if the logo corner overlaps the text panel x-range
        logo_in_text_area = (
            (logo_corner == "TOP_LEFT" and text_zone != "TOP_RIGHT_PANEL") or
            (logo_corner == "TOP_RIGHT" and text_zone != "TOP_LEFT_PANEL")
        )
        if logo_in_text_area:
            text_y = max(text_y, logo_bottom_y + 8)

    # --- Heading font — adaptive size ---
    heading_str = (heading or "").upper()
    heading_font = _get_font('bold', 72)
    for fs in [72, 64, 56, 48, 42, 36, 30]:
        heading_font = _get_font('bold', fs)
        test_lines = _wrap_text(draw, heading_str, heading_font, text_max_w)
        if len(test_lines) <= 3:
            break

    try:
        line_h = int(draw.textbbox((0, 0), "Ag", font=heading_font)[3]) + 14
    except Exception:
        line_h = 82

    # --- Draw heading with per-word highlight ---
    after_heading_y = _draw_heading(
        draw, text_x, text_y,
        heading_str, highlight_words,
        heading_font, (r_p, g_p, b_p),
        line_h, text_max_w,
    )

    # --- Subheading ---
    sub_font = _get_font('regular', 30)
    sub_y = after_heading_y + 14
    sub_lines = _wrap_text(draw, sub_heading or "", sub_font, text_max_w)
    sub_line_h = 42
    for i, sline in enumerate(sub_lines[:4]):
        _shadow_text(draw, text_x, sub_y + i * sub_line_h, sline, sub_font,
                     fill=(215, 215, 215, 255), shadow_offset=2)

    # --- CTA pill button ---
    if domain:
        cta_font = _get_font('bold', 28)
        cta_text = domain.lower().strip("/").split("/")[0]
        cta_y = CANVAS_H - 82
        txt_w = _text_w(draw, cta_text, cta_font)
        pad_x, btn_h, radius = 30, 54, 27
        btn_w = txt_w + 2 * pad_x

        if cta_corner == "BOTTOM_RIGHT":
            cta_x = CANVAS_W - btn_w - 40
        else:
            cta_x = 40

        draw.rounded_rectangle(
            [cta_x, cta_y, cta_x + btn_w, cta_y + btn_h],
            radius=radius,
            fill=(r_p, g_p, b_p),
        )
        draw.text(
            (cta_x + pad_x, cta_y + (btn_h - 28) // 2),
            cta_text,
            fill=(255, 255, 255),
            font=cta_font,
        )

    # Convert and return JPEG
    final_rgb = canvas.convert("RGB")
    out = BytesIO()
    final_rgb.save(out, format="JPEG", quality=95)
    return out.getvalue()
