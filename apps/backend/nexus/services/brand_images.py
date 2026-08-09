"""Extract a product's logo + hero from its website for the f1 email.

Pipeline per slot:  fetch candidates -> validate -> normalize -> S3 -> cache.

Every constraint here is read off the live template
(`templates/email/outreach_f1.tpl.html`), not assumed:

    logo   44x44   fixed px, ratio 1:1     — sits on #f4ede6 / #241f1b (dark)
    hero   552x235 fluid,    ratio 2.35:1  — sits on #fbfaf8 / #1f1b18 (dark)

Two constraints drive most of this module:

  * The hero slot is 2.35:1, a cinematic letterbox. A standard og:image is
    1200x630 (1.91:1) — the WRONG shape. Candidates are centre-cropped, never
    squashed, and anything squarer than 1.4:1 is rejected because cropping it
    to 2.35:1 would cut ~40% of the height and decapitate the subject.

  * A transparent PNG logo with dark ink is INVISIBLE on the dark-mode
    masthead. It passes every other check — valid image, right shape, right
    size — and simply disappears. Detected and flattened onto a light plate.

Assets are always re-hosted in S3. Never reference an origin URL in an email:
Microlink CDN links and customer CDNs expire, block referer-less requests and
rate-limit, and a sent email is archived forever.

Contract: never raises. Returns {} / None when unsure.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("nexus.brand_images")

# --- slot specs, derived from the template ---------------------------------
SLOTS: Dict[str, Dict[str, Any]] = {
    "logo": {
        "target": (88, 88),          # @2x of 44x44
        "ratio": 1.0,
        "accept_ratio": (0.80, 1.25),
        "min_src": (64, 64),
        "max_bytes": 60 * 1024,
        "fmt": "PNG",                # alpha must survive
    },
    "hero": {
        "target": (1104, 470),       # @2x of 552x235
        "ratio": 552 / 235,          # 2.349
        "accept_ratio": (1.40, 3.20),
        "min_src": (600, 250),
        "max_bytes": 400 * 1024,
        "fmt": "JPEG",
    },
}

# Dark-mode masthead surface the logo must stay visible against.
_DARK_SURFACE = (0x24, 0x1F, 0x1B)
_LIGHT_PLATE = (0xFF, 0xFF, 0xFF)

_UA = "Mozilla/5.0 (compatible; PipelytBrandBot/1.0)"


def _host(url: str) -> str:
    try:
        h = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def _same_site(candidate: str, site: str) -> bool:
    """True when the candidate is served by the product's own domain, a
    subdomain of it, or a CDN whose host contains the registrable name.

    Guards the worst failure mode: shipping ANOTHER company's logo. A page
    routinely embeds partner logos, ad pixels and stock photos.
    """
    c, s = _host(candidate), _host(site)
    if not c or not s:
        return False
    if c == s or c.endswith("." + s) or s.endswith("." + c):
        return True
    root = s.split(".")[0]
    return len(root) >= 4 and root in c


def _fetch(url: str, timeout: float = 12.0) -> Optional[bytes]:
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA}, stream=True)
        if r.status_code != 200:
            return None
        ct = (r.headers.get("Content-Type") or "").lower()
        if ct and not ct.startswith("image/"):
            return None
        data = r.content
        return data if data and len(data) > 128 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_for_slot(raw: bytes, slot: str) -> Tuple[bool, str]:
    """(ok, reason). Rejection reasons are returned so they can be persisted —
    'why is there no logo for this customer?' must be answerable without
    re-running the pipeline."""
    spec = SLOTS.get(slot)
    if not spec:
        return False, f"unknown slot {slot}"
    if not raw:
        return False, "empty body"
    try:
        from PIL import Image
        Image.open(io.BytesIO(raw)).verify()          # structural check
        im = Image.open(io.BytesIO(raw))              # verify() exhausts it
        im.load()
    except Exception as e:
        return False, f"undecodable ({e.__class__.__name__})"

    w, h = im.size
    if w < spec["min_src"][0] or h < spec["min_src"][1]:
        return False, f"too small {w}x{h} (min {spec['min_src'][0]}x{spec['min_src'][1]})"

    ratio = w / h if h else 0
    lo, hi = spec["accept_ratio"]
    if not (lo <= ratio <= hi):
        return False, f"ratio {ratio:.2f} outside {lo}-{hi}"

    # Near-uniform images — 1x1 tracking pixels, solid spacers, blank CDN
    # placeholders — are all valid image/* responses. Variance catches them.
    try:
        small = im.convert("RGB").resize((32, 32))
        px = list(small.getdata())
        mean = [sum(c[i] for c in px) / len(px) for i in range(3)]
        var = sum((c[i] - mean[i]) ** 2 for c in px for i in range(3)) / (len(px) * 3)
        if var < 12.0:
            return False, f"near-uniform (variance {var:.1f})"
    except Exception:
        pass

    return True, "ok"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _rel_luminance(rgb) -> float:
    def chan(v):
        c = v / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _needs_light_plate(im) -> bool:
    """True when a transparent logo's visible ink is too dark to read on the
    dark-mode masthead."""
    if im.mode not in ("RGBA", "LA", "P"):
        return False
    rgba = im.convert("RGBA")
    px = [p for p in rgba.getdata() if p[3] > 40]
    if not px:
        return False
    avg = tuple(sum(p[i] for p in px) / len(px) for i in range(3))
    ink = _rel_luminance(avg)
    dark = _rel_luminance(_DARK_SURFACE)
    contrast = (max(ink, dark) + 0.05) / (min(ink, dark) + 0.05)
    return contrast < 3.0


def normalize_for_slot(
    raw: bytes, slot: str, screenshot: bool = False,
) -> Optional[Tuple[bytes, str, int, int, bool]]:
    """Crop to the slot ratio, downscale to @2x, re-encode.

    `screenshot=True` routes through the content-aware crop — a page capture
    needs its nav removed and its densest band chosen, whereas a designed
    og:image is already composed and only wants a centre crop.

    Returns (bytes, content_type, w, h, plated) or None.
    """
    spec = SLOTS.get(slot)
    if not spec:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return None

    plated = False
    try:
        if slot == "logo":
            if _needs_light_plate(im):
                rgba = im.convert("RGBA")
                plate = Image.new("RGBA", rgba.size, _LIGHT_PLATE + (255,))
                plate.alpha_composite(rgba)
                im = plate
                plated = True
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")

        # Crop to the target ratio. Crop, never squash.
        tw, th = spec["target"]
        target_ratio = spec["ratio"]
        if screenshot and slot == "hero":
            im = smart_crop_hero(im, target_ratio)
        else:
            w, h = im.size
            cur = w / h if h else target_ratio
            if cur > target_ratio:                   # too wide -> trim sides
                new_w = int(round(h * target_ratio))
                left = (w - new_w) // 2
                im = im.crop((left, 0, left + new_w, h))
            elif cur < target_ratio:                 # too tall -> trim height
                new_h = int(round(w / target_ratio))
                # Bias upward for the hero: designed marketing images put the
                # subject in the top half, so a true centre crop tends to cut
                # the headline off.
                top = int((h - new_h) * (0.40 if slot == "hero" else 0.5))
                im = im.crop((0, top, w, top + new_h))

        im = im.resize((tw, th), Image.LANCZOS)

        out = io.BytesIO()
        if spec["fmt"] == "PNG":
            im.save(out, format="PNG", optimize=True)
            ct = "image/png"
        else:
            im.convert("RGB").save(out, format="JPEG", quality=82, optimize=True)
            ct = "image/jpeg"
        data = out.getvalue()
        if len(data) > spec["max_bytes"] and spec["fmt"] == "JPEG":
            out = io.BytesIO()
            im.convert("RGB").save(out, format="JPEG", quality=70, optimize=True)
            data = out.getvalue()
        return data, ct, tw, th, plated
    except Exception:
        log.exception("brand_images: normalize failed for slot=%s", slot)
        return None


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------
_ICON_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*(?:apple-touch-icon|icon)[^"\']*["\'][^>]*>', re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)["\']',
    re.I)


def _microlink(site_url: str) -> Dict[str, Any]:
    try:
        from services.business_dna_service import fetch_microlink
        return fetch_microlink(site_url) or {}
    except Exception:
        log.info("brand_images: microlink unavailable", exc_info=True)
        return {}


# Viewport used for the hero screenshot. Deliberately TALLER than the 2.35:1
# slot so the content-aware crop below has room to skip the site nav and pick
# the best band — a viewport already at slot ratio forces the nav into frame.
# deviceScaleFactor 2 gives a retina-grade source to downscale from.
_HERO_VIEWPORT = "viewport.width=1280&viewport.height=860&viewport.deviceScaleFactor=2"


def _microlink_hero_shot(site_url: str) -> str:
    """Screenshot URL captured at a crop-friendly viewport. Returns '' on
    failure so the caller falls back to the generic microlink screenshot."""
    try:
        import requests
        api = (f"https://api.microlink.io/?url={site_url}"
               f"&screenshot=true&{_HERO_VIEWPORT}")
        d = requests.get(api, timeout=45).json().get("data", {}) or {}
        s = d.get("screenshot")
        u = s.get("url") if isinstance(s, dict) else s
        return u if isinstance(u, str) else ""
    except Exception:
        log.info("brand_images: sized screenshot fetch failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Content-aware cropping
#
# A homepage screenshot squeezed into a 2.35:1 letterbox by geometry alone
# produces exactly the artefacts that make an email look cheap: the site's nav
# bar riding along the top, headlines sliced in half, CTAs cut off at the
# bottom, and slabs of empty margin. These helpers pick the crop by looking at
# where the content actually is.
# ---------------------------------------------------------------------------
def _row_energy(im) -> List[float]:
    """Per-row edge energy. Text and UI score high; blank rows score ~0."""
    from PIL import ImageFilter
    g = im.convert("L").filter(ImageFilter.FIND_EDGES)
    w, h = g.size
    px = g.load()
    step = max(1, w // 400)                      # subsample for speed
    cols = max(1, len(range(0, w, step)))
    return [sum(px[x, y] for x in range(0, w, step)) / cols for y in range(h)]


def _col_energy(im) -> List[float]:
    from PIL import ImageFilter
    g = im.convert("L").filter(ImageFilter.FIND_EDGES)
    w, h = g.size
    px = g.load()
    step = max(1, h // 400)
    rows = max(1, len(range(0, h, step)))
    return [sum(px[x, y] for y in range(0, h, step)) / rows for x in range(w)]


def _trim_uniform_border(im, thresh: float = 3.0):
    """Drop near-blank rows/columns at the edges — the 'excessive whitespace'
    that makes a captured image look accidental."""
    re_, ce_ = _row_energy(im), _col_energy(im)
    w, h = im.size
    top = 0
    while top < h - 10 and re_[top] < thresh:
        top += 1
    bot = h - 1
    while bot > top + 10 and re_[bot] < thresh:
        bot -= 1
    left = 0
    while left < w - 10 and ce_[left] < thresh:
        left += 1
    right = w - 1
    while right > left + 10 and ce_[right] < thresh:
        right -= 1
    return im.crop((left, top, right + 1, bot + 1))


def _detect_nav(im, max_frac: float = 0.22) -> int:
    """Y coordinate just past the site's nav bar.

    A nav is a band of content near the top followed by a quiet gap. Returns 0
    when no such pattern exists, so a page without a nav is left alone.
    """
    re_ = _row_energy(im)
    h = len(re_)
    limit = int(h * max_frac)
    if limit <= 5:
        return 0
    peak = max(re_[:limit])
    if peak < 6:
        return 0
    quiet = peak * 0.18
    seen_content = False
    for y in range(limit):
        if re_[y] > peak * 0.45:
            seen_content = True
        if seen_content and re_[y] < quiet:
            run = 0
            while y + run < limit and re_[y + run] < quiet:
                run += 1
            if run >= max(8, h // 90):        # a real gap, not a line break
                return y + run // 2
    return 0


def _best_window(im, target_ratio: float, skip_top: int = 0):
    """Slide a target-ratio window down the image; maximise interior content
    while penalising windows whose edges slice through dense rows (which is
    what cuts headlines and buttons in half)."""
    w, h = im.size
    win_h = int(round(w / target_ratio))
    if win_h >= h - skip_top:
        y = max(0, min(skip_top, max(0, h - win_h)))
        return (0, y, w, min(h, y + win_h))
    re_ = _row_energy(im)
    best_score, best_y = None, skip_top
    step = max(2, (h - skip_top - win_h) // 120)
    for y in range(skip_top, h - win_h + 1, step):
        interior = sum(re_[y:y + win_h]) / win_h
        edge = (re_[y] + re_[min(h - 1, y + win_h - 1)]) / 2.0
        score = interior - 1.4 * edge
        if best_score is None or score > best_score:
            best_score, best_y = score, y
    return (0, best_y, w, best_y + win_h)


def smart_crop_hero(im, target_ratio: float):
    """Trim blank margins, skip the nav, then pick the densest clean band."""
    im = _trim_uniform_border(im)
    skip = _detect_nav(im)
    return im.crop(_best_window(im, target_ratio, skip_top=skip))


def _deep(d: Any, *keys: str) -> str:
    """Microlink nests as {'logo': {'url': ...}} or returns a bare string."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
    if isinstance(cur, dict):
        cur = cur.get("url")
    return cur if isinstance(cur, str) else ""


def candidates_for(slot: str, site_url: str, ml: Dict[str, Any]) -> List[Tuple[str, str]]:
    """[(source_label, url)] in priority order."""
    out: List[Tuple[str, str]] = []
    if slot == "logo":
        for label, keys in (("microlink_logo", ("logo",)),
                            ("microlink_apple", ("apple_touch_icon",))):
            u = _deep(ml, *keys)
            if u:
                out.append((label, u))
        try:
            from services.business_dna_service import fetch_microlink  # noqa: F401
            # fetch_html_sync wraps asyncio.run, which raises inside a
            # running loop — route it through the same guard.
            from nexus.services.f1_sections import run_async_safely
            from nexus.services.playwright_scraper import fetch_html
            res = run_async_safely(lambda: fetch_html(site_url))
            html = getattr(res, "text", "") or ""
            for tag in _ICON_RE.findall(html)[:4]:
                m = _HREF_RE.search(tag)
                if m:
                    out.append(("page_icon", _abs(m.group(1), site_url)))
        except Exception:
            pass
    else:
        u = _deep(ml, "image")
        if u:
            out.append(("og_image", u))
        # A screenshot captured at a crop-friendly viewport beats the generic
        # one, which arrives at 1.6:1 and forces the nav into a 2.35:1 crop.
        sized = _microlink_hero_shot(site_url)
        if sized:
            out.append(("screenshot", sized))
        u = _deep(ml, "screenshot")
        if u and u != sized:
            out.append(("screenshot", u))
    # de-dup, preserve order
    seen, uniq = set(), []
    for label, u in out:
        if u and u not in seen:
            seen.add(u)
            uniq.append((label, u))
    return uniq


def _abs(href: str, base: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def extract_slot(
    site_url: str,
    slot: str,
    ml: Optional[Dict[str, Any]] = None,
    require_same_site: bool = True,
) -> Dict[str, Any]:
    """Run the ladder for one slot. Returns a result dict describing what
    happened — including WHY every candidate was rejected."""
    result: Dict[str, Any] = {
        "slot": slot, "ok": False, "bytes": None, "content_type": None,
        "width": None, "height": None, "source": None, "origin_url": None,
        "plated": False, "attempts": [],
    }
    if not site_url:
        result["error"] = "no site_url"
        return result

    ml = ml if ml is not None else _microlink(site_url)
    for label, url in candidates_for(slot, site_url, ml):
        att = {"source": label, "url": url}
        # Screenshots are served by Microlink, not the customer's domain.
        if require_same_site and label not in ("screenshot",) and not _same_site(url, site_url):
            att["reject"] = "different origin"
            result["attempts"].append(att)
            continue
        raw = _fetch(url)
        if not raw:
            att["reject"] = "fetch failed"
            result["attempts"].append(att)
            continue
        ok, why = validate_for_slot(raw, slot)
        if not ok:
            att["reject"] = why
            result["attempts"].append(att)
            continue
        norm = normalize_for_slot(
            raw, slot, screenshot=(label == "screenshot"),
        )
        if not norm:
            att["reject"] = "normalize failed"
            result["attempts"].append(att)
            continue
        data, ct, w, h, plated = norm
        att["accept"] = f"{w}x{h} {len(data)}B"
        result["attempts"].append(att)
        result.update({
            "ok": True, "bytes": data, "content_type": ct, "width": w,
            "height": h, "source": label, "origin_url": url, "plated": plated,
        })
        return result

    result["error"] = "no usable candidate"
    return result


def upload_asset(workspace_id: Any, product_id: Any, slot: str,
                 data: bytes, content_type: str) -> Tuple[str, str]:
    """Content-addressed S3 upload. Returns (s3_key, public_url).

    The hash means a re-extraction yielding identical bytes reuses the object
    and never invalidates a URL already sitting in a sent email.
    """
    from core.s3_utils import get_s3_client, get_s3_url, S3_BUCKET_NAME
    ext = "png" if content_type.endswith("png") else "jpg"
    sha8 = hashlib.sha256(data).hexdigest()[:8]
    key = f"nexus/brand/{workspace_id or 0}/{product_id or 0}/{slot}_{sha8}.{ext}"
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise RuntimeError("S3 not configured")
    s3.upload_fileobj(
        io.BytesIO(data), S3_BUCKET_NAME, key,
        ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000"},
    )
    return key, get_s3_url(key)
