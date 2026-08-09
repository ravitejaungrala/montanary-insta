"""Derive the full email colour palette from a single brand hex.

The f1 outreach template uses ~8 related colours (a primary family plus a
counter-accent plus two tints). Business DNA gives us ONE brand colour, so
substituting it everywhere flattens the design and substituting it in one
place leaves the original Spenzo orange behind in the other seven.

This module expands one hex into the full set, then CLAMPS each value until
it meets WCAG AA (4.5:1) against the surface it actually sits on. Without the
clamp a pale-yellow or neon brand renders 11px labels that nobody can read —
the template's small type is the constraint, not the brand.

Public surface:
    derive_palette(brand_hex, accent_hex=None) -> Dict[str, str]

Returns keys matching the `$c_*` placeholders in outreach_f1.tpl.html.
Never raises; falls back to a neutral slate palette on bad input.
"""
from __future__ import annotations

import colorsys
import re
from typing import Dict, Optional, Tuple

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Surfaces the palette must stay legible against.
_SURFACE_CARD = (0xFF, 0xFF, 0xFF)      # white stat/capability cards
_SURFACE_SHELL = (0xFF, 0xFD, 0xF8)     # the cream email body
_SURFACE_DARK = (0x17, 0x12, 0x0F)      # the editorial panel

# Neutral slate fallback — mirrors the "never the old hardcoded orange"
# rule already in outreach_template.render_email.
_NEUTRAL = {
    "c_rule": "#475569",
    "c_link": "#334155",
    "c_accent": "#475569",
    "c_eyebrow": "#475569",
    "c_deep": "#1e293b",
    "c_counter": "#0f4c5c",
    "c_soft": "#94a3b8",
    "c_cta_bg": "#f1f5f9",
    "c_cta_border": "#e2e8f0",
    "c_shell": "#eceae6",
    "c_card": "#fffdf8",
    "c_band": "#f4ede6",
    "c_panel": "#fbfaf8",
    "c_tile": "#ffffff",
    "c_hairline": "#e6e1db",
}


def _parse(hex_str: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not hex_str or not isinstance(hex_str, str):
        return None
    m = _HEX_RE.match(hex_str.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _to_hls(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in rgb)
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h: float, l: float, s: float) -> Tuple[int, int, int]:
    l = max(0.0, min(1.0, l))
    s = max(0.0, min(1.0, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _shift(rgb: Tuple[int, int, int], dl: float, ds: float = 0.0) -> Tuple[int, int, int]:
    """Lightness/saturation shift in HLS space."""
    h, l, s = _to_hls(rgb)
    return _from_hls(h, l + dl, s + ds)


def _rel_luminance(rgb: Tuple[int, int, int]) -> float:
    def chan(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    la, lb = _rel_luminance(a), _rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _clamp_contrast(
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
    target: float = 4.5,
    steps: int = 40,
) -> Tuple[int, int, int]:
    """Walk `fg` toward whichever pole increases contrast until it hits
    `target`. Returns the original when it already passes."""
    if contrast_ratio(fg, bg) >= target:
        return fg
    # Darken against a light background, lighten against a dark one.
    direction = -1.0 if _rel_luminance(bg) > 0.5 else 1.0
    h, l, s = _to_hls(fg)
    for i in range(1, steps + 1):
        cand = _from_hls(h, l + direction * (i / steps), s)
        if contrast_ratio(cand, bg) >= target:
            return cand
    # Unreachable in practice — fall back to the pole itself.
    return (0x11, 0x11, 0x11) if direction < 0 else (0xFF, 0xFF, 0xFF)


def _derive_surfaces(
    brand: Tuple[int, int, int],
    background: Optional[Tuple[int, int, int]],
) -> Dict[str, Tuple[int, int, int]]:
    """Email surface colours, tinted toward the site's own background.

    The site's background is used for its HUE, not its lightness. Taking a
    dark background literally would put dark type on dark and swallow the
    content, so anything below the lightness floor contributes hue only and
    the surfaces stay light. That is the "fallback to light" rule: a dark or
    overpowering site background never becomes the email's background.

    Also refuses a heavily saturated hue — a vivid background reads as a
    coloured block behind the copy rather than a paper tone.
    """
    # Hue source: the site background when it actually carries a hue, else the
    # brand. Only the HUE is taken — lightness and saturation are always
    # re-set to the paper values below, which is what makes a dark or vivid
    # site background impossible to inherit.
    #
    # An achromatic background (#fff, #000, any grey) has an arbitrary hue and
    # zero saturation, so tinting from it produces a cold grey email that
    # throws the brand away. Those fall back to the brand hue.
    src = brand
    if background is not None:
        _bh, _bl, bs = _to_hls(background)
        if bs >= 0.06:
            src = background
    h, _l, s = _to_hls(src)
    tint_s = min(s, 0.22)

    return {
        "shell": _from_hls(h, 0.925, min(tint_s, 0.16)),   # outer page
        "card": _from_hls(h, 0.988, min(tint_s, 0.10)),    # main card
        "band": _from_hls(h, 0.945, min(tint_s, 0.18)),    # masthead + footer
        "panel": _from_hls(h, 0.975, min(tint_s, 0.12)),   # section panels
        "tile": _from_hls(h, 0.999, min(tint_s, 0.05)),    # stat/benefit cards
        "hairline": _from_hls(h, 0.895, min(tint_s, 0.14)),  # borders/rules
    }


def _counter_from(brand: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """A muted counter-accent. Rotates ~150 degrees and desaturates so it
    reads as a deliberate second colour rather than a clashing complement."""
    h, l, s = _to_hls(brand)
    return _from_hls((h + 150.0 / 360.0) % 1.0, min(l, 0.28), min(s, 0.62))


def derive_palette(
    brand_hex: Optional[str],
    accent_hex: Optional[str] = None,
    background_hex: Optional[str] = None,
) -> Dict[str, str]:
    """Expand one brand hex into the template's full `$c_*` set.

    Returns the neutral slate palette when `brand_hex` is missing or invalid,
    matching the existing behaviour of never inventing a brand colour.
    """
    brand = _parse(brand_hex)
    if brand is None:
        return dict(_NEUTRAL)

    counter = _parse(accent_hex) or _counter_from(brand)
    surfaces = _derive_surfaces(brand, _parse(background_hex))
    card = surfaces["card"]
    shell = surfaces["shell"]

    # Primary family. The rule/border sits on a cream band and carries no
    # text, so it keeps the brand colour at full strength; everything that
    # carries type gets contrast-clamped below.
    rule = brand
    link = _shift(brand, -0.10)     # button fill — white text sits on it
    accent = _shift(brand, -0.06)   # small-caps eyebrow on light tint
    eyebrow = _shift(brand, -0.04)
    deep = _shift(brand, -0.13)     # 31px stat numerals on white
    soft = _shift(brand, +0.22, +0.05)  # light accent on the dark panel

    # Tints for the CTA card.
    h, l, s = _to_hls(brand)
    cta_bg = _from_hls(h, 0.95, min(s, 0.55))
    cta_border = _from_hls(h, 0.88, min(s, 0.50))

    # --- contrast clamps, each against the surface it really sits on ---
    #
    # Targets are deliberately ABOVE the WCAG minimum. The eyebrows and card
    # labels these drive are 11-13px all-caps, where 4.5:1 is technically
    # compliant but still reads faint — legibility beats the stylistic appeal
    # of a lighter brand tint, so they are pushed to 7:1 (AAA for body text).
    link = _clamp_contrast(link, (0xFF, 0xFF, 0xFF), target=5.0)   # white on fill
    accent = _clamp_contrast(accent, cta_bg, target=7.0)           # 11px caps
    eyebrow = _clamp_contrast(eyebrow, surfaces['panel'], target=7.0)  # 11px caps
    # deep/counter carry the 30px stat numerals AND the 13px capability-card
    # ordinals. The small usage is the binding constraint, so they are held to
    # the same 7:1 as the other small type rather than the 3:1 large-text
    # minimum — an earlier 4.5 target left the ordinals at 4.6:1.
    deep = _clamp_contrast(deep, surfaces['tile'], target=7.0)
    counter = _clamp_contrast(counter, surfaces['tile'], target=7.0)
    soft = _clamp_contrast(soft, _SURFACE_DARK, target=7.0)

    return {
        "c_rule": _hex(rule),
        "c_link": _hex(link),
        "c_accent": _hex(accent),
        "c_eyebrow": _hex(eyebrow),
        "c_deep": _hex(deep),
        "c_counter": _hex(counter),
        "c_soft": _hex(soft),
        "c_cta_bg": _hex(cta_bg),
        "c_cta_border": _hex(cta_border),
        # Surfaces, hue-tinted from the site background (light-clamped).
        "c_shell": _hex(shell),
        "c_card": _hex(card),
        "c_band": _hex(surfaces["band"]),
        "c_panel": _hex(surfaces["panel"]),
        "c_tile": _hex(surfaces["tile"]),
        "c_hairline": _hex(surfaces["hairline"]),
    }
