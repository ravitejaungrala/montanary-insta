"""Business DNA → category classifier.

Categorizes a user's business_dna JSON into ONE of ten canonical buckets
(or 'personal' when no DNA / 'custom:<slug>' when it fits none).

The category drives the Auto Style pipeline: it maps to a style_group,
and the pipeline then GPT-picks the best style from that group.

Categorization runs ONCE at DNA save time (PUT /profile) — the result is
persisted inside business_dna as `_category` and `_category_is_custom`,
so generation time reads the cached value with zero extra latency.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger("pipelyt.business_categorizer")


# ═══════════════════════════════════════════════════════════════════════
# CANONICAL CATEGORIES
# ═══════════════════════════════════════════════════════════════════════
# Order preserved so the frontend dropdown can iterate this list directly.
# Add new canonical categories here; anything else GPT invents gets
# prefixed `custom:` in `_category` and flagged via `_category_is_custom`.

BUSINESS_CATEGORIES: list[dict] = [
    {"key": "software_product",   "label": "Software Product / SaaS",
     "hint": "Google, Microsoft, Notion, Figma, mobile apps, developer tools"},
    {"key": "software_service",   "label": "Software Service / IT Consulting",
     "hint": "PwC, Infosys, Accenture, custom software development, IT consulting"},
    {"key": "physical_product",   "label": "Physical Product / Retail",
     "hint": "Jewelry, watches, cars, electronics, fashion, cosmetics, packaged goods"},
    {"key": "human_services",     "label": "Human Services / Local Trade",
     "hint": "Plumber, electrician, cleaning, salon, gym, personal trainer, handyman"},
    {"key": "travel_immigration", "label": "Travel & Immigration",
     "hint": "Travel agency, visa consulting, tourism, immigration services"},
    {"key": "religious",          "label": "Religious / Spiritual",
     "hint": "Temple, church, mosque, ashram, yoga studio, meditation center"},
    {"key": "food_beverage",      "label": "Food & Beverage",
     "hint": "Restaurant, cafe, bakery, packaged food brand, catering, food delivery"},
    {"key": "healthcare",         "label": "Healthcare / Medical",
     "hint": "Clinic, hospital, dental, mental wellness, physiotherapy, telemedicine"},
    {"key": "education",          "label": "Education / EdTech",
     "hint": "School, coaching institute, online course platform, tutoring, university"},
    {"key": "finance",            "label": "Finance / Fintech",
     "hint": "Bank, investment advisor, insurance, crypto exchange, lending, accounting"},
    {"key": "personal",           "label": "Personal (no business)",
     "hint": "Individual creator, personal brand, no formal business"},
]

CANONICAL_KEYS: set[str] = {c["key"] for c in BUSINESS_CATEGORIES}


# ═══════════════════════════════════════════════════════════════════════
# CATEGORIZER
# ═══════════════════════════════════════════════════════════════════════

_CATEGORIZER_MODEL = os.environ.get("CATEGORIZER_MODEL", "gpt-5-nano")


def _empty_dna(dna: Optional[dict]) -> bool:
    """True when there's no meaningful DNA data to categorize."""
    if not dna:
        return True
    # Ignore internal fields (leading underscore) and pure metadata when
    # deciding whether the DNA is empty.
    payload = {k: v for k, v in dna.items() if not k.startswith("_")}
    if not payload:
        return True
    # Any meaningful string content?
    for v in payload.values():
        if isinstance(v, str) and v.strip():
            return False
        if isinstance(v, (list, dict)) and v:
            return False
    return True


def _dna_summary(dna: dict) -> str:
    """Compact string summary of the DNA fields that matter for categorization."""
    keys_of_interest = [
        "company_name", "business_type", "industry", "category",
        "description", "value_proposition", "products", "services",
        "target_audience", "brand_voice",
    ]
    lines = []
    for k in keys_of_interest:
        v = dna.get(k)
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            v = json.dumps(v)[:400]
        v_str = str(v).strip()
        if v_str:
            lines.append(f"{k}: {v_str[:400]}")
    if not lines:
        # Fall back to any string fields we can find
        for k, v in dna.items():
            if k.startswith("_"):
                continue
            if isinstance(v, str) and v.strip():
                lines.append(f"{k}: {v.strip()[:400]}")
                if len(lines) >= 8:
                    break
    return "\n".join(lines)


def _slugify(s: str) -> str:
    """snake_case a free-form category name from GPT."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip().lower()).strip("_")
    return s[:40] or "other"


def categorize_business_dna(dna: Optional[dict]) -> Tuple[str, bool]:
    """Returns (category_key, is_custom).

    - No DNA / empty DNA        → ('personal', False)
    - DNA fits one of 10 canon  → (canonical_key, False)
    - DNA fits none             → ('custom:<slug>', True), where <slug> is
                                  a snake_case name GPT proposes.
    - GPT unavailable / errors  → ('personal', False) — safe fallback,
                                  drops user into `auto` style group.
    """
    if _empty_dna(dna):
        return ("personal", False)

    dna_text = _dna_summary(dna)
    if not dna_text:
        return ("personal", False)

    # Build the categories list for the prompt
    cat_lines = [f"  - {c['key']}: {c['label']} ({c['hint']})"
                 for c in BUSINESS_CATEGORIES]
    cat_block = "\n".join(cat_lines)

    system_prompt = (
        "You are a business classifier. Given a Business DNA summary, return "
        "a JSON object identifying which category the business fits. Reply "
        "with ONLY a JSON object, no prose.\n\n"
        "Available categories:\n"
        f"{cat_block}\n\n"
        "OUTPUT SCHEMA:\n"
        '  {"category": "<one of the keys above>", "is_custom": false}\n'
        "OR, if none fit:\n"
        '  {"category": "<snake_case_new_name>", "is_custom": true, '
        '"suggested_label": "<Human Readable Name>"}\n\n'
        "Rules:\n"
        "- Prefer a canonical category unless the business truly fits none.\n"
        "- 'personal' is only for individuals with no business — a solo "
        "consultant with a company_name is NOT personal.\n"
        "- Never invent a custom category that overlaps a canonical one."
    )
    user_prompt = f"BUSINESS DNA:\n{dna_text}\n\nReturn the JSON."

    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=_CATEGORIZER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        cat = str(parsed.get("category", "")).strip().lower()
        is_custom = bool(parsed.get("is_custom", False))

        if not cat:
            logger.warning(f"[categorizer] empty category from GPT — falling back to personal")
            return ("personal", False)

        if cat in CANONICAL_KEYS:
            return (cat, False)

        # GPT proposed something not in canon
        if is_custom:
            slug = _slugify(cat)
            logger.info(f"[categorizer] new custom category proposed: {slug} "
                        f"(suggested_label={parsed.get('suggested_label', '')!r})")
            return (f"custom:{slug}", True)

        # GPT returned a non-canonical key without marking custom — coerce
        logger.warning(f"[categorizer] non-canonical key {cat!r} without is_custom — coercing to custom:")
        return (f"custom:{_slugify(cat)}", True)

    except Exception as exc:
        logger.warning(f"[categorizer] failed ({type(exc).__name__}: {exc}) — defaulting to personal")
        return ("personal", False)


def category_label(category_key: str) -> str:
    """Human-readable label for a category key. `custom:foo` → 'Custom: Foo'."""
    if category_key.startswith("custom:"):
        slug = category_key.split(":", 1)[1]
        return "Custom: " + slug.replace("_", " ").title()
    for c in BUSINESS_CATEGORIES:
        if c["key"] == category_key:
            return c["label"]
    return category_key
