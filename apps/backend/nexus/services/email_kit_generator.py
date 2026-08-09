"""Auto-generate per-ROLE outreach email "variants" from a product's grounded
3-section description.

A variant = the role-level pitch (subject, preheader, hero framing, ONE
centerpiece block, a few bullets, CTA label) that the rich template composes
with the per-lead opener + brand colors. Variants are PRODUCT-level: generated
ONCE per role, stored on `nexus_products.icp['email_variants']`, reused for
every lead of that role.

Topped-up on demand: when a lead's role isn't covered yet, we generate just
that role (and ensure a default "General" variant exists), append, persist, and
reuse thereafter. New roles cost one generation; everything else is reused.

Grounding + safety:
  * Every claim must trace to the 3-section description (no invented facts).
  * NO numbers/statistics — word-driven only (matches the locked decision).
  * Output is sanitized to the allowed block types; render_rich_email skips
    anything malformed and the sequencer falls back to the minimal renderer,
    so a bad generation can never break a send.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from nexus.services import gemini

log = logging.getLogger("nexus.email_kit_generator")

# Coarse persona buckets — keep the variant set small + reusable. Order =
# priority (most specific first). The keywords are ALSO stored on the variant
# as `role_match_keywords`, so the next lead with a matching title reuses it.
PERSONA_BUCKETS: List[Tuple[str, List[str]]] = [
    # Technical / engineering buyers (kept first — "vp of engineering" etc.
    # must NOT fall through to a marketing or founder bucket).
    ("Engineering Leadership", ["cto", "chief technology", "technology officer", "vp technology", "engineering", "devops", "platform", "architect", "infrastructure"]),
    ("Product", ["chief product", "cpo", "head of product", "vp product", "product manager", "product owner"]),
    ("Data / IT", ["cio", "chief information", "chief data", "chief digital", "information technology", "head of data", "data engineering", "analytics", "integration", "it director", "head of it"]),
    # Go-to-market / marketing buyers.
    ("CMO", ["cmo", "chief marketing", "chief growth"]),
    ("Marketing Leadership", ["vp marketing", "head of marketing", "marketing director", "director of marketing"]),
    ("Performance Marketing", ["performance", "paid", "ppc", "sem", "acquisition", "media buyer"]),
    ("Growth", ["growth"]),
    ("Demand Generation", ["demand", "demand gen", "lifecycle"]),
    ("Sales Leadership", ["cro", "chief revenue", "vp sales", "head of sales", "sales director"]),
    ("Marketing", ["marketing", "brand", "content", "seo"]),
    # Exec / functional. NOTE: do NOT use bare "president" — it's a substring
    # of "vice president" and would mis-bucket every VP as a founder.
    ("Founder / Owner", ["founder", "co-founder", "ceo", "owner"]),
    ("Operations", ["operations", "coo", "head of ops"]),
    ("Finance", ["cfo", "finance", "controller"]),
]

_CENTERPIECE_SHAPES = {
    "callout": ("title", "body"),
    "feature_list": ("title", "items"),
    "feature_cards": ("title", "cards"),
    "steps": ("title", "steps"),
}


def persona_for_title(title: Optional[str]) -> Tuple[str, List[str]]:
    """Map a lead's job title to a coarse persona (label, match-keywords).
    Falls back to ('General', [])."""
    t = (title or "").lower()
    if t:
        for label, kws in PERSONA_BUCKETS:
            for kw in kws:
                if kw in t:
                    return label, kws
    return "General", []


# ---------------------------------------------------------------------------
# Gemini generation
# ---------------------------------------------------------------------------
_SYSTEM = """ROLE
You are an elite B2B email copywriter specializing in personalized cold outreach
to senior decision-makers. You design ONE reusable outreach-email "role version"
for a specific buyer ROLE — the role-level pitch content that fills a few slots
in a fixed branded HTML template (the template owns the greeting, the per-lead
opener, the CTA button, and the signature).

You are given the PRODUCT/COMPANY in GROUNDED sections (what it is / what we do / features-services / who we serve). Every claim you make MUST trace to those sections. Do NOT invent features, customers, integrations, or facts.

GROUNDING — STRICT (this is the most important rule):
- Use ONLY capabilities that are explicitly stated in WHAT WE DO or FEATURES / SERVICES.
- You MAY phrase a real capability in plain buyer language, but its MEANING must match the input exactly. Do NOT rename, generalize, broaden, or "upgrade" a capability into something the product does not actually do.
  Example: if the product "optimizes spend" / "measures channel impact" / "forecasts outcomes", do NOT relabel that as "revenue forecasting" — that is a different, unstated claim.
- Every centerpiece capability row (and its sub-line) and every bullet MUST map to a specific item in WHAT WE DO or FEATURES / SERVICES.
- If a capability, outcome, feature, customer, integration, or fact is not clearly in the input, LEAVE IT OUT. A shorter, fully-accurate email beats a richer one with one invented claim.

HARD RULES:
- NO numbers, percentages, multipliers, money amounts, or statistics anywhere. Use WORDS only.
- No fluff words: leverage, synergy, world-class, best-in-class, game-changer, 10x, unlock, supercharge, revolutionary.
- Tight, human, specific — like a real founder/rep wrote it, not a brochure.
- You do NOT write the greeting, the per-lead opener, the signature, or the CTA button text body — the template adds those. You write the ROLE-LEVEL pitch.
- You may use {{first_name}} only in the subject if it reads naturally. Never invent a company name.
- business_type tells you framing: product = "our platform"; service = "our team"; gcc = "our GCC build & operate team".

Pick ONE centerpiece type that best fits this ROLE:
- callout       : one punchy framing box for the role's core problem/insight.
- feature_list  : 3-5 capability rows (label + one-line sub) most relevant to the role.
- feature_cards : 3-4 short benefit cards.
- steps         : a simple 3-step "how it works".

Return STRICTLY this JSON (no markdown):
{
  "subject": "<=70 chars; may include {{first_name}}",
  "preheader": "one-line inbox preview",
  "hero_headline": "short bold line, plain text",
  "hero_subhead": "one supporting sentence",
  "centerpiece_type": "callout | feature_list | feature_cards | steps",
  "centerpiece": { fields for the chosen type },
  "bullets": ["3-4 short outcome phrases, words only, no numbers"],
  "cta_label": "short button label, e.g. 'See how it works'"
}

centerpiece fields by type:
  callout:       {"title": "...", "body": "..."}
  feature_list:  {"title": "...", "items": [{"label": "...", "sub": "..."}]}
  feature_cards: {"title": "...", "cards": [{"title": "...", "body": "..."}]}
  steps:         {"title": "...", "steps": [{"title": "...", "text": "..."}]}

EXAMPLE — GOOD (FICTIONAL product "Northlight", an attribution platform that
flags wasted vs underfunded channels; buyer ROLE = Head of Data). Note every
capability row maps to a stated capability; do NOT reuse these specifics:
{
  "subject": "Channel attribution built for data teams",
  "preheader": "Connect your stack and see what each channel really drives",
  "hero_headline": "See which channels actually drive results",
  "hero_subhead": "Northlight unifies your channel data so attribution stops being guesswork.",
  "centerpiece_type": "feature_list",
  "centerpiece": {"title": "Built for data teams", "items": [
    {"label": "Unified channel data", "sub": "Bring fragmented sources together without manual prep."},
    {"label": "Contribution modelling", "sub": "Quantify how each channel drives results."},
    {"label": "Spend optimization", "sub": "Spot where to shift budget for more impact."}
  ]},
  "bullets": ["Centralize disparate channel data", "Measure true channel contribution", "Reallocate budget with confidence"],
  "cta_label": "See how it works"
}
WHY GOOD: every row maps to a stated capability; plain words, no numbers, no
buzzwords; nothing invented.

EXAMPLE — BAD (and WHY):
{
  "centerpiece": {"title": "Capabilities", "items": [
    {"label": "Revenue forecasting", "sub": "Predict next quarter's revenue with AI."},
    {"label": "10x faster reporting", "sub": "Best-in-class, end-to-end analytics."}
  ]}
}
WHY BAD: "Revenue forecasting" is not a stated capability (invented / renamed
from "spend optimization"); "10x" is a number; "best-in-class" and "end-to-end"
are buzzwords.
"""


def _user_prompt(pd: Mapping[str, Any], entity_type: str, role_label: str) -> str:
    caps = pd.get("key_capabilities") or []
    inds = pd.get("target_industries") or []
    lines = [
        f"TARGET ROLE: {role_label}",
        f"business_type: {entity_type or 'product'}",
        "",
        "PRODUCT / COMPANY (grounded — use only this):",
        f"- WHAT IT IS: {pd.get('what_the_company_is') or '(n/a)'}",
        f"- WHAT WE DO: {pd.get('what_they_do') or '(n/a)'}",
    ]
    if caps:
        lines.append("- FEATURES / SERVICES:")
        for c in caps[:12]:
            lines.append(f"    * {c}")
    lines.append(f"- WHO WE SERVE: {pd.get('who_they_serve') or '(n/a)'}")
    if inds:
        lines.append(f"- FOCUS INDUSTRIES: {', '.join(str(i) for i in inds[:12])}")
    if pd.get("customer_profile"):
        lines.append(f"- TYPICAL BUYER: {pd['customer_profile']}")
    lines.append("")
    lines.append(f"Write the JSON 'role version' tuned for a {role_label}.")
    return "\n".join(lines)


def _coerce_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _sanitize_variant(
    raw: Any, role_label: str, keywords: List[str], *, is_default: bool
) -> Optional[Dict[str, Any]]:
    """Validate Gemini output into a render-safe variant dict, or None."""
    if not isinstance(raw, dict):
        return None
    subject = str(raw.get("subject") or "").strip()[:80]
    if not subject:
        return None

    blocks: List[Dict[str, Any]] = []
    ctype = str(raw.get("centerpiece_type") or "").strip()
    cp = raw.get("centerpiece")
    if ctype in _CENTERPIECE_SHAPES and isinstance(cp, dict):
        block: Dict[str, Any] = {"type": ctype}
        if ctype == "callout":
            block["title"] = str(cp.get("title") or "")
            block["body"] = str(cp.get("body") or "")
            if block["body"]:
                blocks.append(block)
        elif ctype == "feature_list":
            items = [
                {"label": str(it.get("label") or ""), "sub": str(it.get("sub") or "")}
                for it in _coerce_list(cp.get("items"))
                if isinstance(it, dict) and it.get("label")
            ][:6]
            if items:
                block["title"] = str(cp.get("title") or "")
                block["items"] = items
                blocks.append(block)
        elif ctype == "feature_cards":
            cards = [
                {"title": str(c.get("title") or ""), "body": str(c.get("body") or "")}
                for c in _coerce_list(cp.get("cards"))
                if isinstance(c, dict) and c.get("title")
            ][:4]
            if cards:
                block["title"] = str(cp.get("title") or "")
                block["cards"] = cards
                blocks.append(block)
        elif ctype == "steps":
            steps = [
                {"title": str(s.get("title") or ""), "text": str(s.get("text") or "")}
                for s in _coerce_list(cp.get("steps"))
                if isinstance(s, dict) and s.get("title")
            ][:3]
            if steps:
                block["title"] = str(cp.get("title") or "")
                block["steps"] = steps
                blocks.append(block)

    bullets = [str(b).strip() for b in _coerce_list(raw.get("bullets")) if str(b).strip()][:5]
    if bullets:
        blocks.append({"type": "bullet_list", "title": "", "items": bullets})

    if not blocks:
        return None  # nothing renderable — skip; caller falls back

    return {
        "role_label": role_label,
        "role_match_keywords": keywords,
        "default": bool(is_default),
        "subject": subject,
        "preheader": str(raw.get("preheader") or "").strip()[:160],
        "hero": {
            "headline_html": str(raw.get("hero_headline") or "").strip(),
            "subhead": str(raw.get("hero_subhead") or "").strip(),
        },
        "blocks": blocks,
        "cta_label": str(raw.get("cta_label") or "").strip()[:40] or "See how it works",
    }


def generate_variant(
    product_description: Mapping[str, Any],
    entity_type: str,
    role_label: str,
    keywords: List[str],
    *,
    is_default: bool = False,
) -> Optional[Dict[str, Any]]:
    """Generate + sanitize ONE role variant. Returns None on any failure."""
    try:
        raw = gemini.chat_completion(
            system=_SYSTEM,
            user=_user_prompt(product_description, entity_type, role_label),
            temperature=0.5,
            max_tokens=1800,
            response_format_json=True,
        )
        parsed = gemini.extract_json(raw) if raw else None
    except Exception:
        log.exception("email_kit_generator: Gemini call failed for role=%s", role_label)
        return None
    return _sanitize_variant(parsed, role_label, keywords, is_default=is_default)


# ---------------------------------------------------------------------------
# Persistence + on-demand top-up
# ---------------------------------------------------------------------------
def _match_existing(variants: List[Mapping[str, Any]], title: Optional[str]) -> Optional[Dict[str, Any]]:
    t = (title or "").lower()
    if not t:
        return None
    for v in variants:
        for kw in (v.get("role_match_keywords") or []):
            if kw and str(kw).lower() in t:
                return dict(v)
    return None


def ensure_variant_for_lead(
    db,
    product_id: Optional[int],
    lead_title: Optional[str],
    *,
    product_description: Optional[Mapping[str, Any]],
    entity_type: str = "product",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (variant, variant_key) for this lead's role, generating + storing
    a new role version when one isn't covered yet. Returns (None, None) when no
    variant can be produced (caller then uses the minimal renderer).

    Order: reuse keyword-matched variant → generate this role → default
    ('General', generated if missing)."""
    if not product_id:
        return None, None
    from nexus.models_phase2 import NexusProduct

    prod = db.query(NexusProduct).filter(NexusProduct.id == product_id).first()
    if prod is None:
        return None, None
    icp = dict(prod.icp or {})
    variants: List[Dict[str, Any]] = list(icp.get("email_variants") or [])

    # 1. reuse an existing role-matched variant
    picked = _match_existing(variants, lead_title)
    if picked:
        return picked, picked.get("role_label")

    pd = product_description if isinstance(product_description, dict) and any(product_description.values()) else None

    def _persist() -> None:
        icp["email_variants"] = variants
        prod.icp = icp
        try:
            db.commit()
        except Exception:
            db.rollback()
            log.exception("email_kit_generator: persist variants failed")

    # 2. generate a variant for this lead's role (if we have a description)
    if pd:
        role_label, keywords = persona_for_title(lead_title)
        if role_label != "General":
            gen = generate_variant(pd, entity_type, role_label, keywords)
            if gen:
                variants.append(gen)
                _persist()
                return gen, gen.get("role_label")

    # 3. fall back to a default "General" variant (generate once if missing)
    default = next((v for v in variants if v.get("default")), None)
    if default:
        return dict(default), default.get("role_label")
    if pd:
        gen = generate_variant(pd, entity_type, "General", [], is_default=True)
        if gen:
            variants.append(gen)
            _persist()
            return gen, gen.get("role_label")

    return None, None
