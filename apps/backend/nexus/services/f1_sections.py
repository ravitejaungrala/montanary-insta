"""Per-product section copy for the f1 outreach email.

The f1 template has three copy blocks that are properties of the PRODUCT, not
the lead — the benefits heading, the dark "why it matters" panel, and the CTA
card. They do not change between prospects, so generating them per draft would
be waste AND would let the same campaign drift in wording between recipients.

Generated once, cached in `nexus_products.f1_sections`, read by
`sequencer._build_sender_ctx_for_product` and resolved by the renderer as:

    per-lead override (gemini) -> per-product cache -> brand-neutral fallback

What is deliberately NOT generated here
---------------------------------------
The stat strip (`proof_points`). Those are factual performance claims about a
real company — "3.2x avg ROAS", "$10M+ optimized". An LLM asked for them will
invent plausible numbers, and publishing invented performance data under a
customer's brand is a different class of problem from an awkward heading. Proof
points must be entered by the operator or extracted from the customer's own
published material; the strip simply stays hidden until then.

Contract mirrors brand_assets: never raises, returns {} when unsure.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

log = logging.getLogger("nexus.f1_sections")

# Keys the renderer looks for. Anything else in the blob is ignored.
FIELDS = (
    "attribution",
    "capabilities",
    "benefits_heading",
    "benefits_sub",
    "editorial_heading",
    "editorial_body",
    "cta_heading",
    "cta_subcopy",
)

# Length ceilings matched to the template's slots — the benefits heading sits
# at 28px in a 552px column, the editorial heading at 22px in a 54%-width dark
# panel. Longer strings wrap into the layout and look broken.
_MAX_LEN = {
    "attribution": 28,
    "benefits_heading": 42,
    "benefits_sub": 110,
    "editorial_heading": 52,
    "editorial_body": 130,
    "cta_heading": 46,
    "cta_subcopy": 90,
}

_SYSTEM = (
    "You write B2B outreach email copy grounded in a company's own website. "
    "You are given scraped site content for ONE company. Return ONLY compact "
    "JSON.\n\n"
    "GROUNDING — this is the point of the task:\n"
    "- Every line must reflect something the SITE actually says. Name the "
    "real capabilities, integrations, workflows and outcomes it describes.\n"
    "- Prefer the specific over the categorical. 'Connects Salesforce to "
    "ServiceNow without custom code' beats 'Core platform capabilities'. "
    "'Model spend across paid and organic' beats 'Marketing capabilities'.\n"
    "- If the site names technologies, systems, channels or job functions it "
    "serves, use those words.\n"
    "- Do NOT write filler like 'Core platform capabilities', 'What you get', "
    "'Key features', 'Our solutions' — a heading that would fit any company "
    "is a failed heading.\n\n"
    "CONSTRAINTS:\n"
    "- Write about THIS company only. Never name another vendor as the seller.\n"
    "- No invented statistics, percentages, customer counts, currency amounts "
    "or award claims. Only numbers the site itself states.\n"
    "- No exclamation marks; avoid 'revolutionary', 'game-changing', 'unlock', "
    "'supercharge', 'seamless', 'cutting-edge', 'best-in-class'.\n"
    "- Sentence case, not Title Case. No trailing full stop on headings.\n"
    "- Respect every length limit exactly; they are layout constraints."
)

# How much scraped site text to hand the model. Enough for the homepage plus a
# couple of subpages; beyond this the signal-to-noise drops and cost rises.
_SITE_CHARS = 6000


def run_async_safely(make_coro):
    """Run a coroutine whether or not an event loop is already running.

    `asyncio.run()` raises RuntimeError when called from inside a running
    loop — which is every FastAPI handler and the async sequencer tick. Left
    unguarded, that error is swallowed by the caller's try/except and the
    product silently never gets copy or images, with nothing in the logs
    pointing at the cause. Today both callers are sync CLI scripts, so this
    is pre-emptive; it stops the trap being sprung the first time someone
    wires generation into a request path.
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())          # no loop — normal path
    # A loop is already running: execute in a worker thread with its own.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(make_coro())).result()


def fetch_site_text(source_url: str, db=None) -> str:
    """Homepage + high-signal subpages as plain text, or '' on failure.

    Uses the scraper's own cache, so a product analysed recently costs
    nothing here.
    """
    url = (source_url or "").strip()
    if not url:
        return ""
    try:
        from nexus.services import playwright_scraper as ps
    except Exception:
        return ""
    bundle = None
    try:
        bundle = ps.get_cached_bundle(url, db=db)
    except Exception:
        bundle = None
    if bundle is None:
        try:
            bundle = run_async_safely(lambda: ps.fetch_bundle(url))
            try:
                ps.cache_bundle(url, bundle, db=db)
            except Exception:
                pass
        except Exception:
            log.info("f1_sections: site fetch failed for %s", url, exc_info=True)
            return ""
    try:
        txt = getattr(bundle, "combined_text", "") or getattr(bundle, "text", "")
    except Exception:
        txt = ""
    return (txt or "")[:_SITE_CHARS]


def _user_prompt(product: Mapping[str, Any]) -> str:
    name = (product.get("name") or "the product").strip()
    vp = (product.get("value_prop") or product.get("value_proposition") or "").strip()
    benefits = product.get("key_benefits") or []
    if isinstance(benefits, str):
        benefits = [benefits]
    blist = "\n".join(f"- {b}" for b in list(benefits)[:6] if b)
    pd = product.get("product_description")
    pd_txt = ""
    if isinstance(pd, dict):
        pd_txt = " ".join(str(v) for v in pd.values() if v)[:900]
    elif isinstance(pd, str):
        pd_txt = pd[:900]

    site = (product.get("_site_text") or "").strip()

    return (
        f"COMPANY / PRODUCT: {name}\n"
        f"VALUE PROPOSITION: {vp or '(none given)'}\n"
        f"KEY BENEFITS:\n{blist or '(none given)'}\n"
        f"DESCRIPTION: {pd_txt or '(none given)'}\n\n"
        "----- SCRAPED WEBSITE CONTENT (your primary source) -----\n"
        f"{site or '(site unavailable — work from the fields above)'}\n"
        "----- END WEBSITE CONTENT -----\n\n"
        "Produce JSON with exactly these keys:\n"
        '  "benefits_heading"  <= 42 chars  — heading above the capability cards.\n'
        "                       Name what this company actually does, not a category.\n"
        '  "benefits_sub"      <= 110 chars — one concrete line under that heading\n'
        '  "editorial_heading" <= 52 chars  — the single sharpest reason this\n'
        "                       product matters to a buyer, in the site's own terms\n"
        '  "editorial_body"    <= 130 chars — one sentence of supporting substance\n'
        '  "cta_heading"       <= 46 chars  — heading on the closing call-to-action\n'
        '  "cta_subcopy"       <= 90 chars  — one reassuring line under the CTA\n'
        '  "capabilities"      — array of EXACTLY 4 objects, each:\n'
        '        {"title": <= 34 chars, "body": <= 95 chars}\n'
        "        The four strongest differentiators the SITE describes. Each\n"
        "        title names a real capability; each body says what it does for\n"
        "        the buyer. These are the email's main selling points — make\n"
        "        them specific enough that a competitor could not reuse them.\n"
    )


# Acronyms the sentence-case instruction lowercases into nonsense — "Custom
# data and ai solutions" instead of "AI". Restored after cleaning.
_ACRONYMS = (
    "AI", "ROI", "ROAS", "API", "APIs", "B2B", "B2C", "SaaS", "CRM", "ERP",
    "KPI", "KPIs", "SEO", "SEM", "IT", "ML", "LLM", "GTM", "SDK", "UX", "UI",
)
_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(sorted(_ACRONYMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_ACRONYM_CANON = {a.lower(): a for a in _ACRONYMS}


def _fix_acronyms(s: str) -> str:
    return _ACRONYM_RE.sub(lambda m: _ACRONYM_CANON[m.group(0).lower()], s)


def _clean(value: Any, key: str) -> str:
    """Trim, strip a trailing period from headings, enforce the length cap."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = " ".join(s.split())
    # A brand name is written how its owner writes it — leave attribution
    # alone rather than "correcting" e.g. "Ai Corp" into "AI Corp".
    if key != "attribution":
        s = _fix_acronyms(s)
    if key.endswith("heading"):
        s = s.rstrip(".")
    cap = _MAX_LEN.get(key)
    if cap and len(s) > cap:
        # Cut on a word boundary rather than mid-word.
        s = s[:cap].rsplit(" ", 1)[0].rstrip(",;:-") or s[:cap]
    return s


_GENERIC_HEADINGS = {
    "core platform capabilities", "what you get", "key features",
    "our solutions", "core capabilities", "platform capabilities",
    "key benefits", "our services", "core features", "what we do",
    "product capabilities", "main features", "our capabilities",
}

_CAP_MAX = {"title": 34, "body": 95}


def _clean_capabilities(raw: Any) -> List[Dict[str, str]]:
    """Up to 4 {title, body} pairs, trimmed to the card's slot widths."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, str]] = []
    for item in raw[:4]:
        if isinstance(item, Mapping):
            title, body = item.get("title"), item.get("body")
        elif isinstance(item, str):
            title, _, body = item.partition(" — ")
        else:
            continue
        t = _clean(title, "cap_title")[: _CAP_MAX["title"]].rstrip(" ,;:-")
        b = _clean(body, "cap_body")[: _CAP_MAX["body"]].rstrip(" ,;:-")
        if t:
            out.append({"title": t, "body": b})
    return out


def generate_f1_sections(product: Mapping[str, Any]) -> Dict[str, Any]:
    """One Gemini call -> the six section strings. {} on failure."""
    name = (product.get("name") or "").strip()
    if not name:
        return {}
    try:
        from nexus.services import gemini
    except Exception:
        log.info("f1_sections: gemini unavailable", exc_info=True)
        return {}

    parsed: Optional[dict] = None
    for attempt in range(2):
        try:
            raw = gemini.chat_completion(
                system=_SYSTEM,
                user=_user_prompt(product),
                model=gemini.CHAT_MODEL,
                temperature=0.3,
                max_tokens=None,
                response_format_json=True,
            )
            if not raw:
                raise ValueError("empty response")
            p = gemini.extract_json(raw)
            if isinstance(p, dict):
                parsed = p
                break
            raise ValueError("not a dict")
        except Exception as exc:  # noqa: BLE001
            log.warning("f1_sections: attempt %d/2 failed (%s)",
                        attempt + 1, str(exc)[:160])

    if not isinstance(parsed, dict):
        return {}

    out: Dict[str, Any] = {
        k: _clean(parsed.get(k), k) for k in FIELDS if k != "capabilities"
    }
    caps = _clean_capabilities(parsed.get("capabilities"))
    if caps:
        out["capabilities"] = caps

    # Reject headings that would fit any company — the whole point is copy a
    # competitor could not reuse.
    h = (out.get("benefits_heading") or "").lower().strip()
    if h in _GENERIC_HEADINGS:
        log.info("f1_sections: generic heading %r rejected for %s", h, name)
        out["benefits_heading"] = ""
    # A blob with no usable heading is worse than none — the renderer's
    # neutral fallback reads better than half-empty product copy.
    if not out.get("benefits_heading") and not out.get("cta_heading"):
        log.info("f1_sections: generation produced nothing usable for %s", name)
        return {}
    # `attribution` is kept even when empty: "" records that the site WAS
    # analysed and shows no parent credit, which must beat the env default.
    # Dropping it let a global NEXUS_EMAIL_ATTRIBUTION leak the wrong company
    # into every product's masthead.
    kept = {k: v for k, v in out.items() if v}
    if "attribution" in out:
        kept["attribution"] = out["attribution"]
    return kept


def read_cached(db, product_id: Optional[int]) -> Dict[str, Any]:
    """Cached copy for a product, or {}."""
    if not product_id:
        return {}
    try:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT f1_sections FROM nexus_products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        ).mappings().first()
    except Exception:
        log.info("f1_sections: read failed (pre-migration?)", exc_info=True)
        return {}
    if not row:
        return {}
    raw = row.get("f1_sections")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in FIELDS:
            continue
        if not v and k != "attribution":
            continue
        out[k] = v if k == "capabilities" else str(v)
    return out


def ensure_f1_sections(
    db,
    product: Optional[Mapping[str, Any]],
    force: bool = False,
) -> Dict[str, Any]:
    """Idempotent: return cached copy, generating only when absent or forced.

    Never called from the send path — `_build_sender_ctx_for_product` reads the
    column directly, so a tick costs no Gemini call.
    """
    prod = product or {}
    pid = prod.get("id")
    if not pid:
        return {}

    if not force:
        cached = read_cached(db, pid)
        if cached:
            return cached

    # Ground the copy in the product's OWN website, not just the stored
    # columns — a heading written from `value_proposition` alone comes out as
    # a category label ("Core platform capabilities") rather than a selling
    # point. Cached by the scraper, so a recently-analysed product is free.
    prod = dict(prod)
    prod["_site_text"] = fetch_site_text(prod.get("source_url"), db=db)
    if not prod["_site_text"]:
        log.info("f1_sections: no site text for product %s — copy will be "
                 "weaker (stored fields only)", pid)

    sections = generate_f1_sections(prod)
    if not sections:
        return {}

    payload = dict(sections)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        from sqlalchemy import text
        db.execute(
            text("UPDATE nexus_products SET f1_sections = CAST(:v AS JSONB) "
                 "WHERE id = :id"),
            {"v": json.dumps(payload), "id": pid},
        )
        db.commit()
    except Exception:
        log.exception("f1_sections: write failed (non-fatal)")
        try:
            db.rollback()
        except Exception:
            pass
    return sections
