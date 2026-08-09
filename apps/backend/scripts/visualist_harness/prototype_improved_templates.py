"""Prototype: four design-intelligence upgrades to current T1.

Upgrades vs. render_5_templates.py:
  1. Auto-contrast — if brand primary fails WCAG-AA (<4.5:1) against the
     text background, auto-shift luminance so it reads cleanly.
  2. Palette harmony — extract dominant color from the bg photo and use
     it as a soft deco accent (corner bar) that ties photo & canvas.
  3. Typographic hierarchy — tracked uppercase eyebrow, tighter display
     leading (1.05), looser body leading (1.38), optical size-up of
     headline via synthetic pseudo-weight.
  4. Subtle elevation — soft shadow under photo + CTA for depth without
     looking noisy.

Produces: out/compare_T1.png  (left=current, right=improved, labeled)
"""
from __future__ import annotations

import sys
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
from render_5_templates import (  # noqa: E402
    t1_header_band,
    COPY,
    PRIMARY_HEX,
    CANVAS,
    WHITE,
    BLACK,
    _font,
    _fit_font,
    _draw_lines,
    render_lockup,
    photo_fit,
)

# ---------------------------------------------------------------------------
# 1. Auto-contrast — WCAG relative luminance
# ---------------------------------------------------------------------------
def _rel_luminance(rgb: tuple) -> float:
    def chan(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast_ratio(fg: tuple, bg: tuple) -> float:
    l1, l2 = _rel_luminance(fg), _rel_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _auto_contrast(primary: tuple, bg: tuple = (255, 255, 255),
                   target: float = 4.5) -> tuple:
    """If primary fails WCAG-AA on bg, shift luminance (HSV V) until it passes.

    Preserves hue/saturation — only pulls the color darker or lighter.
    Returns the adjusted RGB."""
    if _contrast_ratio(primary, bg) >= target:
        return primary
    from colorsys import rgb_to_hsv, hsv_to_rgb
    r, g, b = [c / 255.0 for c in primary]
    h, s, v = rgb_to_hsv(r, g, b)
    bg_light = _rel_luminance(bg) > 0.5
    # If bg is light, we need darker (lower v); else lighter (higher v).
    step = -0.05 if bg_light else 0.05
    for _ in range(20):
        v = max(0.0, min(1.0, v + step))
        rgb = tuple(int(round(c * 255)) for c in hsv_to_rgb(h, s, v))
        if _contrast_ratio(rgb, bg) >= target:
            return rgb
    return (20, 20, 28) if bg_light else (235, 235, 235)


# ---------------------------------------------------------------------------
# 2. Palette harmony — extract dominant non-neutral from bg photo
# ---------------------------------------------------------------------------
def _dominant_accent(img: Image.Image) -> tuple:
    """Pick a dominant non-neutral, non-extreme color via a 16-bin quantize.

    Falls back to a muted blue-grey if photo is neutral-only."""
    small = img.convert("RGB").resize((80, 80), Image.LANCZOS)
    q = small.quantize(colors=16).convert("RGB")
    counts: dict = {}
    for px in q.getdata():
        counts[px] = counts.get(px, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    for (r, g, b), _ in ranked:
        mx, mn = max(r, g, b), min(r, g, b)
        if mx - mn < 25:  # near-grey — skip
            continue
        if max(r, g, b) > 235 or max(r, g, b) < 40:  # too bright/dark
            continue
        return (r, g, b)
    return (86, 106, 132)


# ---------------------------------------------------------------------------
# 3. Typography helpers — tracked text + synthetic weight bump
# ---------------------------------------------------------------------------
def _draw_tracked(d: ImageDraw.ImageDraw, text: str, font, x: int, y: int,
                  fill, tracking_px: int = 2) -> int:
    """Draw text with added letter-spacing (tracking). Returns width used."""
    cursor = x
    for ch in text:
        d.text((cursor, y), ch, font=font, fill=fill)
        bb = d.textbbox((0, 0), ch, font=font)
        cursor += (bb[2] - bb[0]) + tracking_px
    return cursor - x


def _draw_heavy(d: ImageDraw.ImageDraw, text: str, font, x: int, y: int,
                fill) -> None:
    """Pseudo heavy weight — draw twice with 1px X offset for optical bump.

    Cheap alternative to shipping a Black-weight TTF."""
    d.text((x, y), text, font=font, fill=fill)
    d.text((x + 1, y), text, font=font, fill=fill)


def _draw_highlighted_heavy(d: ImageDraw.ImageDraw, lines, font, x: int,
                            y: int, hi_words, primary_rgb, spacing: float,
                            base_color=BLACK) -> int:
    """Highlighted draw using pseudo-heavy weight on all words."""
    hi_set = {w.lower().strip(".,;:!?") for w in (hi_words or [])}
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * spacing)
    cy = y
    for line in lines:
        cx = x
        for word in line.split():
            clean = word.strip(".,;:!?-").lower()
            col = primary_rgb if clean in hi_set else base_color
            _draw_heavy(d, word, font, cx, cy, col)
            wb = d.textbbox((0, 0), word + " ", font=font)
            cx += wb[2] - wb[0]
        cy += line_h
    return cy


# ---------------------------------------------------------------------------
# 4. Soft elevation — drop shadow for photo / CTA
# ---------------------------------------------------------------------------
def _paste_with_shadow(canvas: Image.Image, img: Image.Image, xy: tuple,
                       shadow_rgba=(0, 0, 0, 60), blur: int = 24,
                       offset=(0, 8), mask=None) -> None:
    """Paste img at xy with a soft blurred shadow underneath."""
    x, y = xy
    w, h = img.size
    sh = Image.new("RGBA", (w + blur * 4, h + blur * 4), (0, 0, 0, 0))
    shape = Image.new("RGBA", (w, h), shadow_rgba)
    sh.paste(shape, (blur * 2, blur * 2), mask if mask else shape)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(sh, (x - blur * 2 + offset[0],
                                     y - blur * 2 + offset[1]))
    if mask:
        canvas_rgba.paste(img, (x, y), mask)
    else:
        canvas_rgba.paste(img, (x, y))
    canvas.paste(canvas_rgba.convert("RGB"))


# ---------------------------------------------------------------------------
# Improved T1 — same layout, better execution
# ---------------------------------------------------------------------------
def t1_improved(bg: Image.Image, copy: dict, logo: Image.Image,
                primary_rgb: tuple) -> Image.Image:
    C = CANVAS
    canvas = Image.new("RGB", (C, C), WHITE)
    d = ImageDraw.Draw(canvas)
    pad = 48

    # Upgrade #1: auto-contrast brand primary for text usage
    text_primary = _auto_contrast(primary_rgb, bg=WHITE, target=4.5)
    cta_primary = primary_rgb  # keep vivid on solid CTA — white text gives contrast

    # Upgrade #2: extract photo palette, use as corner deco
    accent_from_photo = _dominant_accent(bg)

    # Corner deco strip — thin bar top-right pulled from photo palette
    deco_w, deco_h = 120, 6
    d.rectangle([C - pad - deco_w, pad - 14, C - pad, pad - 14 + deco_h],
                fill=accent_from_photo)

    # 1. Lockup (unchanged, but product name uses auto-contrasted primary)
    lockup = render_lockup(logo, copy["product_name"], copy["product_tagline"],
                           C - 2 * pad - deco_w - 24, 56, text_primary)
    canvas.paste(lockup, (pad, pad), lockup)

    # 2. Eyebrow — tracked uppercase label above headline (new hierarchy layer)
    eb_text = "FEATURED"
    eb_f = _font("sans_bold", 14)
    eb_y = pad + lockup.size[1] + 22
    _draw_tracked(d, eb_text, eb_f, pad, eb_y, text_primary, tracking_px=3)
    # Micro-line after eyebrow
    eb_bbox = d.textbbox((0, 0), eb_text, font=eb_f)
    eb_w = (eb_bbox[2] - eb_bbox[0]) + len(eb_text) * 3
    d.line([pad + eb_w + 10, eb_y + 9, pad + eb_w + 46, eb_y + 9],
           fill=text_primary, width=2)

    # 3. Headline — tighter leading (1.05 vs 1.16), heavier stroke
    text_top = eb_y + 28
    f_head, head_lines = _fit_font(d, copy["headline"], "display",
                                   42, 28, C - 2 * pad, 150,
                                   max_lines=2, spacing=1.05)
    y = _draw_highlighted_heavy(d, head_lines, f_head, pad, text_top,
                                copy.get("highlight_words"), text_primary,
                                spacing=1.05)

    # 4. Sub — looser leading (1.38), slightly smaller max size for contrast
    y += 14
    sub_color = (60, 64, 74)  # warm near-black — softer than pure black
    f_sub, sub_lines = _fit_font(d, copy["subheading"], "sans_reg",
                                 22, 17, int((C - 2 * pad) * 0.82), 80,
                                 max_lines=2, spacing=1.38)
    y = _draw_lines(d, sub_lines, f_sub, pad, y, sub_color, spacing=1.38)

    # 5. CTA — solid pill with drop shadow for depth
    y += 18
    cta_text = copy["cta"]
    f_cta = _font("sans_bold", 19)
    tw = d.textbbox((0, 0), cta_text, font=f_cta)
    btn_w = (tw[2] - tw[0]) + 66
    btn_h = 50

    # Soft shadow under CTA
    cta_shadow = Image.new("RGBA", (btn_w + 40, btn_h + 40), (0, 0, 0, 0))
    csd = ImageDraw.Draw(cta_shadow)
    csd.rounded_rectangle([20, 20, 20 + btn_w, 20 + btn_h],
                          radius=btn_h // 2, fill=(0, 0, 0, 70))
    cta_shadow = cta_shadow.filter(ImageFilter.GaussianBlur(12))
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(cta_shadow, (pad - 20, y - 20 + 6))
    canvas = canvas_rgba.convert("RGB")
    d = ImageDraw.Draw(canvas)

    d.rounded_rectangle([pad, y, pad + btn_w, y + btn_h],
                        radius=btn_h // 2, fill=cta_primary)
    asc, desc = f_cta.getmetrics()
    d.text((pad + 30, y + (btn_h - (asc + desc)) // 2),
           cta_text, font=f_cta, fill=WHITE)
    # Arrow inside CTA
    ax = pad + btn_w - 26
    ay = y + btn_h // 2
    d.polygon([(ax, ay - 6), (ax + 10, ay), (ax, ay + 6)], fill=WHITE)

    # 6. Photo — rounded corners + soft shadow for elevation
    photo_top = y + btn_h + 30
    photo_h = C - photo_top - pad
    if photo_h > 60:
        photo_w = C - 2 * pad
        photo = photo_fit(bg, photo_w, photo_h)
        mask = _rounded_mask(photo.size, 22)
        # Shadow
        sh = Image.new("RGBA", (photo_w + 80, photo_h + 80), (0, 0, 0, 0))
        shape = Image.new("RGBA", photo.size, (0, 0, 0, 55))
        sh.paste(shape, (40, 40), mask)
        sh = sh.filter(ImageFilter.GaussianBlur(22))
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.alpha_composite(sh, (pad - 40, photo_top - 40 + 10))
        canvas = canvas_rgba.convert("RGB")
        # Photo on top
        canvas.paste(photo, (pad, photo_top), mask)

    return canvas


# ---------------------------------------------------------------------------
# Side-by-side comparison sheet
# ---------------------------------------------------------------------------
def make_compare_sheet(current: Image.Image, improved: Image.Image,
                       out_path: Path) -> None:
    gutter = 40
    label_h = 72
    W = current.width + improved.width + gutter * 3
    H = max(current.height, improved.height) + label_h + gutter * 2
    sheet = Image.new("RGB", (W, H), (246, 246, 248))
    d = ImageDraw.Draw(sheet)
    f_lbl = _font("sans_bold", 28)
    f_tag = _font("sans_reg", 16)

    # Labels
    d.text((gutter, gutter), "CURRENT", font=f_lbl, fill=(120, 120, 130))
    d.text((gutter, gutter + 34),
           "Pillow, static contrast, single-weight type",
           font=f_tag, fill=(140, 140, 150))
    d.text((gutter * 2 + current.width, gutter), "IMPROVED",
           font=f_lbl, fill=(30, 30, 40))
    d.text((gutter * 2 + current.width, gutter + 34),
           "Auto-contrast + photo palette + tracked type + elevation",
           font=f_tag, fill=(80, 80, 90))

    sheet.paste(current, (gutter, gutter + label_h))
    sheet.paste(improved, (gutter * 2 + current.width, gutter + label_h))
    sheet.save(out_path, "PNG")


def main() -> None:
    bg_path = HERE / "out" / "img_3.png"
    logo_path = BACKEND_DIR.parent / "product-page" / "public" / "favicon.png"
    bg = Image.open(bg_path).convert("RGB")
    logo = _load_logo_image(logo_path.read_bytes(), 200, 200)
    primary_rgb = _hex_to_rgb(PRIMARY_HEX)

    print(f"Primary          : {PRIMARY_HEX} -> {primary_rgb}")
    print(f"Contrast on white: {_contrast_ratio(primary_rgb, WHITE):.2f}:1 "
          f"(WCAG-AA needs 4.5)")
    adj = _auto_contrast(primary_rgb, WHITE, 4.5)
    print(f"Auto-adjusted    : {adj}  "
          f"(contrast {_contrast_ratio(adj, WHITE):.2f}:1)")
    print(f"Photo accent     : {_dominant_accent(bg)}")
    print()

    current = t1_header_band(bg, COPY, logo, primary_rgb)
    improved = t1_improved(bg, COPY, logo, primary_rgb)

    out_current = HERE / "out" / "proto_current_T1.png"
    out_improved = HERE / "out" / "proto_improved_T1.png"
    out_sheet = HERE / "out" / "proto_compare_T1.png"
    current.save(out_current, "PNG")
    improved.save(out_improved, "PNG")
    make_compare_sheet(current, improved, out_sheet)
    print(f"  OK  {out_current.relative_to(HERE)}")
    print(f"  OK  {out_improved.relative_to(HERE)}")
    print(f"  OK  {out_sheet.relative_to(HERE)}")


if __name__ == "__main__":
    main()
