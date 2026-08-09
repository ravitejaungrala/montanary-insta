"""Canonical URL normalization for product/campaign deduplication.

Single source of truth used by every place we compare URLs. Previously there
were 3 different normalizations in the codebase (raw equality at `/analyze`
upsert, `TRIM(BOTH '/' FROM LOWER(...))` in some SQL CTEs, Python
`.strip().lower().rstrip('/')` elsewhere). Different normalizations =
silent duplicates: `https://Z-Ninth.com/` vs `https://z-ninth.com` would
collide at one site and diverge at another. This module standardizes.

The normalization is intentionally CONSERVATIVE — we only lowercase + trim
trailing slashes. We do NOT strip scheme, www, query string, or fragment.
Two URLs that differ in scheme (`http` vs `https`) or `www.` prefix may
legitimately be different products in some workspaces, and silently
merging them would be more dangerous than the dedup we gain. If the user
needs aggressive normalization later we can add an `aggressive=True` flag.

Applied at COMPARISON time only — we never overwrite stored URL values.
That way existing rows keep their exact original URL string (the user's
intent), and dedup happens by computing the normalized key on both sides
of the WHERE clause.

Public surface:
    normalize(url)            -> str    Python-side canonical key
    SQL_NORMALIZE_EXPR        -> str    SQL fragment for WHERE / GROUP BY
                                        (uses LOWER + TRIM + COALESCE)
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


# Common multi-part public suffixes — so `example.co.uk` keys to "example",
# not "co". (Not exhaustive; covers the suffixes our customers actually use.)
_MULTI_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "net.nz", "org.nz", "co.za", "org.za",
    "com.br", "com.sg", "com.mx", "com.tr", "com.cn", "com.hk",
    "co.jp", "co.kr", "com.my", "com.ph", "com.vn",
}


def brand_key(url: Optional[str]) -> str:
    """Group a SENDER BRAND by its DOMAIN NAME ONLY — the registrable
    second-level label, ignoring the TLD, subdomains, scheme, `www.`, path, and
    hyphens. So a company's site collapses to ONE brand regardless of which TLD
    variant a user pastes.

    Used by the Connectors brand cards + campaign-match side (must be identical
    on both). Applies whether the URL came from new-campaign scraping or was
    typed directly on the Connectors page.

    Examples:
        "https://www.spenzo.ai/foo?x=1" -> "spenzo"
        "spenzo.io"                     -> "spenzo"
        "https://spenzo.com"            -> "spenzo"
        "https://z-ninth.com"           -> "zninth"   (hyphen folded)
        "blog.acme.co.uk"               -> "acme"     (multi-part TLD + subdomain)
        "" / None                       -> ""

    NOTE: this intentionally merges same-name brands across TLDs (per product
    decision) — `delta.com` and `delta.io` would be treated as one brand.
    """
    if not url:
        return ""
    s = str(url).strip().lower()
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    labels = [seg for seg in host.split(".") if seg]
    if not labels:
        return ""
    if len(labels) == 1:
        sld = labels[0]
    else:
        last_two = ".".join(labels[-2:])
        if last_two in _MULTI_PART_SUFFIXES and len(labels) >= 3:
            sld = labels[-3]          # example.co.uk -> "example"
        else:
            sld = labels[-2]          # spenzo.ai -> "spenzo"
    # Fold hyphen variants of the same name (z-ninth == zninth).
    return sld.replace("-", "")


def normalize(url: Optional[str]) -> str:
    """Return the canonical key for URL-based deduplication.

    Rules:
      - Empty / None -> "" (callers should treat empty as "no URL")
      - Lowercase the whole string
      - Strip leading/trailing whitespace
      - Strip a single trailing slash if present
      - Preserve scheme, www, query, fragment, path otherwise

    Examples:
        "https://Z-Ninth.com/"  -> "https://z-ninth.com"
        "  https://z-ninth.com" -> "https://z-ninth.com"
        "https://z-ninth.com/about" -> "https://z-ninth.com/about"
        "https://z-ninth.com/?utm=x" -> "https://z-ninth.com/?utm=x"  (paths/queries preserved)
        ""                       -> ""
        None                     -> ""
    """
    if not url:
        return ""
    s = str(url).strip().lower()
    if s.endswith("/"):
        s = s[:-1]
    return s


# SQL expression that produces the same canonical key as `normalize()` above
# when applied to a column. Use this in WHERE / GROUP BY clauses so the
# Python and SQL paths agree exactly:
#
#   db.execute(text(
#       f"SELECT id FROM nexus_products "
#       f"WHERE workspace_id = :w AND {SQL_NORMALIZE_EXPR.format('source_url')} = :u"
#   ), {"w": ws_id, "u": normalize(input_url)})
#
# Order of ops matches the Python helper exactly:
#   1. COALESCE NULL → '' so NULL columns don't match empty strings accidentally
#   2. BTRIM strips leading + trailing whitespace (matches Python .strip())
#   3. LOWER lowercases
#   4. REGEXP_REPLACE strips a single trailing slash
SQL_NORMALIZE_EXPR = (
    "REGEXP_REPLACE(LOWER(BTRIM(COALESCE({}, ''))), '/$', '')"
)
