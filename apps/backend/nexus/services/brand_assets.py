"""Per-product brand assets for the outreach email.

Phase A implements COLOURS only. Image extraction (logo / hero) lands in
Phase C — see website_brand_extraction_plan.md.

Colour resolution, in priority order:

  1. `brand_dna.fetch_brand_colors()` — the owner's Business DNA, matched on
     the product's own domain. Unchanged, and keeps its exact-match-or-nothing
     rule: it refuses to borrow the parent account's brand because
     mis-branding is worse than a neutral email.
  2. `nexus_products.brand_colors` — colours previously extracted from this
     product's own website, cached so step 3 runs once.
  3. `business_dna_service.extract_business_dna()` — screenshot -> Gemini
     vision -> {primary, secondary, accent, background}, extracted live from
     the product's site. Persisted to step 2.
  4. `{}` — the caller falls back to `derive_palette(None)`, a neutral slate.

Step 3 closes the gap where a product whose owner never ran Pipelyt onboarding
had no colours at all.

Contract mirrors `brand_dna.py`: NEVER raises, returns `{}` when unsure.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger("nexus.brand_assets")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Vision models routinely return pure white or pure black as "primary" from a
# minimal or high-contrast site. `derive_palette` WILL clamp those into
# legibility, but the result is a grey email carrying no brand signal — worse
# than the honest neutral fallback, and indistinguishable from a bug. Reject
# anything whose relative luminance sits outside this band.
_LUM_MIN = 0.02
_LUM_MAX = 0.92

# Below this squared-RGB distance, primary and accent are effectively the same
# colour; passing both would make `c_counter` collide with `c_link`, flattening
# the two-accent design. Pass accent=None instead and let derive_palette
# synthesize a counter-accent.
_MIN_PAIR_DISTANCE_SQ = 1200


def _norm_hex(v: Any) -> str:
    """Normalise to '#rrggbb' lowercase, or '' when not a usable hex."""
    if not isinstance(v, str):
        return ""
    s = v.strip().lower()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4 and _HEX_RE.match("#" + "".join(c * 2 for c in s[1:])):
        s = "#" + "".join(c * 2 for c in s[1:])
    return s if _HEX_RE.match(s) else ""


def _rgb(hex_str: str) -> tuple:
    return (
        int(hex_str[1:3], 16),
        int(hex_str[3:5], 16),
        int(hex_str[5:7], 16),
    )


def _luminance(hex_str: str) -> float:
    """WCAG relative luminance."""
    def chan(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(v) for v in _rgb(hex_str))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _dist_sq(a: str, b: str) -> int:
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return (ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2


def is_usable_primary(hex_str: str) -> bool:
    """A primary must be a valid hex carrying actual brand signal."""
    h = _norm_hex(hex_str)
    if not h:
        return False
    return _LUM_MIN <= _luminance(h) <= _LUM_MAX


def map_extracted_colors(colors: Mapping[str, Any]) -> Dict[str, str]:
    """Collapse Business-DNA's 4 colours onto the 2 `derive_palette` takes.

    `extract_business_dna` prompts for:
        primary    — "used for buttons and links"   -> brand_hex
        accent     — "used for highlights"          -> accent_hex
        background — "used for background"          -> background_hex (hue only)
        secondary  — "used for text"                -> DISCARDED

    `secondary` is dropped deliberately — it is a TEXT colour, and the template
    owns its own type colours in both light and dark mode.

    Returns {} when `primary` is missing or unusable.
    """
    if not isinstance(colors, Mapping):
        return {}

    primary = _norm_hex(colors.get("primary"))
    if not is_usable_primary(primary):
        if primary:
            log.info("brand_assets: rejecting primary %s (luminance out of band)", primary)
        return {}

    out: Dict[str, str] = {"brand_color": primary}

    accent = _norm_hex(colors.get("accent")) or _norm_hex(colors.get("secondary"))
    if accent and is_usable_primary(accent) and _dist_sq(primary, accent) >= _MIN_PAIR_DISTANCE_SQ:
        out["accent_color"] = accent

    # Background: kept for its HUE only. derive_palette re-sets lightness and
    # saturation to paper values, so a dark or vivid site background tints the
    # email without ever becoming its background. Passed through raw; the
    # light-clamping lives in one place rather than being duplicated here.
    bg = _norm_hex(colors.get("background"))
    if bg:
        out["background_color"] = bg
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _read_cached(db, product_id: Optional[int]) -> Dict[str, str]:
    if not product_id:
        return {}
    try:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT brand_colors FROM nexus_products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        ).mappings().first()
    except Exception:
        # Column may not exist yet on an un-migrated DB — non-fatal.
        log.info("brand_assets: brand_colors read failed (pre-migration?)", exc_info=True)
        return {}
    if not row:
        return {}
    raw = row.get("brand_colors")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    return map_extracted_colors(raw)


def _write_cache(db, product_id: Optional[int], colors: Mapping[str, Any], source: str) -> None:
    if not product_id or not colors:
        return
    payload = {
        "primary": colors.get("primary"),
        "accent": colors.get("accent"),
        "background": colors.get("background"),
        "source": source,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from sqlalchemy import text
        db.execute(
            text("UPDATE nexus_products SET brand_colors = CAST(:v AS JSONB) WHERE id = :id"),
            {"v": json.dumps(payload), "id": product_id},
        )
        db.commit()
    except Exception:
        log.exception("brand_assets: brand_colors write failed (non-fatal)")
        try:
            db.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def resolve_product_assets(db, product: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """{'logo_url','hero_url'} from the cache. READ ONLY — never extracts,
    never hits the network. Safe to call from the send loop.

    Missing/rejected slots are simply absent from the dict; the template omits
    their blocks.
    """
    prod = product or {}
    pid = prod.get("id")
    if not pid:
        return {}
    try:
        from sqlalchemy import text
        rows = db.execute(
            text(
                "SELECT asset_kind, url FROM nexus_brand_assets "
                "WHERE product_id = :pid AND status = 'ready' AND url IS NOT NULL"
            ),
            {"pid": pid},
        ).mappings().all()
    except Exception:
        log.info("brand_assets: asset read failed (pre-migration?)", exc_info=True)
        return {}
    out: Dict[str, str] = {}
    for r in rows:
        kind = r.get("asset_kind")
        if kind in ("logo", "hero"):
            out[f"{kind}_url"] = r.get("url")
    return out


def _delete_superseded(s3, bucket: str, prefix: str, keep_key: str) -> int:
    """Remove older objects for the same product+slot so the bucket never
    accumulates versions. Only ever fires when the site's image genuinely
    changed (identical bytes hash to the same key, i.e. a no-op overwrite).

    Trade-off accepted: an email sent BEFORE a rebrand and opened after it
    will lose that image. Keeping every version instead would grow without
    bound, and a stale logo in an old mail is the lesser problem.
    """
    removed = 0
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for obj in resp.get("Contents", []) or []:
            k = obj.get("Key")
            if k and k != keep_key:
                s3.delete_object(Bucket=bucket, Key=k)
                removed += 1
    except Exception:
        log.info("brand_assets: superseded cleanup skipped", exc_info=True)
    return removed


def ensure_product_images(
    db,
    product: Optional[Mapping[str, Any]],
    force: bool = False,
    max_age_days: int = 90,
) -> Dict[str, Any]:
    """Idempotent extraction of a product's logo + hero.

    Skips any slot that is already `ready` and fresher than `max_age_days`, so
    calling this repeatedly costs one cheap DB read and nothing else. Only
    `force=True` re-extracts.

    NEVER touches a slot whose source is 'uploaded' — a manual upload always
    wins over a scrape.

    Returns a report: {'logo': {...}, 'hero': {...}, 'skipped': [...]}.
    Never raises.
    """
    from sqlalchemy import text

    prod = product or {}
    pid, wsid = prod.get("id"), prod.get("workspace_id")
    site = (prod.get("source_url") or "").strip()
    report: Dict[str, Any] = {"product_id": pid, "site": site, "skipped": []}
    if not pid or not site:
        report["error"] = "missing product id or source_url"
        return report

    # --- what already exists ------------------------------------------------
    existing: Dict[str, Mapping[str, Any]] = {}
    try:
        for r in db.execute(
            text(
                "SELECT asset_kind, status, source, s3_key, url, refreshed_at "
                "FROM nexus_brand_assets WHERE product_id = :pid"
            ),
            {"pid": pid},
        ).mappings().all():
            existing[r["asset_kind"]] = r
    except Exception:
        log.exception("brand_assets: asset lookup failed")
        report["error"] = "asset table unavailable (run migrations)"
        return report

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    todo = []
    for slot in ("logo", "hero"):
        cur = existing.get(slot)
        if cur and cur.get("source") == "uploaded":
            report["skipped"].append(f"{slot}: manual upload wins")
            continue
        if not force and cur and cur.get("status") == "ready" and cur.get("url"):
            ts = cur.get("refreshed_at")
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts > cutoff:
                    report["skipped"].append(f"{slot}: cached, fresh")
                    continue
        todo.append(slot)

    if not todo:
        return report

    # --- extract only what is actually needed -------------------------------
    try:
        from core.s3_utils import get_s3_client, S3_BUCKET_NAME
        from nexus.services.brand_images import (
            _microlink, extract_slot, upload_asset,
        )
    except Exception:
        log.exception("brand_assets: image deps unavailable")
        report["error"] = "image dependencies unavailable"
        return report

    ml = _microlink(site)   # one network fetch shared by both slots
    s3 = get_s3_client()

    for slot in todo:
        try:
            res = extract_slot(site, slot, ml=ml)
            if not res.get("ok"):
                db.execute(
                    text(
                        "INSERT INTO nexus_brand_assets "
                        "(workspace_id,product_id,asset_kind,status,last_error,refreshed_at) "
                        "VALUES (:ws,:pid,:k,'rejected',:err,now()) "
                        "ON CONFLICT (product_id,asset_kind) DO UPDATE SET "
                        "status='rejected', last_error=EXCLUDED.last_error, "
                        "refreshed_at=now()"
                    ),
                    {"ws": wsid, "pid": pid, "k": slot,
                     "err": (res.get("error") or "")[:500]},
                )
                db.commit()
                report[slot] = {"ok": False, "error": res.get("error"),
                                "attempts": res.get("attempts")}
                continue

            key, url = upload_asset(wsid, pid, slot, res["bytes"], res["content_type"])
            removed = _delete_superseded(
                s3, S3_BUCKET_NAME, f"nexus/brand/{wsid or 0}/{pid or 0}/{slot}_", key
            )
            db.execute(
                text(
                    "INSERT INTO nexus_brand_assets "
                    "(workspace_id,product_id,asset_kind,s3_key,url,width,height,"
                    " bytes,content_type,source,source_detail,status,refreshed_at) "
                    "VALUES (:ws,:pid,:k,:key,:url,:w,:h,:b,:ct,:src,"
                    " CAST(:sd AS JSONB),'ready',now()) "
                    "ON CONFLICT (product_id,asset_kind) DO UPDATE SET "
                    " s3_key=EXCLUDED.s3_key, url=EXCLUDED.url, width=EXCLUDED.width,"
                    " height=EXCLUDED.height, bytes=EXCLUDED.bytes,"
                    " content_type=EXCLUDED.content_type, source=EXCLUDED.source,"
                    " source_detail=EXCLUDED.source_detail, status='ready',"
                    " last_error=NULL, refreshed_at=now()"
                ),
                {"ws": wsid, "pid": pid, "k": slot, "key": key, "url": url,
                 "w": res["width"], "h": res["height"], "b": len(res["bytes"]),
                 "ct": res["content_type"], "src": res["source"],
                 "sd": json.dumps({"origin_url": res.get("origin_url"),
                                   "plated": res.get("plated"),
                                   "attempts": res.get("attempts")})},
            )
            db.commit()
            report[slot] = {"ok": True, "url": url, "key": key,
                            "dims": f"{res['width']}x{res['height']}",
                            "source": res["source"], "plated": res.get("plated"),
                            "superseded_removed": removed}
        except Exception as exc:
            log.exception("brand_assets: %s extraction failed", slot)
            try:
                db.rollback()
            except Exception:
                pass
            report[slot] = {"ok": False, "error": str(exc)[:200]}

    return report


def resolve_brand_colors(
    db,
    product: Optional[Mapping[str, Any]],
    allow_extraction: bool = True,
) -> Dict[str, str]:
    """Return {'brand_color', 'accent_color'} for a product, or {}.

    `allow_extraction=False` skips the live website call — use it on any path
    that must not make a network round-trip (e.g. inside a send loop).
    """
    prod = product or {}
    source_url = (prod.get("source_url") or "").strip()
    product_id = prod.get("id")

    # 1. Business DNA, exact domain match.
    try:
        from nexus.services.brand_dna import fetch_brand_colors
        dna = fetch_brand_colors(db, prod.get("user_id"), source_url) or {}
        if dna.get("brand_color"):
            return dna
    except Exception:
        log.exception("brand_assets: business DNA lookup failed (non-fatal)")

    # 2. Previously extracted, cached on the product.
    cached = _read_cached(db, product_id)
    if cached:
        return cached

    if not allow_extraction or not source_url:
        return {}

    # 3. Live extraction from the product's own website.
    try:
        from services.business_dna_service import extract_business_dna
        dna = extract_business_dna(source_url, is_product=True) or {}
        colors = dna.get("colors") if isinstance(dna, dict) else None
        mapped = map_extracted_colors(colors or {})
        if mapped:
            _write_cache(db, product_id, colors or {}, "website_extraction")
            log.info(
                "brand_assets: extracted colours for product=%s from %s -> %s",
                product_id, source_url, mapped,
            )
            return mapped
        log.info("brand_assets: extraction yielded no usable colours for %s", source_url)
    except Exception:
        log.exception("brand_assets: website colour extraction failed (non-fatal)")

    # 4. Nothing usable — caller falls back to the neutral palette.
    return {}
