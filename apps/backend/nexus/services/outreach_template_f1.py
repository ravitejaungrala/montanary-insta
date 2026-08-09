"""Renderer for the f1 branded outreach template.

Loads `templates/email/outreach_f1.tpl.html` and fills it per company. Same
public shape as `outreach_template.render_email` so the sequencer can switch
between the two:

    render_email_f1(lead, sender, gemini, step=0) -> {"subject", "html", "text"}

Design rules enforced here (see dynamic_email_images_plan.md):

  * Every image slot is CONDITIONAL. A missing asset omits its block entirely —
    never a broken-image icon, never an empty `src`.
  * The stat strip renders ONLY from proof points EXTRACTED from the sender's
    own material. It is never LLM-generated: those are factual performance
    claims about a real company, and inventing them would publish fabricated
    results under that company's brand. Fewer than 3 → the strip is dropped.
  * Colours come from `email_palette.derive_palette`, which contrast-clamps
    against the surface each one sits on.

`string.Template` is used rather than str.format (CSS braces) or Jinja2 (not a
backend dependency). Conditional regions are pre-rendered in Python and
injected as `$*_block` values — substitution is single-pass, so a `$` inside a
DATA value (e.g. a proof point of "$10M+") is never re-scanned.
"""
from __future__ import annotations

import html as _html
import logging
import math
import os
from datetime import datetime, timezone
from string import Template
from typing import Any, Dict, List, Mapping, Optional, Sequence

from nexus.services.email_palette import derive_palette
# Single source of truth for the cadence's per-step content keys, shared with
# the incumbent renderer so both read the same stored draft fields.
from nexus.services.outreach_template import _STEP_FIELDS

log = logging.getLogger("nexus.outreach_template_f1")

_TPL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "email", "outreach_f1.tpl.html",
)

_TPL_CACHE: Optional[Template] = None

# The editorial photo is a single global asset for every sender (see plan §3.3).
# Overridable so the S3 URL isn't baked into code.
EDITORIAL_IMAGE_URL = os.getenv("NEXUS_EMAIL_EDITORIAL_IMAGE_URL", "")


def _env(name: str, default: str = "") -> str:
    """Read env at CALL time, not import time, so a .env edit takes effect on
    restart without a code change (matches outreach_template._env_or_default)."""
    v = os.environ.get(name, "")
    return (v.strip() if v else "") or default


# Generic, brand-neutral fallbacks. Used only when the product supplies
# nothing — never a specific company's wording. Real per-product copy comes
# from `sender['f1_sections']` (nexus_products.f1_sections).
_FALLBACK_SECTIONS: Dict[str, str] = {
    "benefits_heading": "What you get",
    "benefits_sub": "",
    "editorial_heading": "",
    "editorial_body": "",
    "cta_heading": "See how it works",
    "cta_subcopy": "A short walkthrough, no preparation needed.",
}


def _section(sections: Mapping[str, Any], g: Mapping[str, Any], key: str) -> str:
    """Resolve one piece of section copy: per-lead override -> per-product
    cached copy -> brand-neutral fallback."""
    for src in (g, sections):
        v = src.get(key) if isinstance(src, Mapping) else None
        if v and str(v).strip():
            return str(v).strip()
    return _FALLBACK_SECTIONS.get(key, "")


def _esc(s: Any) -> str:
    return _html.escape(str(s or ""), quote=True)


# ---------------------------------------------------------------------------
# Wordmark auto-fit
# ---------------------------------------------------------------------------
# Advance widths for Georgia Bold UPPERCASE, in em units. Georgia's caps vary
# enormously (I is 0.40em, W is 1.02em), so a flat average mis-sizes real names
# badly in both directions — "IIT" would shrink for no reason and "WWM" would
# still overflow. Measured per glyph instead.
_GEORGIA_BOLD_CAPS: Dict[str, float] = {
    "A": 0.72, "B": 0.70, "C": 0.71, "D": 0.76, "E": 0.68, "F": 0.64,
    "G": 0.78, "H": 0.79, "I": 0.40, "J": 0.55, "K": 0.74, "L": 0.64,
    "M": 0.95, "N": 0.77, "O": 0.79, "P": 0.64, "Q": 0.79, "R": 0.72,
    "S": 0.62, "T": 0.66, "U": 0.77, "V": 0.72, "W": 1.02, "X": 0.73,
    "Y": 0.70, "Z": 0.64,
    "0": 0.65, "1": 0.65, "2": 0.65, "3": 0.65, "4": 0.65,
    "5": 0.65, "6": 0.65, "7": 0.65, "8": 0.65, "9": 0.65,
    " ": 0.30, "&": 0.80, ".": 0.32, ",": 0.32, "-": 0.40,
    "'": 0.26, "/": 0.42, "+": 0.60,
}
_DEFAULT_ADVANCE = 0.72

# Usable width inside the 600px card, after padding, at each breakpoint.
# Masthead loses another 60px (44px mark + 16px gutter) when a logo renders.
# Keep in lockstep with the padding-right in _logo_block: a mismatch makes
# the fitted wordmark overflow by exactly the difference.
_W_DESKTOP = 552.0
_W_MOBILE = 252.0
_LOGO_COST = 60.0


def _floor1(x: float) -> float:
    """Truncate to 1dp. Never rounds up — see fit_wordmark."""
    return math.floor(x * 10.0) / 10.0


def _text_width_em(text: str) -> float:
    return sum(_GEORGIA_BOLD_CAPS.get(c, _DEFAULT_ADVANCE) for c in text)


def fit_wordmark(
    text: str,
    max_width_px: float,
    base_px: float,
    min_px: float,
    tracking_ratio: float = 0.042,
) -> tuple:
    """Largest font size (≤ base_px) at which `text` fits `max_width_px` on one
    line, with letter-spacing scaling alongside it.

    Returns `(font_px, tracking_px)`, both rounded to 1dp.

    Floors at `min_px` rather than shrinking without limit — below that the
    wordmark stops reading as a wordmark. A name still too long at the floor is
    left to wrap naturally (the cell sets no `nowrap`), which is the graceful
    failure for a genuinely long multi-word company name.
    """
    text = (text or "").strip()
    if not text:
        return _floor1(base_px), _floor1(base_px * tracking_ratio)

    w_em = _text_width_em(text)
    gaps = max(len(text) - 1, 0)
    # width(font) = font*w_em + font*tracking_ratio*gaps  →  solve for font
    denom = w_em + tracking_ratio * gaps
    ideal = max_width_px / denom if denom > 0 else base_px
    font = max(min(base_px, ideal), min_px)
    # Round DOWN, not to nearest: rounding 34.86 up to 34.9 put a fitted
    # wordmark 1.1px over its container. Both values floor so the emitted
    # size is always ≤ the solved size.
    return _floor1(font), _floor1(font * tracking_ratio)


def _load_template() -> Template:
    global _TPL_CACHE
    if _TPL_CACHE is None:
        with open(_TPL_PATH, encoding="utf-8") as fh:
            _TPL_CACHE = Template(fh.read())
    return _TPL_CACHE


# ---------------------------------------------------------------------------
# Block builders — each returns "" when its data is absent
# ---------------------------------------------------------------------------
def _logo_block(url: str) -> str:
    """Masthead logo. Optional and user-supplied only; absent → the <td> is
    dropped and the wordmark centres on its own."""
    if not url:
        return ""
    return (
        '          <td style="vertical-align:middle;padding-right:16px;line-height:0">'
        f'<img src="{_esc(url)}" width="44" height="44" alt="" '
        'style="display:block;width:44px;height:44px;border:0"></td>'
    )


def _hero_block(url: str, alt: str, pal: Dict[str, str]) -> str:
    if not url:
        return ""
    return (
        '  <tr><td class="pad dm-panel" style="padding:32px 24px 24px;background-color:{pal["c_panel"]}">\n'
        f'    <img src="{_esc(url)}" width="552" height="235" alt="{_esc(alt)}" '
        'style="display:block;width:100%;max-width:552px;height:auto;border:0;'
        'border-radius:20px">\n'
        '  </td></tr>\n'
    )


def _stat_card(pt: Mapping[str, Any], accent: str, pal: Dict[str, str]) -> str:
    return f"""            <td width="32%" class="c33 dm-tile" style="width:32%;vertical-align:top;background-color:{pal["c_tile"]};border:1px solid {pal["c_hairline"]};border-top:3px solid {accent};border-radius:14px">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="width:100%"><tbody><tr><td style="padding:20px;font-family:Arial,Helvetica,sans-serif">
                <div class="fs-stat" style="font-family:Georgia,'Times New Roman',serif;font-size:30px;font-weight:700;letter-spacing:-0.02em;line-height:1.1;color:{accent};padding:0 0 8px;mso-line-height-rule:exactly">{_esc(pt.get('value'))}</div>
                <div class="dm-h" style="font-size:11.5px;font-weight:700;letter-spacing:1.3px;line-height:1.35;color:#171412;mso-line-height-rule:exactly">{_esc(pt.get('label')).upper()}</div>
                <div class="dm-m" style="font-size:13px;line-height:1.5;color:#443e39;padding-top:8px;mso-line-height-rule:exactly">{_esc(pt.get('note'))}</div>
              </td></tr></tbody></table>
            </td>"""


def _stats_block(proof_points: Sequence[Mapping[str, Any]], pal: Dict[str, str]) -> str:
    """Render the 3-up stat strip.

    Requires exactly 3 usable proof points. These MUST be extracted from the
    sender's own published material — never model-generated. Anything less
    than 3 drops the strip; the email reads fine without it.
    """
    usable = [
        p for p in (proof_points or [])
        if isinstance(p, Mapping) and str(p.get("value") or "").strip()
        and str(p.get("label") or "").strip()
    ][:3]
    if len(usable) < 3:
        return ""

    accents = [pal["c_deep"], pal["c_counter"], pal["c_deep"]]
    spacer = '            <td width="2%" class="sp" style="width:2%;font-size:0">&nbsp;</td>'
    cards = []
    for i, pt in enumerate(usable):
        cards.append(_stat_card(pt, accents[i], pal))
        if i < len(usable) - 1:
            cards.append(spacer)
    joined = "\n".join(cards)

    return f"""  <tr><td class="pad dm-panel" style="padding:0 24px 0;background-color:{pal["c_panel"]}">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="width:100%;table-layout:fixed"><tbody><tr>
{joined}
    </tr></tbody></table>
  </td></tr>

  <tr><td class="pad dm-panel" style="padding:24px 24px 0;background-color:{pal["c_panel"]};font-size:0;line-height:0"><div class="dm-rule" style="height:1px;background-color:{pal["c_hairline"]};font-size:0;line-height:1px;mso-line-height-rule:exactly">&nbsp;</div></td></tr>
"""


def _benefit_card(
    idx: int, title: str, body: str, accent: str, pal: Dict[str, str],
) -> str:
    """One capability card. The icon chip that used to lead the card was
    removed with the rest of the SVG icons, so the ordinal takes its place at
    top-left — leaving the chip in place with no icon renders an empty box."""
    return f"""          <td width="48.55%" class="c50 dm-tile" style="width:48.55%;vertical-align:top;background-color:{pal["c_tile"]};border:1px solid {pal["c_hairline"]};border-left:3px solid {accent};border-radius:20px">
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="width:100%;table-layout:fixed"><tbody><tr><td style="padding:24px;font-family:Arial,Helvetica,sans-serif">
          <div style="font-family:Georgia,'Times New Roman',serif;font-size:13px;font-weight:700;letter-spacing:0.5px;line-height:1.2;color:{accent};mso-line-height-rule:exactly">{idx:02d}</div>
          <div class="dm-h" style="font-size:17.5px;font-weight:700;letter-spacing:-0.015em;line-height:1.32;color:#171412;padding:12px 0 8px;mso-line-height-rule:exactly">{_esc(title)}</div>
          <div class="dm-b" style="font-size:14.5px;line-height:1.6;color:#443e39;mso-line-height-rule:exactly">{_esc(body)}</div>
        </td></tr></tbody></table>
          </td>"""


def _capabilities_block(
    benefits: Sequence[Any],
    company: str,
    section_heading: str,
    section_sub: str,
    pal: Dict[str, str],
) -> str:
    """2-up benefit grid built from `nexus_products.key_benefits`.

    `key_benefits` is a free-length JSONB array, so: >4 takes the first four,
    <4 renders however many exist (a lone benefit becomes a full-width card),
    0 drops the whole section.
    """
    items: List[Dict[str, str]] = []
    for raw in (benefits or [])[:4]:
        if isinstance(raw, Mapping):
            title = str(raw.get("title") or raw.get("label") or "").strip()
            body = str(raw.get("body") or raw.get("description") or "").strip()
        else:
            title, _, body = str(raw or "").partition(" — ")
            title, body = title.strip(), body.strip()
        if title:
            items.append({"title": title, "body": body})
    if not items:
        return ""

    accents = [pal["c_deep"], pal["c_counter"]]
    spacer = '          <td width="16" class="sp" style="width:16px;font-size:0">&nbsp;</td>'

    rows = []
    for start in range(0, len(items), 2):
        pair = items[start:start + 2]
        cells = []
        for j, it in enumerate(pair):
            n = start + j
            cells.append(
                _benefit_card(n + 1, it["title"], it["body"],
                              accents[n % 2], pal)
            )
            if j == 0 and len(pair) == 2:
                cells.append(spacer)
        pad = "0 0 16px" if start + 2 < len(items) else "0"
        rows.append(
            f'      <tr><td style="padding:{pad}">\n'
            '        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
            'style="width:100%;table-layout:fixed"><tbody><tr>\n'
            + "\n".join(cells) +
            '\n        </tr></tbody></table>\n      </td></tr>'
        )

    eyebrow = f"WHAT {company} DOES" if company else "WHAT WE DO"
    sub = (
        f'      <tr><td class="dm-b" style="padding:0 0 24px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:15px;line-height:1.55;color:#443e39;mso-line-height-rule:exactly">'
        f'{_esc(section_sub)}</td></tr>\n'
        if section_sub else ""
    )

    return f"""  <tr><td class="pad dm-panel" style="padding:24px 24px 32px;background-color:{pal["c_panel"]}">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="width:100%"><tbody>
      <tr><td style="padding:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:700;letter-spacing:2.3px;line-height:1.2;color:{pal['c_eyebrow']};mso-line-height-rule:exactly">{_esc(eyebrow)}</td></tr>
      <tr><td dir="ltr" class="fs-240 dm-h" style="font-family:Georgia,'Times New Roman',serif;font-size:28px;font-weight:700;letter-spacing:-0.025em;padding:0 0 8px;line-height:1.15;color:#171412;mso-line-height-rule:exactly">{_esc(section_heading)}</td></tr>
{sub}{chr(10).join(rows)}
    </tbody></table>
  </td></tr>
"""


def _editorial_block(
    heading: str, body: str, image_url: str, pal: Dict[str, str]
) -> str:
    """Dark 'why it matters' panel. The photo is a global constant; when it is
    absent the copy simply goes full-width rather than leaving a gap."""
    if not heading and not body:
        return ""
    if image_url:
        copy_w, img_cell = "54%", (
            '      <td width="46%" class="c50 ed-img" style="width:46%;vertical-align:middle;'
            'padding:24px 24px 24px 0;line-height:0">\n'
            f'        <img src="{_esc(image_url)}" width="240" height="210" alt="" '
            'style="display:block;width:100%;max-width:240px;height:auto;border:0;'
            'border-radius:14px">\n'
            '      </td>\n'
        )
    else:
        copy_w, img_cell = "100%", ""

    return f"""  <tr><td class="pad" style="padding:0 24px 32px">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" class="stack" style="width:100%;table-layout:fixed;background-color:#17120f;border-radius:20px"><tbody><tr>
      <td width="{copy_w}" class="c50 ed-copy" style="width:{copy_w};vertical-align:middle;padding:32px 24px;font-family:Arial,Helvetica,sans-serif">
        <div style="font-size:11px;font-weight:700;letter-spacing:2.1px;line-height:1.2;color:{pal['c_soft']};padding-bottom:12px;mso-line-height-rule:exactly">WHY IT MATTERS</div>
        <div class="fs-240" style="font-family:Georgia,'Times New Roman',serif;font-size:22px;font-weight:700;letter-spacing:-0.015em;line-height:1.3;color:#ffffff;padding-bottom:12px;mso-line-height-rule:exactly">{_esc(heading)}</div>
        <div style="font-size:15px;line-height:1.6;color:#cdc6c1;mso-line-height-rule:exactly">{_esc(body)}</div>
      </td>
{img_cell}    </tr></tbody></table>
  </td></tr>
"""


def _headline_block(headline: str) -> str:
    if not headline:
        return ""
    return (
        '  <tr><td class="pad fs-347 dm-h" dir="ltr" '
        'style="font-family:Georgia,\'Times New Roman\',serif;font-size:31px;'
        'font-weight:700;letter-spacing:-0.02em;padding:0 24px 12px;line-height:1.16;'
        'mso-line-height-alt:36px;mso-line-height-rule:exactly;color:#171412">'
        f'{_esc(headline)}</td></tr>'
    )


def _cta_block(heading: str, subcopy: str, url: str, label: str, pal: Dict[str, str]) -> str:
    """The full CTA card. Omitted entirely on the closing step — a break-up
    note that still pushes for a meeting reads as tone-deaf."""
    if not url:
        return ""
    return f"""  <tr><td class="pad" style="padding:0 24px 32px">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" class="dm-cta" style="width:100%;background-color:{pal['c_cta_bg']};border:1px solid {pal['c_cta_border']};border-radius:16px"><tbody><tr><td align="center" style="padding:32px 24px;text-align:center;font-family:Arial,Helvetica,sans-serif">
      <div style="font-size:11px;font-weight:700;letter-spacing:2.4px;line-height:1.2;color:{pal['c_accent']};padding-bottom:12px;mso-line-height-rule:exactly">NEXT STEP</div>
      <div class="dm-h" style="font-family:Georgia,'Times New Roman',serif;font-size:24px;font-weight:700;letter-spacing:-0.01em;line-height:1.3;color:#171412;padding-bottom:12px;mso-line-height-rule:exactly">{_esc(heading)}</div>
      <div class="dm-b" style="font-size:15px;line-height:1.55;color:#443e39;padding-bottom:24px;mso-line-height-rule:exactly">{_esc(subcopy)}</div>
      <!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{_esc(url)}" style="height:52px;width:260px;v-text-anchor:middle;" arcsize="50%" fillcolor="{pal['c_link']}" stroked="f"><w:anchorlock/><v:textbox inset="0px,0px,0px,0px"><center dir="false" style="color:#ffffff;font-family:Arial,sans-serif;font-size:14px;font-weight:700;letter-spacing:1px">{_esc(label)}</center></v:textbox></v:roundrect><![endif]-->
      <!--[if !mso]><!-->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto"><tbody><tr>
        <td bgcolor="{pal['c_link']}" align="center" style="background-color:{pal['c_link']};border-radius:100px;text-align:center">
          <a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" style="color:#ffffff;text-decoration:none;display:block;padding:16px 32px;font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;letter-spacing:1.2px;line-height:18px;white-space:nowrap;mso-line-height-rule:exactly">{_esc(label)} &nbsp;&rarr;</a>
        </td>
      </tr></tbody></table>
      <!--<![endif]-->
    </td></tr></tbody></table>
  </td></tr>
"""


def _footer_line(company: str, postal_address: str = "") -> str:
    """Footer attribution + postal address.

    The address is DATA, never a table keyed by company name — it comes from
    the product's brand config (`icp['brand']['postal_address']`) or the
    NEXUS_DEFAULT_POSTAL_ADDRESS env default. A workspace with none set simply
    ships no address line.

    Note: a commercial email without a valid postal address does not meet
    CAN-SPAM, so leaving this unset is a compliance gap, not a style choice.
    """
    year = str(datetime.now(timezone.utc).year)
    line = f"&copy; {year} {_esc(company)}" if company else f"&copy; {year}"
    addr = (postal_address or "").strip()
    if addr:
        line += f" &middot; {_esc(addr)}"
    return line


def _footer_meta_block(unsubscribe_url: str, reason: str, pal: Dict[str, str]) -> str:
    """Opt-out + permission reminder.

    A one-click opt-out is required of commercial email under CAN-SPAM and is
    what separates cold outreach from spam in a recipient's eyes. It renders
    only when a URL is supplied — nothing here can invent one — so an absent
    `unsubscribe_url` is a live compliance gap, not a cosmetic one.
    """
    bits: List[str] = []
    if reason:
        bits.append(_esc(reason))
    if unsubscribe_url:
        bits.append(
            f'<a href="{_esc(unsubscribe_url)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:{pal["c_link"]};text-decoration:underline">Unsubscribe</a>'
        )
    if not bits:
        return ""
    return (
        '  <div class="dm-m" style="color:#443e39;font-size:11.5px;line-height:18px;'
        'padding-top:12px;mso-line-height-rule:exactly">'
        + " &middot; ".join(bits) +
        '</div>\n'
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def render_email_f1(
    lead: Mapping[str, Any],
    sender: Optional[Mapping[str, Any]] = None,
    gemini: Optional[Mapping[str, Any]] = None,
    step: int = 0,
) -> Dict[str, str]:
    """Render the f1 template. Mirrors `outreach_template.render_email`'s
    return shape: {"subject", "html", "text"}."""
    s: Dict[str, Any] = dict(sender or {})
    g: Dict[str, Any] = dict(gemini or {})
    images: Mapping[str, Any] = s.get("images") or {}

    company = str(s.get("company_name") or "").strip()
    company_url = str(s.get("company_url") or "").strip()
    first = str(lead.get("first_name") or "there").strip()

    pal = derive_palette(
        s.get("brand_color"), s.get("accent_color"), s.get("background_color")
    )

    # --- cadence step -------------------------------------------------------
    # Step 0 is the full pitch. Steps 1-3 keep the branded shell (masthead,
    # colours, signature, footer) but drop every showcase section — a 4-touch
    # cadence of identical full brochures reads as a mass mailing, and the
    # follow-up's job is one short personalised paragraph. Mirrors the rules
    # in sequencer._render_branded_step.
    try:
        step = int(step)
    except (TypeError, ValueError):
        step = 0
    if step not in _STEP_FIELDS:
        step = 0
    is_initial = step == 0
    show_cta = step in (0, 1, 2)   # no CTA on the closing note

    # Step-specific stored copy. The sequencer's `_step_content` builds the
    # gemini dict with step-keyed names ('followup_subject'/'followup_body'
    # etc.), so reading only 'subject'/'intro_body' would silently render the
    # INITIAL copy on every follow-up.
    subj_key, body_key = _STEP_FIELDS[step]
    subject = (
        str(g.get(subj_key) or "").strip()
        or str(g.get("subject") or "").strip()
        or f"Quick thought, {first}"
    )
    intro = (
        str(g.get(body_key) or "").strip()
        or str(g.get("intro_body") or "").strip()
    )
    # The big display headline belongs to the initial email only.
    headline = str(g.get("headline") or "").strip() or subject if is_initial else ""
    preheader = str(g.get("preheader") or "").strip() or headline or subject

    # --- masthead sub-rows -------------------------------------------------
    url_block = ""
    if company_url:
        href = company_url if company_url.startswith("http") else f"https://{company_url}"
        url_block = (
            '      <tr><td align="center" style="padding:8px 24px 0;text-align:center;'
            'font-family:Arial,Helvetica,sans-serif;font-size:13.5px;font-weight:700;'
            'letter-spacing:1.3px;line-height:1.2;text-transform:uppercase;'
            'mso-line-height-rule:exactly">'
            f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:{pal["c_link"]};text-decoration:none;font-weight:700">'
            f'{_esc(company_url)}</a></td></tr>'
        )

    # Platform attribution. The name is config, not a literal — a white-label
    # deployment sets NEXUS_EMAIL_ATTRIBUTION="" (or show_powered_by=False) and
    # the row disappears entirely.
    powered = ""
    attribution = str(
        s.get("attribution")
        if s.get("attribution") is not None
        else _env("NEXUS_EMAIL_ATTRIBUTION", "")
    ).strip()
    if attribution and s.get("show_powered_by", True):
        powered = (
            '      <tr><td align="right" class="dm-m" style="padding:16px 24px 12px;'
            'text-align:right;font-family:Arial,Helvetica,sans-serif;font-size:10.5px;'
            'font-weight:700;letter-spacing:1.5px;line-height:1.2;color:#443e39;'
            'text-transform:uppercase;mso-line-height-rule:exactly">'
            f'POWERED BY <span class="dm-h" style="color:#171412">'
            f'{_esc(attribution.upper())}</span></td></tr>'
        )

    # --- signature ---------------------------------------------------------
    rep_name = str(s.get("rep_name") or "").strip()
    rep_title = str(s.get("rep_title") or "").strip()
    sig_name = rep_name or (f"{company} Team" if company else "The team")
    sig_title_block = (
        f'  <tr><td dir="ltr" class="pad" style="font-size:15px;padding:0 24px 4px;'
        f'line-height:1.45;color:{pal["c_accent"]};mso-line-height-rule:exactly">'
        f'{_esc(rep_title)}</td></tr>'
        if rep_title else ""
    )
    sig_url_block = ""
    if company_url:
        href = company_url if company_url.startswith("http") else f"https://{company_url}"
        sig_url_block = (
            '  <tr><td dir="ltr" class="pad" style="font-size:15px;padding:0 24px 32px;'
            'line-height:1.45;mso-line-height-rule:exactly">'
            f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:{pal["c_link"]};text-decoration:none;font-weight:700">'
            f'{_esc(company_url)}</a></td></tr>'
        )

    # --- wordmark auto-fit -------------------------------------------------
    # Sized per company so a long name never overflows the 600px card. Inline
    # styles carry the desktop size (clients that strip <style> still get a
    # correct render); the emitted media query handles the mobile breakpoint.
    wm_text = company.upper() or "•"
    has_logo = bool(images.get("logo_url"))
    head_avail_d = _W_DESKTOP - (_LOGO_COST if has_logo else 0.0)
    head_avail_m = _W_MOBILE - (_LOGO_COST if has_logo else 0.0)

    wm_d, wm_d_tr = fit_wordmark(wm_text, head_avail_d, base_px=38, min_px=20)
    wm_m, wm_m_tr = fit_wordmark(wm_text, head_avail_m, base_px=26, min_px=15)
    wf_d, wf_d_tr = fit_wordmark(wm_text, _W_DESKTOP, base_px=21, min_px=14)
    wf_m, wf_m_tr = fit_wordmark(wm_text, _W_MOBILE, base_px=18, min_px=12)

    wordmark_css = (
        "@media only screen and (max-width:599px){"
        f".wm{{font-size:{wm_m}px!important;letter-spacing:{wm_m_tr}px!important}}"
        f".wm-f{{font-size:{wf_m}px!important;letter-spacing:{wf_m_tr}px!important}}"
        "}"
    )

    # Per-product section copy (nexus_products.f1_sections). A per-lead
    # value in `gemini` still wins; the brand-neutral fallback is last.
    sections: Mapping[str, Any] = s.get("f1_sections") or {}

    mapping: Dict[str, str] = {
        **pal,
        "wordmark_css": wordmark_css,
        "wm_style": f"font-size:{wm_d}px;letter-spacing:{wm_d_tr}px",
        "wm_footer_style": f"font-size:{wf_d}px;letter-spacing:{wf_d_tr}px",
        "headline": _esc(headline),
        "preheader": _esc(preheader),
        "first_name": _esc(first),
        "intro_body": _esc(intro),
        "wordmark": _esc(company.upper() or "•"),
        "logo_block": _logo_block(str(images.get("logo_url") or "")),
        "company_url_block": url_block,
        "powered_by_block": powered,
        "headline_block": _headline_block(headline),
        # Showcase sections — initial email only.
        "hero_block": _hero_block(
            str(images.get("hero_url") or ""),
            str(g.get("hero_alt") or f"{company} product overview").strip(),
            pal,
        ) if is_initial else "",
        "stats_block": _stats_block(s.get("proof_points") or [], pal) if is_initial else "",
        "capabilities_block": _capabilities_block(
            s.get("key_benefits") or [],
            company.upper(),
            _section(sections, g, "benefits_heading"),
            _section(sections, g, "benefits_sub"),
            pal,
        ) if is_initial else "",
        "editorial_block": _editorial_block(
            _section(sections, g, "editorial_heading"),
            _section(sections, g, "editorial_body"),
            str(images.get("editorial_url") or EDITORIAL_IMAGE_URL or ""),
            pal,
        ) if is_initial else "",
        "cta_block": _cta_block(
            _section(sections, g, "cta_heading"),
            _section(sections, g, "cta_subcopy"),
            str(s.get("cta_url") or ""),
            str(s.get("cta_label")
                or _env("NEXUS_DEFAULT_CTA_LABEL", "Book a quick call")).upper(),
            pal,
        ) if show_cta else "",
        "sig_name": _esc(sig_name),
        "sig_title_block": sig_title_block,
        "sig_url_block": sig_url_block,
        "footer_line": _footer_line(
            company,
            str(s.get("postal_address")
                or _env("NEXUS_DEFAULT_POSTAL_ADDRESS", "")),
        ),
        "footer_meta_block": _footer_meta_block(
            str(s.get("unsubscribe_url") or ""),
            str(s.get("permission_reminder") or "").strip(),
            pal,
        ),
    }

    html = _load_template().substitute(mapping)

    # --- plain-text alternative -------------------------------------------
    # Mirrors the HTML's per-step shape: benefits only on the initial, CTA
    # only where the button renders.
    parts = [f"Hi {first},\n\n{intro}\n\n"]
    if is_initial:
        for raw in (s.get("key_benefits") or [])[:4]:
            parts.append(
                f"- {raw}\n" if not isinstance(raw, Mapping)
                else f"- {raw.get('title','')}\n"
            )
    if show_cta and s.get("cta_url"):
        parts.append(f"\n{s.get('cta_label','Book a quick call')}: {s.get('cta_url')}\n")
    parts.append(f"\nBest regards,\n{sig_name}\n")
    if rep_title:
        parts.append(f"{rep_title}\n")
    if company_url:
        parts.append(f"{company_url}\n")

    return {"subject": subject, "html": html, "text": "".join(parts)}
