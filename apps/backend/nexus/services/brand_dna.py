"""Fetch brand COLORS for a NEXUS product from the existing Pipelyt Business
DNA, matched by the product's website URL.

Per product decision, the outreach email reuses ONLY the colors from Business
DNA (NOT its fonts, tagline, or wordmark). The wordmark is the product/company
name, the font is our fixed email-safe stack, and there is no tagline.

Business DNA lives in `users.business_dna` (a JSON blob produced by
`services/business_dna_service.py`). Shape (relevant bits):

    {
      "colors": {"primary": "#hex", "secondary": "#hex",
                 "accent": "#hex", "background": "#hex"},   # the user's main brand
      "products": { "<id>": {"colors": {...}, "url"|"website": "...", ...}, ... }
    }

Matching strategy (defensive — tolerates whichever shape is present):
  1. a `products[*]` entry whose URL matches the product's source_url domain;
  2. the top-level brand if `users.business_url` matches that domain;
  3. otherwise the top-level brand, or the sole product if there's exactly one.

Returns {} when nothing usable is found — the caller then uses neutral
defaults, so a missing DNA never breaks or mis-brands the email.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.brand_dna")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _domain(url: Optional[str]) -> str:
    """Bare host (no scheme/www/trailing slash), lowercased."""
    if not url:
        return ""
    u = str(url).strip().lower()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        host = urlparse(u).netloc or ""
    except Exception:
        host = ""
    return host.replace("www.", "").strip("/")


def _canon(url: Optional[str]) -> str:
    """Normalised host for MATCHING only: the bare domain with hyphens removed,
    so a brand registered under both spellings — e.g. 'z-ninth.com' and
    'zninth.com' — compares equal. Safe against cross-tenant over-matching
    because fetch_brand_colors only ever compares domains WITHIN a single
    owner's Business DNA (a user won't own two different companies that differ
    only by a hyphen)."""
    return _domain(url).replace("-", "")


def _is_hex(v: Any) -> bool:
    return isinstance(v, str) and bool(_HEX_RE.match(v.strip()))


def _colors_from(entry: Any) -> Dict[str, str]:
    """Extract a usable {brand_color, accent_color, background_color} from a
    DNA brand/product dict's `colors` block. Returns {} if no valid primary
    hex is present."""
    if not isinstance(entry, dict):
        return {}
    colors = entry.get("colors")
    if not isinstance(colors, dict):
        return {}
    primary = (colors.get("primary") or "").strip()
    if not _is_hex(primary):
        return {}
    secondary = (colors.get("accent") or colors.get("secondary") or "").strip()
    accent = secondary if _is_hex(secondary) else primary
    out = {"brand_color": primary, "accent_color": accent}
    # Business DNA already stores the site's background; it was previously
    # dropped here, so products resolved on this path could never tint their
    # email surfaces. `derive_palette` takes the HUE only and re-sets
    # lightness, so a dark site background can never darken the email.
    background = (colors.get("background") or "").strip()
    if _is_hex(background):
        out["background_color"] = background
    return out


def fetch_brand_colors(
    db: Session,
    user_id: Optional[int],
    source_url: Optional[str],
) -> Dict[str, str]:
    """Return {brand_color, accent_color} from the owner's Business DNA matched
    by the product's website, or {} if unavailable."""
    if not user_id:
        return {}
    try:
        row = db.execute(
            text("SELECT business_url, business_dna FROM users WHERE id = :id LIMIT 1"),
            {"id": user_id},
        ).mappings().first()
    except Exception:
        log.exception("brand_dna: users lookup failed")
        return {}
    if not row:
        return {}

    dna = row.get("business_dna")
    if isinstance(dna, str):
        try:
            dna = json.loads(dna)
        except Exception:
            dna = None
    if not isinstance(dna, dict):
        return {}

    target = _domain(source_url)
    target_c = _canon(source_url)  # hyphen-insensitive form for matching
    products = dna.get("products") if isinstance(dna.get("products"), dict) else {}

    # 1. product entry whose URL matches the target domain
    if target:
        for entry in products.values():
            if not isinstance(entry, dict):
                continue
            entry_url = (
                entry.get("url") or entry.get("website")
                or entry.get("source_url") or entry.get("business_url") or ""
            )
            if entry_url and _canon(entry_url) == target_c:
                c = _colors_from(entry)
                if c:
                    return c

    # 2. top-level brand ONLY if the account's own business_url matches the
    #    target domain (i.e. the product IS the parent company's site).
    if target and _canon(row.get("business_url")) == target_c:
        c = _colors_from(dna)
        if c:
            return c

    # No EXACT match → return {} so the caller uses NEUTRAL defaults. We must
    # NOT borrow the top-level account brand or another product's colors — that
    # produced z-ninth (blue) emails in NeuZenAI's orange. A product gets its
    # real colors only once it has its own Business DNA entry.
    return {}
