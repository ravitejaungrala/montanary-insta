"""Rich, branded, email-client-safe HTML building blocks for NEXUS outreach.

The marketing team hand-built attractive, role-segmented HTML emails (the
"Spenzo AI" references). This module reproduces that quality AUTOMATICALLY by
treating an email as a composition of small, reusable, tested SECTION BLOCKS.

Design rules (do not break these — they are what keep emails rendering in
Gmail AND Outlook):
  * Table-based layout, inline CSS only. No <style> blocks, no flexbox/grid.
  * Gradients/border-radius DEGRADE in Outlook's Word engine — every coloured
    surface also carries a solid `bgcolor` fallback so it never renders blank.
  * No raster images are required for content (the wordmark is styled TEXT,
    like the references). Images, if any, are optional + small.
  * WORD-DRIVEN: per product decision, blocks never rely on statistics or
    numbers. Number-style blocks (comparison / big-stat / stat-bar) take plain
    TEXT cells, so they show words, not figures.

This module is PURE: no DB, no network, no AI. It takes a `brand` dict + a
`variant` dict + a `lead` dict and returns ready-to-send HTML. That keeps it
trivially testable (render a sample to an .html file) and means it can never
break the live send path — callers fall back to the minimal renderer in
`outreach_template.render_email` if anything here returns None.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


# ---------------------------------------------------------------------------
# Escaping (kept local so this module has zero imports and stays testable)
# ---------------------------------------------------------------------------
def _esc(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_attr(s: Any) -> str:
    """Escape a value destined for an HTML attribute (e.g. href)."""
    return _esc(s).replace('"', "&quot;")


def _paragraphs(text: str, *, color: str = "#333333", size: int = 16) -> str:
    """Wrap each blank-line-separated paragraph in a styled <p>."""
    parts = [p.strip() for p in (str(text or "")).split("\n\n") if p.strip()]
    return "".join(
        f'<p style="margin:0 0 16px 0;font-family:{_FONT};font-size:{size}px;'
        f'color:{color};line-height:1.7;">{_esc(p).replace(chr(10), "<br/>")}</p>'
        for p in parts
    )


# ---------------------------------------------------------------------------
# Brand kit defaults — NEUTRAL, brand-agnostic fallbacks ONLY. Nothing here is
# tied to any specific product. At runtime every value is supplied per-customer
# from their Business DNA + product analysis; these defaults are used solely
# when a particular field is missing, so a missing value never breaks the email
# (and never imposes another brand's identity).
# ---------------------------------------------------------------------------
_FONT = "'Helvetica Neue',Helvetica,Arial,sans-serif"

DEFAULT_BRAND_KIT: Dict[str, Any] = {
    "wordmark": "",            # falls back to company_name
    "wordmark_accent": "",     # coloured tail of the wordmark (from DNA)
    "company_name": "",
    "logo_url": "",            # optional image logo; text wordmark used if empty
    # Fallback brand color — used ONLY when a product has no Business-DNA
    # color on file. Default to the brand orange-red so unmatched products
    # still render with an on-brand accent instead of a neutral grey.
    "brand_color": "#ff4500",  # default orange-red (buttons, links, highlights)
    "accent_color": "#ff4500",  # gradient partner — same orange-red
    "text_dark": "#111827",    # neutral dark surfaces / headings
    "page_bg": "#f5f5f5",      # outer canvas
    "header_style": "dark",    # "dark" | "light_strip"
    "site_url": "",            # shown in header / "Visit Site" pill
    "cta_url": "",
    "cta_label": "See how it works",
    "cta_subtext": "",
    "social": {},              # {"x": url, "linkedin": url, "instagram": url}
    "signature": {             # filled from connected mailbox at send time
        "rep_name": "",
        "rep_title": "",
        "rep_email": "",
        "rep_phone": "",
        "company_url": "",
    },
}


def _merged_brand(brand_kit: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    b = {**DEFAULT_BRAND_KIT, **(brand_kit or {})}
    b["social"] = {**DEFAULT_BRAND_KIT["social"], **(b.get("social") or {})}
    b["signature"] = {**DEFAULT_BRAND_KIT["signature"], **(b.get("signature") or {})}
    return b


def _merge(text: Any, ctx: Mapping[str, str]) -> str:
    """Replace the tiny merge surface ({{first_name}}, {{company}}) only."""
    out = str(text or "")
    out = out.replace("{{first_name}}", ctx.get("first_name", ""))
    out = out.replace("{{company}}", ctx.get("company", ""))
    return out


# ---------------------------------------------------------------------------
# Structural wrappers
# ---------------------------------------------------------------------------
def wrapper_open(brand: Mapping[str, Any]) -> str:
    bg = brand["page_bg"]
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        "<style>@media only screen and (max-width:620px){.hero-text{font-size:24px !important;line-height:32px !important;}}</style>"
        "</head>"
        f'<body style="margin:0;padding:0;background:{bg};">'
        f'<center style="width:100%;background:{bg};">'
        '<table role="presentation" width="600" cellspacing="0" cellpadding="0" '
        'border="0" align="center" style="max-width:600px;margin:0 auto;"><tbody>'
    )


def wrapper_close(brand: Mapping[str, Any]) -> str:
    return "</tbody></table></center></body></html>"


def preheader(text: str) -> str:
    """Hidden inbox-preview line (improves the snippet shown in the inbox)."""
    if not text:
        return ""
    return (
        '<div style="display:none;font-size:1px;color:#f5f5f5;line-height:1px;'
        'max-height:0;max-width:0;overflow:hidden;mso-hide:all;">'
        f"{_esc(text)}</div>"
    )


# ---------------------------------------------------------------------------
# Header — two styles seen in the references
# ---------------------------------------------------------------------------
def _wordmark(brand: Mapping[str, Any], *, on_dark: bool) -> str:
    wm = _esc(brand.get("wordmark") or brand.get("company_name") or "")
    acc = _esc(brand.get("wordmark_accent") or "")
    color = brand["brand_color"]
    base = "#ffffff" if on_dark else "#111111"
    logo = brand.get("logo_url")
    if logo:
        return (
            f'<img src="{_esc_attr(logo)}" alt="{wm}" height="26" '
            'style="display:block;border:0;outline:none;max-height:26px;">'
        )
    if not wm:
        return ""
    inner = f'{wm}<span style="color:{color};">{acc}</span>' if acc else wm
    head_color = color if (on_dark and not acc) else base
    return (
        f'<span style="font-family:{_FONT};font-size:22px;font-weight:800;'
        f'color:{head_color};letter-spacing:-0.5px;">{inner}</span>'
    )


def header(brand: Mapping[str, Any]) -> str:
    site = brand.get("site_url") or "#"
    site_label = _esc((brand.get("site_url") or "").replace("https://", "").replace("http://", "").strip("/")) or "Visit site"
    if (brand.get("header_style") or "dark") == "light_strip":
        grad = f"linear-gradient(90deg,{brand['brand_color']} 0%,{brand['accent_color']} 100%)"
        pill = brand["brand_color"]
        return (
            f'<tr><td style="background:{brand["brand_color"]};background:{grad};'
            'height:5px;font-size:0;line-height:0;">&nbsp;</td></tr>'
            '<tr><td style="background-color:#ffffff;padding:20px 36px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
            f"<td>{_wordmark(brand, on_dark=False)}</td>"
            f'<td align="right"><a href="{_esc_attr(site)}" '
            f'style="font-family:{_FONT};font-size:12px;font-weight:600;color:{pill};'
            f'text-decoration:none;border:1.5px solid {pill};padding:5px 14px;'
            'border-radius:20px;">Visit Site</a></td>'
            "</tr></table></td></tr>"
        )
    # dark header
    return (
        f'<tr><td style="background-color:{brand["text_dark"]};padding:20px 30px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f"<td>{_wordmark(brand, on_dark=True)}</td>"
        f'<td align="right"><a href="{_esc_attr(site)}" '
        f'style="font-family:{_FONT};font-size:13px;color:{brand["brand_color"]};text-decoration:none;">{site_label}</a></td>'
        "</tr></table></td></tr>"
    )


# ---------------------------------------------------------------------------
# Intro: greeting + optional hero framing + personalized opener
# ---------------------------------------------------------------------------
def intro(
    brand: Mapping[str, Any],
    *,
    first: str = "there",
    eyebrow: str = "",
    headline_html: str = "",
    subhead: str = "",
    opener: str = "",
) -> str:
    rows = [
        f'<p style="font-family:{_FONT};font-size:16px;color:#333333;'
        f'line-height:27px;margin:0 0 16px 0;">Hi {_esc(first)},</p>'
    ]
    if eyebrow:
        rows.append(
            f'<p style="font-family:{_FONT};font-size:12px;font-weight:700;'
            f'color:{brand["brand_color"]};letter-spacing:2px;text-transform:uppercase;'
            f'margin:0 0 10px 0;">{_esc(eyebrow)}</p>'
        )
    if headline_html:
        rows.append(
            f'<h1 class="hero-text" style="font-family:{_FONT};font-size:26px;'
            'font-weight:800;color:#0A0A0A;line-height:34px;letter-spacing:-0.5px;'
            f'margin:0 0 14px 0;">{headline_html}</h1>'
        )
    if opener:
        rows.append(_paragraphs(opener))
    if subhead:
        rows.append(_paragraphs(subhead))
    return (
        '<tr><td style="background-color:#ffffff;padding:40px 40px 12px 40px;">'
        + "".join(rows)
        + "</td></tr>"
    )


# ---------------------------------------------------------------------------
# Centerpiece blocks (the per-role differentiator)
# ---------------------------------------------------------------------------
def _card_open(brand: Mapping[str, Any], *, pad: int = 28) -> str:
    dark = brand["text_dark"]
    return (
        '<tr><td style="background-color:#ffffff;padding:8px 40px;">'
        f'<div style="background:{dark};background-color:{dark};border-radius:10px;padding:{pad}px;">'
    )


def _card_close() -> str:
    return "</div></td></tr>"


def cp_steps(brand: Mapping[str, Any], *, title: str = "", steps: Optional[List[Dict[str, str]]] = None) -> str:
    steps = steps or []
    color = brand["brand_color"]
    cells = []
    for i, st in enumerate(steps[:3]):
        n = str(st.get("n") or (i + 1))
        cells.append(
            '<td width="30%" style="text-align:center;padding:0 4px;vertical-align:top;">'
            f'<div style="width:40px;height:40px;border-radius:50%;background:{color};'
            f'background-color:{color};text-align:center;line-height:40px;font-family:{_FONT};'
            f'font-size:16px;font-weight:900;color:#fff;margin:0 auto 8px;">{_esc(n)}</div>'
            f'<p style="font-family:{_FONT};font-size:13px;font-weight:700;color:#ffffff;margin:0 0 4px 0;">{_esc(st.get("title"))}</p>'
            f'<p style="font-family:{_FONT};font-size:11px;color:#888;line-height:16px;margin:0;">{_esc(st.get("text"))}</p>'
            "</td>"
        )
        if i < min(len(steps), 3) - 1:
            cells.append(f'<td width="5%" style="text-align:center;vertical-align:middle;"><span style="font-size:14px;color:{color};font-weight:900;">&rarr;</span></td>')
    title_html = (
        f'<p style="font-family:{_FONT};font-size:13px;font-weight:700;color:{color};'
        f'letter-spacing:1.5px;text-transform:uppercase;margin:0 0 20px 0;text-align:center;">{_esc(title)}</p>'
        if title else ""
    )
    return (
        _card_open(brand)
        + title_html
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        + "".join(cells)
        + "</tr></table>"
        + _card_close()
    )


def cp_quote(brand: Mapping[str, Any], *, quote: str = "", attribution: str = "") -> str:
    attr = (
        f'<p style="font-family:{_FONT};font-size:12px;color:#888;margin:12px 0 0 0;text-align:center;">— {_esc(attribution)}</p>'
        if attribution else ""
    )
    return (
        _card_open(brand, pad=24)
        + f'<p style="font-family:{_FONT};font-size:16px;color:#ffffff;line-height:28px;'
          f'font-style:italic;text-align:center;margin:0;">{_esc(quote).replace(chr(10), "<br>")}</p>'
        + attr
        + _card_close()
    )


def cp_comparison_table(
    brand: Mapping[str, Any],
    *,
    title: str = "",
    columns: Optional[List[str]] = None,
    rows: Optional[List[List[str]]] = None,
) -> str:
    """Word-driven comparison (no numbers). The column at `highlight_index`
    (default 1 = the 'with us' column) is accented."""
    columns = columns or []
    rows = rows or []
    color = brand["brand_color"]
    hi = 1 if len(columns) > 1 else 0

    def _cell(text: str, *, head: bool, idx: int) -> str:
        accent = idx == hi
        bg = "background:rgba(255,69,0,0.15);" if accent else ""
        fg = color if accent else ("#888" if head else "#fff")
        weight = "800" if (accent or head) else "600"
        size = "12px" if head else "13px"
        align = "left" if idx == 0 else "center"
        border = "" if head else "border-top:1px solid rgba(255,255,255,0.08);"
        return (
            f'<td style="padding:10px 8px;text-align:{align};{bg}{border}">'
            f'<p style="font-family:{_FONT};font-size:{size};color:{fg};font-weight:{weight};margin:0;'
            f'{"letter-spacing:1px;text-transform:uppercase;" if head else ""}">{_esc(text)}</p></td>'
        )

    head_html = "<tr>" + "".join(_cell(c, head=True, idx=i) for i, c in enumerate(columns)) + "</tr>"
    body_html = "".join(
        "<tr>" + "".join(_cell(c, head=False, idx=i) for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    title_html = (
        f'<p style="font-family:{_FONT};font-size:18px;font-weight:800;color:#ffffff;'
        f'text-align:center;margin:0 0 16px 0;">{_esc(title)}</p>'
        if title else ""
    )
    return (
        _card_open(brand)
        + title_html
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        + head_html + body_html
        + "</table>"
        + _card_close()
    )


def cp_big_phrase(brand: Mapping[str, Any], *, phrase: str = "", caption: str = "") -> str:
    """Giant accent PHRASE (word-driven replacement for a giant stat)."""
    color = brand["brand_color"]
    cap = (
        f'<p style="font-family:{_FONT};font-size:15px;color:#ffffff;line-height:24px;margin:8px 0 0 0;">{_esc(caption).replace(chr(10), "<br>")}</p>'
        if caption else ""
    )
    return (
        _card_open(brand, pad=32)
        + f'<p style="font-family:{_FONT};font-size:30px;font-weight:900;color:{color};'
          f'line-height:38px;letter-spacing:-1px;margin:0;text-align:center;">{_esc(phrase)}</p>'
        + cap
        + _card_close()
    )


def cp_callout(brand: Mapping[str, Any], *, title: str = "", body: str = "") -> str:
    color = brand["brand_color"]
    title_html = f'<strong style="color:{color};">{_esc(title)}</strong> ' if title else ""
    return (
        '<tr><td style="background-color:#ffffff;padding:8px 40px;">'
        f'<div style="background:#FFF8F5;background-color:#FFF8F5;border-left:4px solid {color};'
        'padding:20px 24px;border-radius:0 8px 8px 0;">'
        f'<p style="font-family:{_FONT};font-size:15px;color:#333;line-height:26px;margin:0;">'
        f'{title_html}{_esc(body)}</p></div></td></tr>'
    )


def cp_feature_list(brand: Mapping[str, Any], *, title: str = "", items: Optional[List[Dict[str, str]]] = None) -> str:
    items = items or []
    color = brand["brand_color"]
    rows = []
    for i, it in enumerate(items):
        border = "" if i == len(items) - 1 else "border-bottom:1px solid rgba(255,255,255,0.08);"
        rows.append(
            f'<tr><td style="padding:8px 0;{border}">'
            f'<p style="font-family:{_FONT};font-size:14px;color:{color};font-weight:700;margin:0;">{_esc(it.get("label"))}</p>'
            f'<p style="font-family:{_FONT};font-size:13px;color:#aaa;margin:2px 0 0 0;">{_esc(it.get("sub"))}</p>'
            "</td></tr>"
        )
    title_html = (
        f'<p style="font-family:{_FONT};font-size:14px;font-weight:700;color:{color};'
        f'margin:0 0 16px 0;text-align:center;letter-spacing:1px;text-transform:uppercase;">{_esc(title)}</p>'
        if title else ""
    )
    return (
        _card_open(brand, pad=24)
        + title_html
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        + "".join(rows)
        + "</table>"
        + _card_close()
    )


def cp_feature_cards(brand: Mapping[str, Any], *, title: str = "", cards: Optional[List[Dict[str, str]]] = None) -> str:
    cards = cards or []
    color = brand["brand_color"]

    def _card(c: Dict[str, str]) -> str:
        return (
            f'<div style="background:#FFF8F5;background-color:#FFF8F5;border-left:4px solid {color};'
            'padding:14px 16px;border-radius:0 8px 8px 0;margin-bottom:10px;">'
            f'<p style="font-family:{_FONT};font-size:13px;font-weight:700;color:{color};margin:0 0 4px 0;">{_esc(c.get("title"))}</p>'
            f'<p style="font-family:{_FONT};font-size:12px;color:#555;line-height:18px;margin:0;">{_esc(c.get("body"))}</p></div>'
        )

    left = cards[0::2]
    right = cards[1::2]
    title_html = (
        f'<tr><td style="background-color:#ffffff;padding:16px 40px 4px 40px;">'
        f'<p style="font-family:{_FONT};font-size:15px;font-weight:700;color:#0A0A0A;margin:0;">{_esc(title)}</p></td></tr>'
        if title else ""
    )
    return (
        title_html
        + '<tr><td style="background-color:#ffffff;padding:8px 40px 0 40px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td width="48%" style="vertical-align:top;padding-right:8px;">{"".join(_card(c) for c in left)}</td>'
        f'<td width="48%" style="vertical-align:top;padding-left:8px;">{"".join(_card(c) for c in right)}</td>'
        "</tr></table></td></tr>"
    )


def cp_stat_bar(brand: Mapping[str, Any], *, items: Optional[List[Dict[str, str]]] = None) -> str:
    """3-up bar. Word-driven: each item is {value(word), label}."""
    items = items or []
    color = brand["brand_color"]
    n = len(items) or 1
    w = int(100 / n)
    cells = []
    for i, it in enumerate(items):
        border = "border-right:1px solid rgba(255,255,255,0.15);" if i < len(items) - 1 else ""
        cells.append(
            f'<td width="{w}%" style="text-align:center;{border}">'
            f'<p style="font-family:{_FONT};font-size:24px;font-weight:900;color:{color};margin:0;">{_esc(it.get("value"))}</p>'
            f'<p style="font-family:{_FONT};font-size:10px;color:rgba(255,255,255,0.6);line-height:14px;margin:4px 0 0 0;">{_esc(it.get("label")).replace(chr(10), "<br>")}</p>'
            "</td>"
        )
    return (
        _card_open(brand, pad=24)
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        + "".join(cells)
        + "</tr></table>"
        + _card_close()
    )


# ---------------------------------------------------------------------------
# Body extras + closers
# ---------------------------------------------------------------------------
def bullet_list(brand: Mapping[str, Any], *, title: str = "", items: Optional[List[str]] = None) -> str:
    items = items or []
    color = brand["brand_color"]
    title_html = (
        f'<p style="font-family:{_FONT};font-size:15px;font-weight:700;color:#0A0A0A;margin:0 0 12px 0;">{_esc(title)}</p>'
        if title else ""
    )
    rows = "".join(
        f'<span style="color:{color};">&rarr;</span> {_esc(it)}<br>' for it in items
    )
    return (
        '<tr><td style="background-color:#ffffff;padding:20px 40px 8px 40px;">'
        + title_html
        + f'<p style="font-family:{_FONT};font-size:15px;color:#333;line-height:28px;margin:0;">{rows}</p>'
        + "</td></tr>"
    )


def cta(brand: Mapping[str, Any], *, label: str = "", url: str = "#", subtext: str = "") -> str:
    color = brand["brand_color"]
    sub = (
        f'<p style="font-family:{_FONT};font-size:13px;color:#999;margin:12px 0 0 0;">{_esc(subtext)}</p>'
        if subtext else ""
    )
    return (
        '<tr><td style="background-color:#ffffff;padding:24px 40px 40px;text-align:center;">'
        f'<a href="{_esc_attr(url or "#")}" style="display:inline-block;background:{color};'
        f'background-color:{color};color:#ffffff;font-family:{_FONT};font-size:15px;font-weight:700;'
        f'padding:14px 36px;border-radius:4px;text-decoration:none;">{_esc(label)} &rarr;</a>'
        + sub
        + "</td></tr>"
    )


def signature(brand: Mapping[str, Any]) -> str:
    sig = brand.get("signature") or {}
    name = _esc(sig.get("rep_name") or (f'{brand.get("company_name")} Team' if brand.get("company_name") else "The team"))
    color = brand["brand_color"]
    lines = [f'<strong style="color:#0A0A0A;">{name}</strong>']
    if sig.get("rep_title"):
        lines.append(f'<span style="color:#555555;">{_esc(sig["rep_title"])}</span>')
    url = sig.get("company_url") or brand.get("site_url")
    if url:
        disp = _esc((str(url) or "").replace("https://", "").replace("http://", "").strip("/"))
        # Absolute href — a relative "spenzo.ai" makes Outlook auto-linkify the
        # text too, rendering as "[spenzo.ai]spenzo.ai".
        href = str(url) if str(url).startswith(("http://", "https://")) else f"https://{disp}"
        lines.append(f'<a href="{_esc_attr(href)}" style="color:{color};text-decoration:none;">{disp}</a>')
    if sig.get("rep_phone"):
        lines.append(f'<span style="color:{color};">{_esc(sig["rep_phone"])}</span>')
    body = "<br>".join(lines)
    return (
        '<tr><td style="background-color:#ffffff;padding:0 40px 36px 40px;'
        'border-top:1px solid #eeeeee;padding-top:28px;">'
        f'<p style="font-family:{_FONT};font-size:15px;color:#333333;line-height:26px;margin:0;">'
        f'Best regards,</p>'
        f'<p style="font-family:{_FONT};font-size:15px;color:#333333;line-height:26px;margin:6px 0 0 0;">{body}</p>'
        "</td></tr>"
    )


# Stable, hosted social icons (same source the references used).
_SOCIAL_ICONS = {
    "x": "https://img.icons8.com/color/48/twitterx--v1.png",
    "linkedin": "https://img.icons8.com/color/48/linkedin.png",
    "instagram": "https://img.icons8.com/color/48/instagram-new--v1.png",
}


def footer(brand: Mapping[str, Any]) -> str:
    """Minimal footer. NO 'manage preferences' / unsubscribe — for 1:1 cold
    outreach that signals bulk and hurts deliverability. Social icons only if
    provided."""
    social = brand.get("social") or {}
    icons = []
    for key, url in social.items():
        icon = _SOCIAL_ICONS.get(key)
        if not url or not icon:
            continue
        icons.append(
            f'<td style="padding:0 8px;" valign="middle" align="center">'
            f'<a href="{_esc_attr(url)}" style="text-decoration:none;">'
            f'<img src="{icon}" width="22" height="22" alt="{_esc(key)}" '
            'style="display:block;border:0;outline:none;"></a></td>'
        )
    icons_html = (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" '
        f'style="margin-bottom:14px;"><tr>{"".join(icons)}</tr></table>'
        if icons else ""
    )
    # Company-name line removed — the signature already shows the brand +
    # domain, so a footer brand line is redundant. Render the footer ONLY when
    # there are social icons to show; otherwise end the email at the signature.
    if not icons:
        return ""
    return (
        f'<tr><td style="background-color:{brand["page_bg"]};padding:20px 40px;text-align:center;">'
        + icons_html
        + "</td></tr>"
    )


# ---------------------------------------------------------------------------
# Block registry — a variant's middle section is an ordered list of these.
# ---------------------------------------------------------------------------
BLOCK_BUILDERS = {
    "steps": cp_steps,
    "quote": cp_quote,
    "comparison_table": cp_comparison_table,
    "big_phrase": cp_big_phrase,
    "callout": cp_callout,
    "feature_list": cp_feature_list,
    "feature_cards": cp_feature_cards,
    "stat_bar": cp_stat_bar,
    "bullet_list": bullet_list,
}


# ---------------------------------------------------------------------------
# Role matching + assembly
# ---------------------------------------------------------------------------
def select_variant(
    variants: Optional[List[Mapping[str, Any]]],
    lead_title: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Pick the role variant whose keywords match the lead's title. Falls back
    to the variant flagged `default`, else the first. Returns None when there
    are no variants (caller then uses the minimal renderer)."""
    if not variants:
        return None
    title = (lead_title or "").lower()
    if title:
        for v in variants:
            for kw in (v.get("role_match_keywords") or []):
                if kw and str(kw).lower() in title:
                    return dict(v)
    for v in variants:
        if v.get("default"):
            return dict(v)
    return dict(variants[0])


def _merge_content(content: Dict[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    """Merge {{first_name}}/{{company}} into a block's string + nested fields."""
    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return _merge(v, ctx)
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        return v
    return {k: _walk(v) for k, v in content.items()}


def _plain_text(brand: Mapping[str, Any], *, first: str, opener: str, hero: Mapping[str, Any], variant: Mapping[str, Any], cta_url: str, cta_label: str) -> str:
    """A real text/plain alternative (multipart improves inbox placement)."""
    parts = [f"Hi {first},", ""]
    if opener:
        parts.append(opener)
    # Subhead removed — redundant with the per-lead intro_body now in `opener`.
    for blk in variant.get("blocks", []):
        for key in ("title", "body", "quote", "phrase", "caption"):
            if blk.get(key):
                parts.append(str(blk[key]))
        for it in (blk.get("items") or []):
            if isinstance(it, dict):
                parts.append(f"- {it.get('label') or it.get('value') or ''}: {it.get('sub') or it.get('label') or ''}".strip(": "))
            else:
                parts.append(f"- {it}")
    if cta_label:
        parts.append("")
        parts.append(f"{cta_label}: {cta_url}")
    # Keep this in step with `signature()` (the HTML block): same name, same
    # fallback, and the role too. The text/plain alternative is what some
    # clients actually display, so a different signature here means the same
    # email signs two different ways depending on the reader.
    sig = brand.get("signature") or {}
    parts.append("")
    parts.append("Best regards,")
    parts.append(
        sig.get("rep_name")
        or (f'{brand.get("company_name")} Team' if brand.get("company_name") else "The team")
    )
    if sig.get("rep_title"):
        parts.append(str(sig["rep_title"]))
    return "\n".join(p for p in parts if p is not None)


def render_rich_email(
    brand_kit: Optional[Mapping[str, Any]],
    variant: Optional[Mapping[str, Any]],
    lead: Mapping[str, Any],
    step: int = 0,
    opener: str = "",
) -> Optional[Dict[str, str]]:
    """Compose a rich branded email from brand kit + role variant + lead.

    `opener` is the per-lead personalized line(s) written by the content model
    (falls back to the variant's own opener for previews). Returns
    {subject, html, text}, or None to signal "fall back to the minimal
    renderer" (no variant, or assembly failed)."""
    if not variant:
        return None
    try:
        brand = _merged_brand(brand_kit)
        first = (lead.get("first_name") or "there").strip()
        ctx = {"first_name": first, "company": (lead.get("company_name") or "").strip()}

        subject = _merge(variant.get("subject"), ctx).strip() or f"Quick note, {first}"
        pre = _merge(variant.get("preheader"), ctx).strip()
        hero = _merge_content(dict(variant.get("hero") or {}), ctx)
        opener_txt = _merge(opener or variant.get("opener"), ctx).strip()

        out: List[str] = [wrapper_open(brand)]
        if pre:
            out.append(preheader(pre))
        out.append(header(brand))
        out.append(intro(
            brand,
            first=first,
            eyebrow=hero.get("eyebrow", ""),
            headline_html=hero.get("headline_html", ""),
            # Subhead removed — the per-lead intro_body (in `opener`) now carries
            # the body, so the hero subhead was redundant repetition.
            subhead="",
            opener=opener_txt,
        ))
        for blk in (variant.get("blocks") or []):
            builder = BLOCK_BUILDERS.get(blk.get("type"))
            if not builder:
                continue
            try:
                content = _merge_content({k: v for k, v in blk.items() if k != "type"}, ctx)
                out.append(builder(brand, **content))
            except Exception:
                continue  # skip one bad block, keep the rest

        # Keep the CTA label and URL PAIRED so a variant's label (e.g.
        # "Explore the platform") can't end up sitting on top of the brand's
        # booking URL. Use the variant's CTA only when it supplies its OWN
        # url; otherwise use the brand's label + url together (the booking
        # link with its matching "Book a quick call" label).
        if variant.get("cta_url"):
            cta_url = variant["cta_url"]
            cta_label = _merge(variant.get("cta_label") or brand.get("cta_label"), ctx)
        else:
            cta_url = brand.get("cta_url") or "#"
            cta_label = _merge(brand.get("cta_label") or variant.get("cta_label"), ctx)
        # CTA button shows on EVERY step, including the closing — the close
        # stays low-pressure in its copy, but still offers a soft "book a call"
        # option for anyone who's now ready to act.
        if cta_label:
            out.append(cta(brand, label=cta_label, url=cta_url, subtext=_merge(variant.get("cta_subtext") or brand.get("cta_subtext"), ctx)))

        out.append(signature(brand))
        out.append(footer(brand))
        out.append(wrapper_close(brand))

        html = "".join(out)
        if "<td" not in html:
            return None
        text = _plain_text(brand, first=first, opener=opener_txt, hero=hero, variant=variant, cta_url=cta_url, cta_label=cta_label)
        return {"subject": subject, "html": html, "text": text}
    except Exception:
        return None
