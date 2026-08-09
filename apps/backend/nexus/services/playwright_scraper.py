"""
Jina-Reader product page scraper.

Scraping goes through Jina Reader (https://r.jina.ai/<url>), which renders
the page (including JS/SPAs via its browser engine) and returns clean
markdown — no local browser needed. The homepage uses Jina's browser
engine; subpages use fast mode with a browser fallback when they come back
empty. (The previous Playwright and raw-httpx-static fallbacks were removed:
Playwright isn't shipped in the Lambda image, and static httpx returns an
empty React shell for the SPA sites this targets — Jina's browser engine
covers both cases.)

The public surface is just `fetch_html(url) -> ScrapeResult`. Callers can
pass the `.text` field straight into `gemini.analyze_product`.

Retry policy: 3 attempts with exponential backoff.
60-second wall timeout total (enforced by the route).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("pipelyt.nexus.scraper")

JINA_BASE = "https://r.jina.ai/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ScrapeResult:
    url: str
    text: str = ""
    title: str = ""
    meta_description: str = ""
    h1s: List[str] = field(default_factory=list)
    h2s: List[str] = field(default_factory=list)
    char_count: int = 0
    source: str = ""  # 'jina' | 'empty'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _extract_headers(markdown: str) -> tuple[list[str], list[str]]:
    h1s: list[str] = []
    h2s: list[str] = []
    for level, text in _HEADER_RE.findall(markdown or ""):
        clean = text.strip()
        if len(level) == 1 and len(h1s) < 5:
            h1s.append(clean)
        elif len(level) == 2 and len(h2s) < 10:
            h2s.append(clean)
    return h1s, h2s


# ---------------------------------------------------------------------------
# Jina Reader
# ---------------------------------------------------------------------------


def _jina_content_len(body: str) -> int:
    """Length of Jina's ACTUAL rendered content, ignoring its preamble.

    Jina emits a fixed header — "Title:/URL Source:/Published Time:/
    Markdown Content:\n<body>". A JS/SPA page that didn't render returns
    ONLY that preamble (~100 chars) with an empty body. Gating on raw
    len() lets that title-only shell pass; gating on the text AFTER the
    "Markdown Content:" marker correctly flags it as empty so the caller
    can escalate to the next strategy."""
    if not body:
        return 0
    marker = "Markdown Content:"
    idx = body.find(marker)
    content = body[idx + len(marker):] if idx != -1 else body
    return len(content.strip())


async def _fetch_via_jina(
    url: str, timeout: float = 25.0, *, use_browser: bool = False,
) -> Optional[ScrapeResult]:
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not available — cannot reach Jina Reader")
        return None

    # Mirror the PROVEN keyless config in services/business_dna_service.py
    # (fetch_jina_base / fetch_jina_fast), which works on Jina's anonymous
    # tier where this path was getting 403'd. Two headers this used to add
    # were the cause: "X-No-Cache: true" forced a fresh live render on every
    # call (the keyless tier rate-limits that hard → 403), and a spoofed
    # Chrome User-Agent tripped Jina's keyless anti-abuse. Dropping both lets
    # Jina serve its cached copy exactly like the main app's scraper does.
    headers: dict = {}
    if use_browser:
        # Mirror business-DNA's base fetch (fetch_jina_base): a real headless
        # Chromium renders JS/SPA pages before extraction. Without this,
        # client-rendered sites (Next.js/React SPAs) hand back a title-only
        # shell (~100 chars) and ICP extraction starves.
        headers["X-Engine"] = "browser"
        headers["X-Timeout"] = "25"
        headers["X-With-Links-Summary"] = "true"
    else:
        # Mirror business-DNA's fast subpage fetch (fetch_jina_fast): no
        # browser engine for speed, and drop image markdown so subpage text
        # isn't bloated with `![Image](…)` noise that wastes tokens and
        # dilutes ICP grounding.
        headers["X-Retain-Images"] = "none"
    target = f"{JINA_BASE}{url}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(target, headers=headers)
            resp.raise_for_status()
            body = resp.text or ""
    except Exception as exc:
        logger.info("Jina fetch failed for %s: %s", url, exc)
        return None

    # Gate on RENDERED content, not Jina's boilerplate preamble, so an
    # empty SPA render is treated as a miss (caller escalates).
    if _jina_content_len(body) < 80:
        return None

    h1s, h2s = _extract_headers(body)
    title = h1s[0] if h1s else ""
    return ScrapeResult(
        url=url,
        text=body,
        title=title,
        h1s=h1s,
        h2s=h2s,
        char_count=len(body),
        source="jina",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_html(
    url: str,
    *,
    retries: int = 3,
    total_timeout: float = 60.0,
) -> ScrapeResult:
    """Scrape `url` via Jina Reader (browser engine). Retried up to
    `retries` times with exponential backoff. Total budget capped at
    `total_timeout` seconds. Returns an empty ScrapeResult if Jina yields
    nothing — the caller decides what to do."""

    deadline = asyncio.get_event_loop().time() + total_timeout
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 1.0:
            break
        try:
            # Browser engine renders JS/SPAs — same config as the
            # business-DNA path. Subpages use fast mode via _fetch_subpage.
            result = await _fetch_via_jina(
                url, timeout=min(remaining, 30.0), use_browser=True,
            )
        except Exception as exc:
            last_err = exc
            logger.info(
                "scrape attempt=%d errored for %s: %s", attempt, url, exc,
            )
            result = None

        if result and result.text:
            logger.info(
                "scrape ok url=%s source=%s chars=%d attempt=%d",
                url, result.source, result.char_count, attempt,
            )
            return result

        # Back off before retrying
        await asyncio.sleep(min(1.5 * attempt, 4.0))

    logger.warning("scrape failed for %s last_err=%s", url, last_err)
    return ScrapeResult(url=url, text="", source="empty")


def fetch_html_sync(url: str, **kwargs) -> ScrapeResult:
    """Blocking helper for callers that aren't already in an async
    context (e.g. background scripts, lambda_pinger)."""
    return asyncio.run(fetch_html(url, **kwargs))


# ---------------------------------------------------------------------------
# Multi-page scrape — homepage + signal-rich subpages
# ---------------------------------------------------------------------------
#
# Most marketing homepages are dense on positioning but thin on specifics.
# The signals that drive ICP filtering (services offered, industries
# served, integrations, customers) usually live on dedicated subpages.
# `fetch_bundle()` discovers + fetches those subpages in parallel so the
# Gemini prompt sees a structured view of the whole site instead of just
# the homepage.
#
# Subpage filtering: NO allow-list and NO deny-list — every internal,
# non-asset subpage discovered on the homepage is fetched. The link parser
# matches an exact path segment (e.g. /services) or a trailing-slash slug.
# ---------------------------------------------------------------------------

# 2026-06-18 — Subpage deny-list REMOVED per user request. Every internal,
# non-asset subpage discovered on the homepage is now fetched (no slug is
# excluded). Coverage is still bounded by the asset filter below, the
# yield-stop policy, and the _MAX_SUBPAGES_SAFETY cap.

# Asset / media file extensions — Jina happily fetches these and returns
# image alt-text or 422 errors. Either way they're useless for ICP
# extraction. Block at the link-discovery stage so we never waste a
# parallel slot on them.
_ASSET_EXTENSIONS: tuple[str, ...] = (
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff", ".avif",
    # video
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".flv",
    # audio
    ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac",
    # documents (skip — we want the HTML version, not the binary)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".tar", ".gz",
    # web assets
    ".css", ".js", ".json", ".xml", ".rss",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


def _is_asset_url(path_or_url: str) -> bool:
    """True if the URL's path ends with an image/video/document asset extension."""
    p = (path_or_url or "").lower().split("?", 1)[0].split("#", 1)[0]
    return p.endswith(_ASSET_EXTENSIONS)


# ---------------------------------------------------------------------------
# Scrape limits — no env vars, no fixed ceilings. Adaptive via:
#   1. DEADLINE PROPAGATION: callers pass a monotonic `deadline_ts` (absolute
#      time when they need control back). We derive per-subpage timeouts
#      from the remaining budget instead of a fixed number.
#   2. SELF-TUNING TIMEOUTS: every successful Jina fetch updates a rolling
#      P95 latency estimate. Next subpage timeout = max(P95 × 1.5, floor).
#      Bootstrap value is a sensible 6s until we have 3+ samples.
#   3. YIELD-BASED STOP: after the first batch of subpages, if a freshly
#      fetched page adds <10% new chars vs the running average, we stop
#      pulling more (diminishing returns).
#
# The ONLY remaining numbers are POLICY values, not arbitrary ceilings:
#   - `_TIMEOUT_FLOOR_SEC` = 2.0  → don't give Jina less than 2s to respond
#   - `_TIMEOUT_BOOTSTRAP_SEC` = 6.0 → before we have rolling stats
#   - `_YIELD_THRESHOLD` = 0.10 → match Apollo's adaptive-stop policy
#   - `_YIELD_MIN_PAGES` = 3 → need a baseline before yield-stop activates
#   - `_MAX_SUBPAGES_SAFETY` = 100 → only trips on pathological link-spam
# ---------------------------------------------------------------------------
import time as _time
from collections import deque as _deque

_TIMEOUT_FLOOR_SEC = 2.0
_TIMEOUT_BOOTSTRAP_SEC = 6.0
_YIELD_THRESHOLD = 0.10
_YIELD_MIN_PAGES = 3
_MAX_SUBPAGES_SAFETY = 100

# Rolling fetch latencies for the in-process P95 estimator. ~50 samples
# is enough to be stable without skewing on a single outlier; resets
# implicitly across Lambda cold-starts (re-bootstraps cleanly).
_FETCH_LATENCIES: _deque = _deque(maxlen=50)


# ---------------------------------------------------------------------------
# In-memory scrape cache — 2026-05-29
# ---------------------------------------------------------------------------
# Why: when the user clicks "Regenerate" on the wizard summary screen,
# we don't want to hit Jina again — the page hasn't changed. We cache
# the ScrapeBundle in a module-level dict keyed by hash(normalised URL)
# with a 15-min TTL. Cache is in-process only (no DB, no Redis) — gets
# wiped on restart, which is fine because Regenerate is a same-session
# action that happens within seconds of the original scrape.
#
# `_url_hash` lower-cases + strips trailing slash so "ZNINTH.com/" and
# "https://zninth.com" hash to the same key.
# ---------------------------------------------------------------------------
import hashlib as _hashlib

_SCRAPE_CACHE: dict = {}   # {url_hash: (ScrapeBundle, expires_at_monotonic)}
_SCRAPE_CACHE_TTL_SEC = 15 * 60   # 15 minutes


def _url_hash(url: str) -> str:
    norm = (url or "").strip().lower().rstrip("/")
    return _hashlib.sha256(norm.encode()).hexdigest()


def _bundle_to_jsonable(bundle) -> Optional[dict]:
    """ScrapeBundle -> plain JSON dict for the DB cache. None on any
    surprise — the DB layer is strictly best-effort."""
    try:
        import dataclasses
        return dataclasses.asdict(bundle)
    except Exception:  # noqa: BLE001
        return None


def _bundle_from_jsonable(d) -> Optional["ScrapeBundle"]:
    """Plain JSON dict -> ScrapeBundle. None on any surprise (e.g. a row
    written by a future/older shape) — caller treats it as a cache miss."""
    try:
        if not isinstance(d, dict) or not isinstance(d.get("homepage"), dict):
            return None
        return ScrapeBundle(
            homepage=ScrapeResult(**d["homepage"]),
            subpages=[ScrapeResult(**sp) for sp in d.get("subpages") or []
                      if isinstance(sp, dict)],
            sources=d.get("sources") or {},
        )
    except Exception:  # noqa: BLE001
        return None


def get_cached_bundle(url: str, db=None):
    """Return cached ScrapeBundle if it exists AND hasn't expired.
    Returns None on miss / expiry. Auto-prunes expired entries.

    2026-06-11: optional `db` session adds a CROSS-PROCESS layer backed by
    nexus_scrape_cache — on Lambda, /scrape-preview and /analyze can land
    on different containers, so the memory layer alone misses. Memory is
    checked first (free); the DB read is best-effort and NEVER raises —
    any failure (table missing, bad row, connection blip) is a miss.
    Callers that don't pass `db` get the exact pre-existing behaviour."""
    h = _url_hash(url)
    entry = _SCRAPE_CACHE.get(h)
    if entry:
        bundle, expires_at = entry
        if _time.monotonic() <= expires_at:
            return bundle
        _SCRAPE_CACHE.pop(h, None)
    if db is None:
        return None
    try:
        from datetime import datetime, timedelta
        from nexus.models_phase3 import NexusScrapeCache
        row = (
            db.query(NexusScrapeCache)
            .filter(NexusScrapeCache.url_hash == h)
            .first()
        )
        if row is None or row.fetched_at is None:
            return None
        if datetime.utcnow() - row.fetched_at > timedelta(seconds=_SCRAPE_CACHE_TTL_SEC):
            return None
        bundle = _bundle_from_jsonable(row.bundle)
        if bundle is not None:
            # Promote to the (faster) memory layer for this container,
            # keeping the REMAINING ttl so both layers expire together.
            remaining = _SCRAPE_CACHE_TTL_SEC - (
                datetime.utcnow() - row.fetched_at
            ).total_seconds()
            _SCRAPE_CACHE[h] = (bundle, _time.monotonic() + max(1.0, remaining))
            logger.info("scrape cache: DB-layer HIT for %s (cross-process)", url)
        return bundle
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        return None


def cache_bundle(url: str, bundle, db=None) -> None:
    """Insert/replace the cache entry for `url` with a fresh 15-min TTL.

    With `db`, also upserts into nexus_scrape_cache so OTHER processes /
    Lambda containers can reuse the scrape, and opportunistically prunes
    expired rows. Strictly best-effort: any DB failure is swallowed (the
    memory layer above already succeeded, scraping is never affected)."""
    if bundle is None:
        return
    h = _url_hash(url)
    _SCRAPE_CACHE[h] = (bundle, _time.monotonic() + _SCRAPE_CACHE_TTL_SEC)
    if db is None:
        return
    payload = _bundle_to_jsonable(bundle)
    if payload is None:
        return
    try:
        from sqlalchemy import text as _sql_text
        db.execute(
            _sql_text(
                """INSERT INTO nexus_scrape_cache (url_hash, url, bundle, fetched_at)
                   VALUES (:h, :u, CAST(:b AS JSONB), NOW())
                   ON CONFLICT (url_hash) DO UPDATE
                   SET url = EXCLUDED.url, bundle = EXCLUDED.bundle,
                       fetched_at = EXCLUDED.fetched_at"""
            ),
            {"h": h, "u": (url or "")[:2048], "b": json.dumps(payload, default=str)},
        )
        # Opportunistic prune — keeps the table at "currently previewed
        # URLs" size. Same statement-level best-effort as the upsert.
        db.execute(
            _sql_text(
                "DELETE FROM nexus_scrape_cache "
                "WHERE fetched_at < NOW() - make_interval(secs => :ttl)"
            ),
            {"ttl": float(_SCRAPE_CACHE_TTL_SEC)},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass


def _adaptive_subpage_timeout(deadline_ts: Optional[float]) -> float:
    """Return the timeout to use for the next Jina subpage fetch.

    Reasoning:
      - If we have a deadline, never exceed `(deadline - now) / 2` so a
        single hanging subpage can't burn the caller's whole remaining
        budget.
      - Otherwise pick `max(P95 × 1.5, floor)` where P95 is the rolling
        observed latency. First few calls use the bootstrap.
      - Always at least `_TIMEOUT_FLOOR_SEC` so we don't ask Jina for
        sub-second responses on a tight deadline.
    """
    # P95 from rolling stats
    if len(_FETCH_LATENCIES) >= 5:
        sorted_lat = sorted(_FETCH_LATENCIES)
        p95 = sorted_lat[int(len(sorted_lat) * 0.95) - 1]
        adaptive = max(_TIMEOUT_FLOOR_SEC, p95 * 1.5)
    else:
        adaptive = _TIMEOUT_BOOTSTRAP_SEC

    # Cap by remaining deadline budget (half of remaining, so one slow
    # subpage can't eat everything left)
    if deadline_ts is not None:
        remaining = deadline_ts - _time.monotonic()
        if remaining <= _TIMEOUT_FLOOR_SEC:
            return _TIMEOUT_FLOOR_SEC
        adaptive = min(adaptive, remaining / 2.0)

    return max(_TIMEOUT_FLOOR_SEC, adaptive)


def _record_fetch_latency(elapsed_sec: float) -> None:
    """Feed the rolling latency window so future calls self-tune."""
    if 0.05 <= elapsed_sec <= 60.0:  # ignore nonsense readings
        _FETCH_LATENCIES.append(elapsed_sec)


# Back-compat alias kept for now so existing references in the module
# below don't break. Resolved per-call via _adaptive_subpage_timeout.
_SUBPAGE_TIMEOUT_SEC = _TIMEOUT_BOOTSTRAP_SEC
_MAX_SUBPAGES = _MAX_SUBPAGES_SAFETY


@dataclass
class ScrapeBundle:
    """Homepage + 0..N high-signal subpages from the same domain.

    Returned by `fetch_bundle()`. Callers should pass the whole bundle
    to `gemini.analyze_product(scrape_result=bundle, ...)` — the Gemini
    prompt builder knows how to render a bundle as labelled sections.

    The `subpages` list is best-effort — 4xx / 5xx / timeouts are
    skipped silently. `sources` records what was tried (useful for
    debugging when ICP extraction comes back weak).
    """
    homepage: ScrapeResult
    subpages: List[ScrapeResult] = field(default_factory=list)
    sources: dict = field(default_factory=dict)  # {"/services": "jina"|"skipped"|"4xx"}

    # Back-compat: some callers expect a `.text` attribute (treating the
    # bundle as a ScrapeResult). Expose the homepage text for those.
    @property
    def text(self) -> str:
        return self.homepage.text if self.homepage else ""

    @property
    def title(self) -> str:
        return self.homepage.title if self.homepage else ""

    @property
    def meta_description(self) -> str:
        return self.homepage.meta_description if self.homepage else ""

    @property
    def h1s(self) -> List[str]:
        return self.homepage.h1s if self.homepage else []

    @property
    def h2s(self) -> List[str]:
        return self.homepage.h2s if self.homepage else []

    @property
    def char_count(self) -> int:
        total = self.homepage.char_count if self.homepage else 0
        for sp in self.subpages:
            total += sp.char_count or 0
        return total

    @property
    def source(self) -> str:
        return self.homepage.source if self.homepage else "empty"

    @property
    def combined_text(self) -> str:
        """Homepage + every subpage's text, each under a labelled header.

        Used to hand the FULL scraped site (not just the homepage or a short
        summary) to downstream LLM calls — e.g. the grounded ICP research in
        suggest-targeting. Order: homepage first, then subpages as fetched.
        """
        parts: List[str] = []
        if self.homepage and self.homepage.text:
            parts.append(f"# HOMEPAGE ({self.homepage.url})\n{self.homepage.text}")
        for sp in self.subpages or []:
            if sp and getattr(sp, "text", None):
                parts.append(f"# {sp.url}\n{sp.text}")
        return "\n\n".join(parts)


# Anchor-href regex. Captures the URL inside <a href="...">. Case-insensitive
# attribute name; HTML5 allows the quote style to be ", ', or absent. We
# only support quoted hrefs since that covers the overwhelming majority of
# real-world pages and keeps the regex simple.
_HREF_RE = re.compile(r'''<a\s+[^>]*href\s*=\s*["']([^"']+)["']''', re.IGNORECASE)

# Markdown link parser — Jina returns the homepage as markdown (the page
# is JS-rendered by Jina's browser, so links survive even on SPAs that
# strip <a href> from the static HTML). Format: `[anchor text](url "title")`.
# We capture the URL only.
_MD_LINK_RE = re.compile(r'''\[[^\]]+\]\(\s*(\S+?)(?:\s+"[^"]*")?\s*\)''')


def _slug_of(path: str) -> str:
    """Return the first path segment of a URL path, lower-cased and
    stripped of trailing slashes. Returns '' for root paths."""
    p = path.strip().lstrip("/")
    if not p:
        return ""
    first = p.split("/", 1)[0]
    return first.split("?", 1)[0].split("#", 1)[0].lower()


def _is_internal(absolute_url: str, base_host: str) -> bool:
    """True iff `absolute_url`'s host matches `base_host` (allowing
    www. variants)."""
    from urllib.parse import urlparse
    try:
        host = (urlparse(absolute_url).hostname or "").lower()
    except Exception:
        return False
    if not host or not base_host:
        return False
    if host == base_host:
        return True
    # www.foo.com matches foo.com (and vice versa)
    if host.startswith("www.") and host[4:] == base_host:
        return True
    if base_host.startswith("www.") and base_host[4:] == host:
        return True
    return False


def _discover_subpage_urls(
    homepage_html: str,
    base_url: str,
    max_subpages: int = _MAX_SUBPAGES,
    homepage_markdown: str = "",
) -> List[str]:
    """Parse the homepage HTML AND its Jina-rendered markdown for internal
    links matching the allow-list.

    Sources of links (both checked, deduped by slug):
      1. Raw HTML <a href="..."> — works for SSR sites
      2. Jina markdown [text](url) — works for JS-rendered SPAs where the
         static HTML is an empty React shell. Jina renders the page in a
         browser so links survive into the markdown output.

    Returns absolute URLs in priority order (allow-list order, then
    shortest path per slug). Caller is responsible for fetching them.
    """
    from urllib.parse import urljoin, urlparse

    if not homepage_html and not homepage_markdown:
        return []

    parsed_base = urlparse(base_url)
    base_host = (parsed_base.hostname or "").lower()
    if not base_host:
        return []

    # Map slug -> (path_length, absolute_url). We pick the SHORTEST PATH
    # per slug since /services is the index page and /services/strategy
    # is a deeper sub-section — for ICP extraction the index page is
    # usually the densest signal.
    candidate_by_slug: dict[str, tuple[int, str]] = {}

    # Combine href hits from BOTH sources into one iteration. Sources:
    #   - <a href="..."> from raw HTML (httpx fetch)
    #   - [text](url) from Jina markdown
    href_iter = []
    if homepage_html:
        for m in _HREF_RE.finditer(homepage_html):
            href_iter.append(m.group(1))
    if homepage_markdown:
        for m in _MD_LINK_RE.finditer(homepage_markdown):
            href_iter.append(m.group(1))

    for href in href_iter:
        href = (href or "").strip()
        if not href or href.startswith("#"):
            continue
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        try:
            absolute = urljoin(base_url, href)
        except Exception:
            continue
        if not _is_internal(absolute, base_host):
            continue
        # 2026-05-29 — Block image / video / document / asset URLs at
        # the discovery stage. Markdown image refs and <img> tags get
        # picked up by the link regex; without this filter Jina ends
        # up burning parallel slots fetching ".png" pages that just
        # return image alt-text (or 422 errors).
        if _is_asset_url(absolute):
            continue
        try:
            path = urlparse(absolute).path or "/"
        except Exception:
            continue
        slug = _slug_of(path)
        if not slug:
            continue
        # 2026-06-18 — Deny-list removed: every internal, non-asset subpage
        # is now eligible (no slug exclusions). Coverage is still bounded by
        # the asset filter, yield-stop, and _MAX_SUBPAGES_SAFETY.

        # Strip query strings + fragments so we don't fetch the same
        # page twice with different UTM params.
        clean_absolute = absolute.split("?", 1)[0].split("#", 1)[0]
        path_len = len(path)
        existing = candidate_by_slug.get(slug)
        if existing is None or path_len < existing[0]:
            candidate_by_slug[slug] = (path_len, clean_absolute)

    # Emit every internal, non-asset slug we found.
    # Sort by path length (shortest first) so /services beats
    # /services/strategy when both exist (the index page is denser
    # for ICP signal). Cap at max_subpages defence-in-depth.
    ordered: List[str] = []
    sorted_entries = sorted(
        candidate_by_slug.items(),
        key=lambda kv: (kv[1][0], kv[1][1]),  # (path_len, url)
    )
    for _slug, (_path_len, url) in sorted_entries:
        ordered.append(url)
        if len(ordered) >= max_subpages:
            break

    return ordered


# 2026-05-29 — Jina rate-limit throttle.
# Jina's free Reader endpoint starts returning 429 ("Too Many Requests")
# when we fire >5-6 concurrent fetches against the same host. The previous
# unbounded asyncio.gather meant a site with 15 subpages would burn most
# of them on 429s/timeouts (the slowest single subpage gated by Jina's
# IP-level throttle). A module-level semaphore caps concurrency so each
# subpage gets a fair chance and 429s vanish.
_MAX_JINA_CONCURRENCY = 5
_JINA_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_jina_semaphore() -> asyncio.Semaphore:
    """Lazily create the semaphore in the active event loop. We can't
    instantiate it at module-load time because no loop is running yet."""
    global _JINA_SEMAPHORE
    if _JINA_SEMAPHORE is None:
        _JINA_SEMAPHORE = asyncio.Semaphore(_MAX_JINA_CONCURRENCY)
    return _JINA_SEMAPHORE


async def _fetch_subpage(
    url: str, *, deadline_ts: Optional[float] = None,
) -> Optional[ScrapeResult]:
    """Fetch ONE subpage via Jina using an adaptive, deadline-aware timeout.

    Returns None on any failure — caller skips silently.

    Throttled through a module-level semaphore (max 5 concurrent Jina
    calls) so we don't trip Jina's 429 rate limit on sites with many
    discovered subpages.
    """
    timeout = _adaptive_subpage_timeout(deadline_ts)
    started = _time.monotonic()
    try:
        async with _get_jina_semaphore():
            result = await asyncio.wait_for(
                _fetch_via_jina(url, timeout=timeout),
                timeout=timeout + 1.0,
            )
            # Browser-engine fallback: fast mode returns empty on
            # client-rendered (SPA) subpages. Retry ONCE with the browser
            # engine — same render path that recovers SPA homepages — so
            # JS-only subpages aren't silently dropped. Bounded by the
            # remaining deadline; skipped if there isn't enough budget.
            if result is None or not result.text:
                br_timeout = max(timeout, 25.0)
                if deadline_ts is None or (deadline_ts - _time.monotonic()) > br_timeout:
                    result = await asyncio.wait_for(
                        _fetch_via_jina(url, timeout=br_timeout, use_browser=True),
                        timeout=br_timeout + 1.0,
                    )
        # Feed the rolling stats so the NEXT subpage call self-tunes.
        _record_fetch_latency(_time.monotonic() - started)
        return result
    except asyncio.TimeoutError:
        logger.info(
            "scrape subpage timeout url=%s (timeout=%.1fs)", url, timeout,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("scrape subpage failed url=%s: %s", url, exc)
        return None


async def fetch_bundle(
    url: str,
    *,
    retries: int = 3,
    deadline_ts: Optional[float] = None,
    max_subpages: int = _MAX_SUBPAGES_SAFETY,
) -> ScrapeBundle:
    """Fetch a homepage + a curated set of subpages in parallel — no
    hardcoded ceilings.

    Adaptive control:
      - `deadline_ts` (caller-provided monotonic deadline) propagates
        down to every subpage fetch. Without it we use the rolling-P95
        adaptive timeout.
      - Subpages are fetched in parallel via `asyncio.gather`, so
        wall-clock is bounded by the slowest single page, not the sum.
      - Yield-stop: after a min number of pages, if a fetched page's
        char-count is <10% of the running average, we drop further
        candidates (diminishing returns — usually footer/legal pages).

    The only "limits" left in the code are POLICY values, not arbitrary
    ceilings:
      - 10% yield threshold (matches Apollo's adaptive-stop)
      - 3-page minimum before yield-stop kicks in
      - 100-page absolute safety on link-spammy sites

    All subpage failures are silent — the homepage alone is always
    returned, so /analyze keeps working even when subpages are
    inaccessible.
    """
    # 2026-05-29 — Wall-clock timer for the whole fetch_bundle call.
    # Emitted as a TIMING log line at the end (mm:ss format) so we can
    # see in real-time how long scraping took for THIS /analyze call.
    _scrape_started = _time.monotonic()

    def _mmss(sec: float) -> str:
        sec = max(0.0, float(sec))
        m = int(sec // 60)
        s = sec - m * 60
        return f"{m:02d}:{s:05.2f}"

    # Derive homepage timeout from remaining deadline budget (or use a
    # generous bootstrap when caller didn't provide one).
    if deadline_ts is not None:
        homepage_timeout = max(_TIMEOUT_FLOOR_SEC, (deadline_ts - _time.monotonic()) * 0.6)
    else:
        homepage_timeout = 60.0  # bootstrap when no deadline given
    homepage = await fetch_html(url, retries=retries, total_timeout=homepage_timeout)

    # If the homepage scrape failed entirely there's nothing to traverse.
    if not homepage or not homepage.text:
        return ScrapeBundle(homepage=homepage or ScrapeResult(url=url), subpages=[])

    # Discover subpage URLs. We need raw HTML for this — Jina's markdown
    # drops most <a> tags. Re-fetch via httpx with a short timeout; if it
    # fails we just skip subpage discovery.
    homepage_html = ""
    # Base host for the internal-link check. When the input URL redirects
    # to a different host (e.g. spenzo.io 301→www.spenzo.ai), discovery
    # must use the FINAL host — otherwise every nav link (which points at
    # the canonical host) is treated as "external" and dropped, so the
    # Contact/About pages that carry the address are never fetched.
    effective_url = url
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            resp = await client.get(url)
            effective_url = str(resp.url) or url
            if resp.status_code < 400:
                homepage_html = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "scrape bundle: subpage-link discovery fetch failed url=%s: %s — homepage-only result",
            url, exc,
        )
    if effective_url != url:
        logger.info(
            "scrape bundle: %s redirects to %s — using final host for subpage discovery",
            url, effective_url,
        )

    # Pass BOTH the raw HTML (works for SSR pages) and the Jina-rendered
    # markdown (works for JS-rendered SPAs like zninth.com / Next.js
    # marketing sites — the static HTML is an empty React shell but Jina
    # renders the page in a browser so nav links appear in the markdown).
    homepage_markdown = (homepage.text or "") if homepage else ""
    subpage_urls = _discover_subpage_urls(
        homepage_html,
        effective_url,
        max_subpages=max_subpages,
        homepage_markdown=homepage_markdown,
    )
    sources: dict = {urlparse_path(u): "discovered" for u in subpage_urls}

    # Log which subpages were DISCOVERED (before fetching) so coverage is
    # easy to verify per /analyze call (e.g. /services, /products, /about,
    # /contact, /case-studies, …).
    if subpage_urls:
        logger.info(
            "scrape bundle: discovered %d subpages from %s (parallel fetch starting): %s",
            len(subpage_urls), url,
            ", ".join(urlparse_path(u) for u in subpage_urls),
        )

    if not subpage_urls:
        _elapsed = _time.monotonic() - _scrape_started
        logger.info(
            "scrape bundle: no eligible subpages found for %s (homepage-only)",
            url,
        )
        logger.info(
            "[TIMING] Scraping took %s — fetched homepage only (no subpages discovered) from %s",
            _mmss(_elapsed), url,
        )
        return ScrapeBundle(homepage=homepage, subpages=[], sources=sources)

    # Parallel fetch with deadline propagation. Each subpage gets a
    # per-call adaptive timeout based on (a) the caller's remaining
    # deadline, and (b) recent observed Jina latencies.
    results = await asyncio.gather(
        *[_fetch_subpage(u, deadline_ts=deadline_ts) for u in subpage_urls],
        return_exceptions=False,
    )

    # Yield-stop policy: walk results in discovery order and keep the
    # ones that materially add content. If a page's char_count drops
    # below 10% of the running average AND we already have at least 3
    # successful pages, mark the rest as "skipped_low_yield". This
    # matches the adaptive-stop pattern Apollo discovery already uses
    # (15% threshold there, 10% here since scrape pages vary more).
    subpages: List[ScrapeResult] = []
    running_total_chars = 0
    yield_stopped = False
    for sp_url, res in zip(subpage_urls, results):
        path = urlparse_path(sp_url)
        if yield_stopped:
            sources[path] = "skipped_low_yield"
            continue
        if res and res.text:
            char_count = res.char_count
            avg_so_far = (running_total_chars / max(len(subpages), 1)) if subpages else char_count
            # Activate yield-stop only after we have a baseline.
            if (
                len(subpages) >= _YIELD_MIN_PAGES
                and avg_so_far > 0
                and (char_count / avg_so_far) < _YIELD_THRESHOLD
            ):
                # This page is mostly empty (likely a stub like /privacy
                # that slipped past the deny list). Stop here.
                logger.info(
                    "scrape bundle: yield-stop at %s (chars=%d < %.0f%% of avg %.0f)",
                    sp_url, char_count, _YIELD_THRESHOLD * 100, avg_so_far,
                )
                yield_stopped = True
                sources[path] = "skipped_low_yield"
                continue
            subpages.append(res)
            running_total_chars += char_count
            sources[path] = res.source
        else:
            sources[path] = "empty"

    # ── Detailed scrape report — visible per /analyze call ────────────
    # Goal (per user 2026-05-29): "scrape as much info as possible from
    # the website that will be used for lead & ICP filter". This log
    # block makes it easy to verify in real time:
    #   - which subpages were discovered
    #   - which succeeded / which were skipped (deny / yield-stop / empty)
    #   - how much text content actually landed in the bundle
    #   - a small preview of the homepage + each subpage so the operator
    #     can sanity-check that the right content (services, industries,
    #     about, etc.) is being fed to Gemini.
    total_chars = homepage.char_count + sum(sp.char_count for sp in subpages)
    _elapsed = _time.monotonic() - _scrape_started
    logger.info(
        "scrape bundle ok url=%s homepage_chars=%d subpages=%d/%d total_chars=%d",
        url, homepage.char_count, len(subpages), len(subpage_urls), total_chars,
    )
    logger.info(
        "[TIMING] Scraping took %s — fetched %d of %d pages, %s characters from %s",
        _mmss(_elapsed),
        len(subpages) + 1,
        len(subpage_urls) + 1,
        f"{total_chars:,}",
        url,
    )

    # Per-source breakdown (homepage + each subpage).
    def _preview(text_blob: str, n: int = 240) -> str:
        s = (text_blob or "").strip().replace("\n", " ").replace("\r", " ")
        s = " ".join(s.split())  # collapse whitespace
        return (s[:n] + "…") if len(s) > n else s

    logger.info(
        "scrape detail — HOMEPAGE  url=%s  chars=%d  source=%s",
        url, homepage.char_count, homepage.source or "?",
    )
    logger.info("scrape preview HOMEPAGE: %s", _preview(homepage.text))

    for sp in subpages:
        logger.info(
            "scrape detail — SUBPAGE   url=%s  chars=%d  source=%s",
            sp.url, sp.char_count, sp.source or "?",
        )
        logger.info("scrape preview SUBPAGE %s: %s",
                    urlparse_path(sp.url), _preview(sp.text))

    # Per-URL outcome summary (status of every discovered URL).
    if sources:
        summary_parts = [f"{p}={s}" for p, s in sources.items()]
        logger.info("scrape outcomes: %s", " | ".join(summary_parts))

    return ScrapeBundle(homepage=homepage, subpages=subpages, sources=sources)


def urlparse_path(url: str) -> str:
    """Tiny helper: extract just the path from a URL (e.g.
    'https://x.com/services' → '/services'). Used for log-friendly
    `sources` dict keys."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).path or "/"
    except Exception:
        return url
