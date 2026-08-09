"""7-day design-signature memory for the Image Director.

Prevents the designer_grade_post pipeline from producing look-alike posts
for the same Business DNA within a rolling 7-day window.

For each successful generation we record a compact "signature" —
    heading, cta_text, colour trio (bg/text/accent), aspect_ratio,
    supporting_visual, variant_type, timestamp
— onto `business_dna._image_director_history` (product-scoped when a
product_name is picked, else company-scoped).

At generation time the pipeline loads the last 7 days of signatures for
the same DNA and injects them into the Image Director's user prompt as
"AVOID THESE PAST DESIGNS" context, so the Director actively picks a
meaningfully different design.

Storage lives inside `users.business_dna` JSONB (never `ALTER TABLE users`
— see the migration convention). Writes are idempotent-ish: duplicate
timestamps get appended, and history is trimmed to the most recent 30
entries per entity to keep the blob small.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("pipelyt.image_director_history")

_MEMORY_WINDOW_DAYS = 7
_MEMORY_MAX_ENTRIES = 30
_HISTORY_KEY        = "_image_director_history"


# ══════════════════════════════════════════════════════════════════════
# READ — pull the last 7 days of signatures from the DNA
# ══════════════════════════════════════════════════════════════════════
def recent_signatures(
    business_dna: Optional[dict],
    product_name: Optional[str] = None,
    window_days: int = _MEMORY_WINDOW_DAYS,
) -> list[dict]:
    """Return the design signatures used within the past `window_days`.

    Product-scoped when `product_name` matches a nested product; else
    reads the top-level `_image_director_history` list. Returns most-
    recent first (so a small `[:N]` slice keeps the latest entries).
    Empty list on missing / malformed data — never raises.
    """
    if not isinstance(business_dna, dict):
        return []

    target = business_dna
    pname = (product_name or "").strip()
    if pname:
        products = business_dna.get("products")
        if isinstance(products, dict) and pname in products \
                and isinstance(products[pname], dict):
            target = products[pname]

    history = target.get(_HISTORY_KEY) or []
    if not isinstance(history, list):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    fresh: list[tuple[datetime, dict]] = []
    for e in history:
        if not isinstance(e, dict):
            continue
        ts = str(e.get("timestamp") or "").strip()
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if when < cutoff:
            continue
        fresh.append((when, e))

    fresh.sort(key=lambda p: p[0], reverse=True)
    return [e for _, e in fresh]


def build_avoid_block(recent: list[dict], max_show: int = 8) -> str:
    """Format the past signatures as a prompt block the Image Director
    can read directly. Empty string when nothing recent — the block is
    then simply omitted from the prompt."""
    if not recent:
        return ""
    lines = []
    for i, e in enumerate(recent[:max_show], start=1):
        parts = []
        # Composition FIRST — this is the primary anti-repeat signal.
        if e.get("composition_summary"):
            parts.append(f'composition="{str(e["composition_summary"])[:100]}"')
        if e.get("heading"):
            parts.append(f'heading="{str(e["heading"])[:80]}"')
        if e.get("cta_text"):
            parts.append(f'cta="{e["cta_text"]}"')
        cols = []
        for k in ("primary_bg", "primary_text", "accent"):
            v = e.get(k)
            if v:
                cols.append(str(v))
        if cols:
            parts.append(f'colours={cols}')
        if e.get("aspect_ratio"):
            parts.append(f'aspect={e["aspect_ratio"]}')
        if e.get("supporting_visual"):
            sv = str(e["supporting_visual"])[:60]
            parts.append(f'visual="{sv}"')
        when = e.get("timestamp", "")[:19]
        lines.append(f"  {i}. [{when}Z] {' · '.join(parts)}")
    return (
        "══════════════════════════════════════════════════════════════════\n"
        "AVOID THESE PAST DESIGNS (used for this brand in the past 7 days)\n"
        "══════════════════════════════════════════════════════════════════\n"
        "The following designs were already produced for this Business DNA\n"
        "in the last 7 days. Your job is to produce something VISIBLY\n"
        "DIFFERENT from every one of them. Change the COMPOSITION itself,\n"
        "not just the words:\n"
        "  • Pick a genuinely different composition (if past = split-with-\n"
        "    illustration, try a typographic hero, a stat card, a diagram,\n"
        "    a full-bleed photo, an announcement banner — anything unlike\n"
        "    what's shown below)\n"
        "  • Change the heading angle — a different question, promise,\n"
        "    provocation, or fact — not a rewording of the same idea\n"
        "  • Change the CTA (still from the locked list)\n"
        "  • Change the colour pairing you pull from the DNA palette\n"
        "  • Change the aspect ratio when the brief allows it\n"
        "  • Change whether you include a supporting visual at all\n\n"
        "PAST DESIGNS (do NOT clone any of these):\n"
        + "\n".join(lines) +
        "\n══════════════════════════════════════════════════════════════════\n\n"
    )


# ══════════════════════════════════════════════════════════════════════
# WRITE — persist one signature after a successful generation
# ══════════════════════════════════════════════════════════════════════
def build_signature_entry(
    *,
    director_brief: dict,
    variant_type: str,
) -> dict:
    """Build the compact signature dict written to _image_director_history."""
    return {
        "variant_type":         variant_type,
        "composition_summary":  (director_brief.get("composition_summary") or "")[:200],
        "heading":              (director_brief.get("heading") or "")[:200],
        "subheading":           (director_brief.get("subheading") or "")[:200],
        "cta_text":             director_brief.get("cta_text"),
        "primary_bg":           director_brief.get("primary_bg"),
        "primary_text":         director_brief.get("primary_text"),
        "accent":               director_brief.get("accent"),
        "aspect_ratio":         director_brief.get("aspect_ratio"),
        "supporting_visual":    (director_brief.get("supporting_visual") or "")[:200],
        "logo_placement":       director_brief.get("logo_placement"),
        "timestamp":            datetime.now(timezone.utc).isoformat(),
    }


def _trim(history: list) -> list:
    if not isinstance(history, list):
        return []
    def _ts(e):
        return str((e or {}).get("timestamp") or "")
    ordered = sorted([e for e in history if isinstance(e, dict)], key=_ts)
    if len(ordered) > _MEMORY_MAX_ENTRIES:
        ordered = ordered[-_MEMORY_MAX_ENTRIES:]
    return ordered


def record_signature(
    *,
    user_id: Optional[int],
    product_name: Optional[str],
    director_brief: dict,
    variant_type: str,
) -> bool:
    """Append the signature to the correct DNA's `_image_director_history`.

    Returns True on success, False on any error (never raises — the memory
    is a nice-to-have, never a blocker for generation).
    """
    if not user_id or not isinstance(director_brief, dict):
        return False

    try:
        from core.database import SessionLocal
        from models import User
        from sqlalchemy.orm.attributes import flag_modified

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                logger.warning(
                    f"[image-director-memory] user id={user_id} not found — skipping"
                )
                return False

            dna = user.business_dna or {}
            if not isinstance(dna, dict):
                logger.warning(
                    f"[image-director-memory] user id={user_id} has non-dict "
                    f"business_dna — skipping"
                )
                return False

            # Product-scoped or company-scoped target — mirrors reader.
            target: dict = dna
            target_desc = "company DNA"
            pname = (product_name or "").strip()
            if pname:
                products = dna.get("products")
                if isinstance(products, dict) and pname in products \
                        and isinstance(products[pname], dict):
                    target = products[pname]
                    target_desc = f"product DNA products[{pname!r}]"

            entry   = build_signature_entry(
                director_brief=director_brief,
                variant_type=variant_type,
            )
            history = list(target.get(_HISTORY_KEY) or [])
            history.append(entry)
            history = _trim(history)
            target[_HISTORY_KEY] = history

            # MutableDict tracks top-level key sets but not nested-list
            # appends — force-mark dirty so the UPDATE fires.
            user.business_dna = dna
            flag_modified(user, "business_dna")
            db.commit()

            logger.info(
                f"[image-director-memory] recorded variant={variant_type!r} "
                f"heading={entry['heading'][:40]!r} cta={entry['cta_text']!r} "
                f"for user id={user_id} on {target_desc} — history now "
                f"{len(history)} entries"
            )
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            f"[image-director-memory] record failed (non-fatal) for user "
            f"id={user_id}: {exc}"
        )
        return False
