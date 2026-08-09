"""GTM LinkedIn Agent — Playwright browser primitives.

Low-level, anti-detection-aware browser actions used by the worker
(`gtm/linkedin/worker.py`). Everything Playwright is imported LAZILY (inside
functions) so this module imports cleanly in the main backend (which has no
Playwright); only the worker container ships Chromium + playwright.

Anti-detection (Section 3 of the plan): every in-page step waits a fresh random
jitter from the account's settings; the BrowserContext is seeded with the
account's persisted device profile (UA / viewport / locale) so the fingerprint
stays consistent.

⚠️ LinkedIn DOM selectors below follow the documented strategy in the plan but
MUST be verified against the live UI on first run (LinkedIn changes markup); they
are centralized here so a selector fix is a one-file change.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from gtm.linkedin.logs import setup as _setup_logs

_setup_logs()
logger = logging.getLogger("pipelyt.gtm_linkedin.browser")

# Session-level flag: set True when the free-account upsell fires (out of
# personalized invite notes). Once set, all subsequent do_connect calls skip
# "Add a note" entirely and just click Send (sends without a note). Reset
# when a new browser session starts.
_notes_exhausted = False

_LOGIN_REDIRECT_MARKERS = ("/login", "/checkpoint", "/uas/login", "authwall")


# ── Anti-detection helpers (deterministic, no Playwright) ─────────────────────
def jitter_seconds(settings: Any, *, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    """A fresh random delay (seconds) drawn from the account's configured range.
    Math.random is unavailable in some sandboxes — use the stdlib `random`."""
    import random

    a = lo if lo is not None else float(getattr(settings, "random_delay_min_seconds", 3) or 3)
    b = hi if hi is not None else float(getattr(settings, "random_delay_max_seconds", 12) or 12)
    if b < a:
        a, b = b, a
    return round(random.uniform(a, b), 2)


def human_pause(settings: Any, *, lo: Optional[float] = None, hi: Optional[float] = None) -> None:
    time.sleep(jitter_seconds(settings, lo=lo, hi=hi))


def in_working_hours(settings: Any, *, now_utc: Optional[datetime] = None) -> bool:
    """True if the current time falls inside the account's working window in its
    own timezone. Out-of-window slots are deferred by the scheduler."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(getattr(settings, "timezone", None) or "Asia/Kolkata")
    except Exception:
        tz = None
    now = (now_utc or datetime.utcnow())
    local = now.astimezone(tz) if tz else now
    start = str(getattr(settings, "working_start_time", "07:00") or "07:00")
    end = str(getattr(settings, "working_end_time", "22:00") or "22:00")

    def _mins(hhmm: str) -> int:
        h, _, m = hhmm.partition(":")
        return int(h) * 60 + int(m or 0)

    cur = local.hour * 60 + local.minute
    return _mins(start) <= cur <= _mins(end)


def _chromium_launch_args() -> List[str]:
    """Chromium flags tuned to the runtime environment.

    Lambda restricts forking and has tiny /dev/shm (~64 MB), so it needs
    --single-process, --no-zygote, and --disable-dev-shm-usage.  Fargate (and
    local) can run multi-process Chromium which is more stable.  We detect
    Lambda via AWS_LAMBDA_FUNCTION_NAME (set automatically by the runtime)."""
    args = [
        "--no-sandbox",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
    ]
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        args += ["--single-process", "--no-zygote", "--disable-dev-shm-usage"]
    else:
        args.append("--disable-dev-shm-usage")
    return args


# ── Browser context (lazy Playwright) ─────────────────────────────────────────
@contextlib.contextmanager
def browser_session(
    cookies_json: Optional[str],
    device_profile: Any,
    *,
    headless: Optional[bool] = None,
) -> Iterator[Any]:
    """Launch Chromium, build a BrowserContext seeded with the account cookies +
    its persisted device profile, yield a ready `page`. Closes everything on exit.

    `device_profile` is a GtmLinkedInBrowserProfile-shaped object (user_agent,
    viewport "WxH", browser_locale, browser_timezone).

    `headless` defaults to the `GTM_LINKEDIN_HEADLESS` env var (true unless set to
    'false') — set it false to WATCH the browser when testing / fixing selectors."""
    if headless is None:
        headless = os.getenv("GTM_LINKEDIN_HEADLESS", "true").strip().lower() != "false"
    from playwright.sync_api import sync_playwright  # lazy

    ua = getattr(device_profile, "user_agent", None) or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    vp = str(getattr(device_profile, "viewport", "1366x768") or "1366x768")
    try:
        w, h = (int(x) for x in vp.lower().split("x"))
    except Exception:
        w, h = 1366, 768
    locale = getattr(device_profile, "browser_locale", None) or "en-US"
    tz = getattr(device_profile, "browser_timezone", None) or "Asia/Kolkata"

    launch_args = _chromium_launch_args()
    pw = sync_playwright().start()
    browser = None
    context = None
    try:
        browser = pw.chromium.launch(headless=headless, args=launch_args)
        context = browser.new_context(
            user_agent=ua, viewport={"width": w, "height": h},
            locale=locale, timezone_id=tz,
        )
        if cookies_json:
            try:
                context.add_cookies(json.loads(cookies_json))
            except Exception:
                logger.warning("gtm_linkedin: failed to seed cookies (corrupt blob?)")
        page = context.new_page()
        yield page
    finally:
        with contextlib.suppress(Exception):
            if context:
                context.close()
        with contextlib.suppress(Exception):
            if browser:
                browser.close()
        with contextlib.suppress(Exception):
            pw.stop()


def _fingerprint_from_profile(device_profile: Any):
    """Resolve (user_agent, width, height, locale, timezone) from a stored
    device profile, with the same defaults browser_session uses. One resolver so
    the fingerprint is IDENTICAL between login capture and later automation."""
    ua = getattr(device_profile, "user_agent", None) or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    vp = str(getattr(device_profile, "viewport", "1366x768") or "1366x768")
    try:
        w, h = (int(x) for x in vp.lower().split("x"))
    except Exception:
        w, h = 1366, 768
    locale = getattr(device_profile, "browser_locale", None) or "en-US"
    tz = getattr(device_profile, "browser_timezone", None) or "Asia/Kolkata"
    return ua, w, h, locale, tz


@contextlib.contextmanager
def browser_session_persistent(
    account_id: int,
    device_profile: Any,
    *,
    headless: Optional[bool] = None,
    proxy: Optional[Dict[str, str]] = None,
    save_on_exit=True,
) -> Iterator[Any]:
    """PERSISTENT-profile session (industry-standard "reuse one profile").

    Restores the account's FULL browser profile (cookies + localStorage +
    IndexedDB + cache) from S3 into a user_data_dir, launches a persistent
    Chromium context with the account's fixed fingerprint (and optional per-account
    proxy), yields a ready `page`, then saves the profile back to S3 on exit. No
    per-run cookie seeding — the restored profile is already logged in.

    `proxy` (optional) = {"server","username","password"} routes traffic through
    the account's dedicated residential IP (Phase 3).

    `save_on_exit` — True (default) always persists the profile on close (correct
    for the automation worker: keep the refreshed session). Pass a callable to
    decide at exit time (the login flow passes `lambda: logged_in` so a failed/
    timed-out login never overwrites a good profile or leaves an orphan).
    """
    if headless is None:
        headless = os.getenv("GTM_LINKEDIN_HEADLESS", "true").strip().lower() != "false"
    from playwright.sync_api import sync_playwright  # lazy
    from . import profile_store

    ua, w, h, locale, tz = _fingerprint_from_profile(device_profile)

    launch_args = _chromium_launch_args()

    user_data_dir = tempfile.mkdtemp(prefix=f"li_udd_{account_id}_")
    restored = profile_store.load_profile(account_id, user_data_dir)
    logger.info("browser_session_persistent: account=%s restored_profile=%s", account_id, restored)

    pw = sync_playwright().start()
    context = None
    try:
        launch_kwargs: Dict[str, Any] = dict(
            user_data_dir=user_data_dir,
            headless=headless,
            args=launch_args,
            user_agent=ua,
            viewport={"width": w, "height": h},
            locale=locale,
            timezone_id=tz,
        )
        if proxy:
            launch_kwargs["proxy"] = proxy
        context = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        yield page
    finally:
        with contextlib.suppress(Exception):
            if context:
                context.close()
        with contextlib.suppress(Exception):
            pw.stop()
        # Persist the (possibly refreshed) profile back so the next run reuses it —
        # unless save_on_exit says not to (login flow skips a failed login).
        _do_save = save_on_exit() if callable(save_on_exit) else bool(save_on_exit)
        if _do_save:
            with contextlib.suppress(Exception):
                profile_store.save_profile(account_id, user_data_dir)
        with contextlib.suppress(Exception):
            shutil.rmtree(user_data_dir, ignore_errors=True)


def export_cookies(page: Any) -> str:
    """Serialize the context's current cookies → JSON string (to encrypt+store)."""
    return json.dumps(page.context.cookies())


# ── Session validation ────────────────────────────────────────────────────────
def session_is_valid(page: Any) -> bool:
    """Cheap authenticated check: if LinkedIn bounces us to a login/checkpoint/
    authwall URL, the session is dead. Tolerates the case where the page is
    already on the feed (just-completed manual login) and the benign
    'interrupted by another navigation' race when LinkedIn self-redirects."""
    try:
        cur = (page.url or "").lower()
        if "linkedin.com/feed" in cur:
            return True
        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            if "interrupted" not in str(e).lower():
                raise
            # LinkedIn was already navigating (it redirected us) → settle, then re-read.
            with contextlib.suppress(Exception):
                page.wait_for_load_state("domcontentloaded", timeout=15000)
        url = (page.url or "").lower()
        return ("linkedin.com" in url) and not any(m in url for m in _LOGIN_REDIRECT_MARKERS)
    except Exception:
        logger.warning("gtm_linkedin: session validation navigation failed", exc_info=True)
        return False


# ── Actions (selectors per plan — VERIFY against live LinkedIn on first run) ──
def _debug_dir() -> str:
    """Where to drop li_debug_* files. In Lambda the only writable path is /tmp
    (the task dir is read-only); locally use the cwd so the test script finds them."""
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("LAMBDA_TASK_ROOT"):
        return "/tmp"
    return os.getcwd()


def _debug_shot(page: Any, tag: str) -> None:
    """Best-effort screenshot + HTML dump + URL/title for debugging selector
    failures. Saved as li_debug_<tag>.png / .html (cwd locally, /tmp in Lambda).
    In Lambda the files aren't easily retrievable, so the URL+title (and the
    `_log_buttons` output) logged to CloudWatch are the primary signal there."""
    base = os.path.join(_debug_dir(), f"li_debug_{tag}")
    with contextlib.suppress(Exception):
        page.screenshot(path=f"{base}.png", full_page=True)
    with contextlib.suppress(Exception):
        with open(f"{base}.html", "w", encoding="utf-8") as fh:
            fh.write(page.content())
    with contextlib.suppress(Exception):
        logger.warning("debug: saved %s.{png,html} — url=%s title=%r",
                       base, page.url, (page.title() or "")[:80])


def _dismiss_consent(page: Any) -> None:
    """Dismiss the EU cookie-consent banner if LinkedIn shows it (it blocks the
    login form). Best-effort — no-op when absent."""
    for sel in ("button[action-type='ACCEPT']", "button:has-text('Accept')",
                "button:has-text('Agree')", "button[data-tracking-control-name*='accept']"):
        with contextlib.suppress(Exception):
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=2500)
                return


def _find_visible_form(page: Any, selector: str, *, timeout_s: int = 30) -> Any:
    """Return the frame whose `selector` has a VISIBLE match — searches the main
    frame AND every child iframe (LinkedIn's login form can render in an iframe,
    while the main frame holds a hidden duplicate). Polls up to `timeout_s`."""
    for _ in range(int(timeout_s)):
        for fr in page.frames:
            try:
                loc = fr.locator(selector)
                for i in range(min(loc.count(), 8)):
                    if loc.nth(i).is_visible():
                        return fr
            except Exception:  # noqa: BLE001
                continue
        time.sleep(1)
    return None


def _click_submit(frame: Any) -> bool:
    """Click the login Sign-in submit. Uses the EXACT accessible name 'Sign in'
    (role=button) so it never hits the 'Sign in with Microsoft/Apple' OAuth
    buttons or the 'Sign in' heading. Falls back to type=submit / aria-label."""
    with contextlib.suppress(Exception):
        loc = frame.get_by_role("button", name="Sign in", exact=True)
        for i in range(min(loc.count(), 4)):
            el = loc.nth(i)
            if el.is_visible():
                with contextlib.suppress(Exception):
                    el.scroll_into_view_if_needed(timeout=2000)
                el.click(timeout=6000)
                return True
    return _click_first_visible(frame, ["button[type=submit]", "[aria-label='Sign in']"])


def _fill_first_visible(page: Any, selector: str, value: str, *, timeout_ms: int = 6000) -> bool:
    """Fill the first VISIBLE element matching `selector` (handles pages that
    render a hidden duplicate form alongside the visible one)."""
    loc = page.locator(selector)
    for i in range(min(loc.count(), 8)):
        el = loc.nth(i)
        try:
            if el.is_visible():
                el.fill(value, timeout=timeout_ms)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _describe_checkpoint(page: Any) -> Dict[str, Any]:
    """Inspect a LinkedIn checkpoint page so the LOG tells us what it actually is:
    an email/SMS code entry, a CAPTCHA/human-check, or an 'is this you' confirm —
    instead of blindly assuming 'enter a code'."""
    out = {"heading": "", "text": "", "has_code_input": False, "has_captcha": False}
    with contextlib.suppress(Exception):
        out["heading"] = (page.locator("h1, h2").first.inner_text(timeout=3000) or "").strip()[:120]
    with contextlib.suppress(Exception):
        out["text"] = (page.locator("main, body").first.inner_text(timeout=3000) or "").strip()
    with contextlib.suppress(Exception):
        out["has_code_input"] = page.locator(
            "input[name=pin], input[autocomplete='one-time-code'], "
            "input#input__phone_verification_pin, input[type=tel]"
        ).count() > 0
    with contextlib.suppress(Exception):
        captcha_el = page.locator(
            "iframe[src*='captcha' i], iframe[title*='captcha' i], "
            "#captcha-internal, div[class*='captcha' i], div[id*='captcha' i]"
        ).count() > 0
        txt = (out["text"] or "").lower()
        wordy = ("verify you" in txt or "let's do a quick" in txt or "puzzle" in txt
                 or "prove you" in txt or "are you a human" in txt)
        out["has_captcha"] = bool(captcha_el or (wordy and not out["has_code_input"]))
    return out


def do_login(page: Any, email: str, password: str) -> Dict[str, Any]:
    """Headless login. Returns {status: 'active'|'needs_code'|'failed',
    cookies?: json, detail?: str}. Tolerates the cookie-consent wall and both
    LinkedIn login layouts (id= and name= field variants)."""
    try:
        logger.info("login: opening LinkedIn login page for %s", email)
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
        _dismiss_consent(page)
        # Email + password fields differ across layouts: id on /login, name on the
        # home-page sign-in form. The new UI uses React ids (not #username) — match
        # by autocomplete/type, and fill the first VISIBLE one (the page renders a
        # hidden duplicate form alongside the visible one).
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=15000)
        user_sel = ("input[autocomplete='username'], input#username, "
                    "input[name='session_key'], input[type='email']")
        pass_sel = ("input[autocomplete='current-password'], input#password, "
                    "input[name='session_password'], input[type='password']")
        # The visible login form can live in an IFRAME (the main frame holds a
        # hidden duplicate). Poll the main frame + every child frame for a frame
        # that actually shows a visible email field, then fill IT.
        frame = _find_visible_form(page, user_sel, timeout_s=30)
        if frame is None:
            logger.warning("login: FAILED — no visible login form found (url=%s). Saved li_debug_login_no_form.*", page.url)
            _debug_shot(page, "login_no_form")
            return {"status": "failed",
                    "detail": f"login form not found (url={page.url}). See li_debug_login_no_form.png"}
        logger.info("login: form found, filling credentials")
        if not _fill_first_visible(frame, user_sel, email):
            logger.warning("login: FAILED — email field not fillable. Saved li_debug_login_no_form.*")
            _debug_shot(page, "login_no_form")
            return {"status": "failed", "detail": "email field not fillable"}
        _fill_first_visible(frame, pass_sel, password)
        if not _click_submit(frame):
            logger.warning("login: FAILED — 'Sign in' submit button not found. Saved li_debug_login_no_submit.*")
            _debug_shot(page, "login_no_submit")
            return {"status": "failed", "detail": "Sign in submit button not found"}
        logger.info("login: submitted, waiting for result")
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=20000)
        url = (page.url or "").lower()
        if any(k in url for k in ("/checkpoint", "/challenge", "two-step", "add-phone", "verification")):
            info = _describe_checkpoint(page)
            logger.info("login: CHECKPOINT (url=%s) heading=%r code_input=%s captcha=%s | text=%r",
                        page.url, info["heading"], info["has_code_input"], info["has_captcha"], info["text"][:400])
            _debug_shot(page, "login_checkpoint")
            # A CAPTCHA / human-check with NO code field can't be solved headless and
            # sends no code — surface that clearly instead of asking for a code.
            if info["has_captcha"] and not info["has_code_input"]:
                return {"status": "failed",
                        "detail": "LinkedIn showed a human-verification/CAPTCHA (no code sent). "
                                  "A headless cloud login is being bot-blocked — capture the session "
                                  "from a trusted (local) login instead. See li_debug_login_checkpoint.*"}
            return {"status": "needs_code",
                    "detail": (info["heading"] or "verification required")}
        if session_is_valid(page):
            logger.info("login: authenticated session confirmed")
            return {"status": "active", "cookies": export_cookies(page)}
        logger.warning("login: FAILED — submitted but not authenticated (url=%s). Saved li_debug_login_not_authed.* "
                       "(usually wrong password or an unexpected interstitial)", page.url)
        _debug_shot(page, "login_not_authed")
        return {"status": "failed",
                "detail": f"login did not reach an authenticated session (url={page.url}). "
                          "See li_debug_login_not_authed.png"}
    except Exception as e:  # noqa: BLE001
        logger.exception("login: EXCEPTION during login for %s: %s", email, e)
        _debug_shot(page, "login_exception")
        return {"status": "failed", "detail": str(e)[:200]}


def submit_challenge(page: Any, code: str) -> Dict[str, Any]:
    """Submit a 2FA/checkpoint code on the challenge page already loaded."""
    try:
        if not _fill_first_visible(page, (
            "input[autocomplete='one-time-code'], input[name='pin'], "
            "input#input__phone_verification_pin, input[type='tel']"
        ), code):
            _debug_shot(page, "challenge_no_input")
            return {"status": "failed", "detail": "code field not found"}
        # "Recognize this device" → reduces how often LinkedIn re-challenges this
        # session later (best-effort; ignored if the checkbox isn't present).
        with contextlib.suppress(Exception):
            page.locator("input[type=checkbox]").first.check(timeout=2000)
        _click_first_visible(page, [
            "button[type=submit]", "button:has-text('Submit')",
            "button:has-text('Verify')", "[aria-label*='Submit' i]",
        ])
        # LinkedIn holds background connections open, so 'networkidle' never fires
        # and would always time out — wait tolerantly, then verify the session.
        with contextlib.suppress(Exception):
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        with contextlib.suppress(Exception):
            page.wait_for_timeout(3000)
        if session_is_valid(page):
            logger.info("challenge: code accepted — authenticated session confirmed")
            return {"status": "active", "cookies": export_cookies(page)}
        # Still on a checkpoint? (wrong code / another factor) — report clearly.
        url = (page.url or "").lower()
        if any(k in url for k in ("/checkpoint", "/challenge", "verification")):
            _debug_shot(page, "challenge_not_active")
            return {"status": "needs_code", "detail": f"still on checkpoint (url={page.url}) — code may be wrong/expired"}
        _debug_shot(page, "challenge_not_active")
        return {"status": "failed", "detail": f"challenge not accepted (url={page.url})"}
    except Exception as e:  # noqa: BLE001
        _debug_shot(page, "challenge_exception")
        return {"status": "failed", "detail": str(e)[:200]}


def _normalize_profile_url(url: str) -> str:
    """Force HTTPS + a trailing slash on a /in/ profile URL. Navigating to a bare
    `http://…/in/handle` makes LinkedIn's http→https redirect drop the path and
    land on the HOME FEED instead of the profile — so the connect step sees no
    Connect. The canonical `https://www.linkedin.com/in/handle/` loads the profile."""
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    elif not u.startswith("https://"):
        u = "https://" + u.lstrip("/")
    if "/in/" in u and not u.endswith("/"):
        u = u + "/"
    return u


def open_profile(page: Any, profile_url: str, settings: Any) -> None:
    url = _normalize_profile_url(profile_url)
    logger.info("open_profile: navigating to %s", url or "(empty url!)")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    human_pause(settings, lo=4, hi=9)
    with contextlib.suppress(Exception):
        page.mouse.wheel(0, 600)  # gentle scroll
    human_pause(settings, lo=2, hi=6)


def capture_member_id(page: Any) -> Optional[str]:
    """Best-effort stable member id from the profile (for later thread lookup)."""
    with contextlib.suppress(Exception):
        return page.evaluate(
            "() => { const m = document.body.innerHTML.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/); return m ? m[1] : null; }"
        )
    return None


def capture_profile_identity(page: Any) -> Dict[str, Optional[str]]:
    """Read identity off the open profile: {url, name}.

    Fail-fast: everything runs inside a single JS eval (one round trip, no
    Playwright wait/timeout stacking).  Total budget is under a second even
    when nothing matches.  Never blocks the caller for tens of seconds
    scanning selectors that don't exist."""
    out: Dict[str, Optional[str]] = {"url": None, "name": None}
    with contextlib.suppress(Exception):
        out["url"] = page.url
    try:
        name = page.evaluate("""() => {
            // 1) Traditional h1
            const h1 = document.querySelector('main h1, h1.text-heading-xlarge, section h1, h1');
            if (h1) {
                const t = (h1.innerText || h1.textContent || '').trim();
                if (t) return t;
            }
            // 2) LinkedIn 2026 top-card aria-labels — filter out action verbs
            const bad = new Set(['message','connect','follow','invite','profile','view','more']);
            const areas = document.querySelectorAll(
                'section.pv-top-card [aria-label], div.pv-top-card [aria-label]'
            );
            for (const el of areas) {
                const a = (el.getAttribute('aria-label') || '').trim();
                const first = (a.split(' ')[0] || '').toLowerCase();
                if (a && a.includes(' ') && !bad.has(first)) return a;
            }
            // 3) meta og:title — always present on profile pages
            const og = document.querySelector('meta[property="og:title"]');
            if (og) {
                const c = (og.getAttribute('content') || '').trim();
                if (c) {
                    // "Patrick Michalina - Groq | LinkedIn" → "Patrick Michalina"
                    return c.split('|')[0].split(' - ')[0].trim();
                }
            }
            // 4) document.title as a last resort
            const t = (document.title || '').trim();
            if (t) return t.split('|')[0].split(' - ')[0].trim();
            return '';
        }""") or ""
        if name:
            out["name"] = name[:255]
    except Exception:  # noqa: BLE001
        pass
    return out


def _log_buttons(page: Any, tag: str) -> None:
    """Dump the first ~60 clickables' (tag, text, aria-label) — the primary
    selector-debug signal in production where the screenshot files aren't easily
    retrievable.

    Prints one item per line so the log is READABLE (a JSON list on one line is
    unreadable in CloudWatch — the exact problem we solved after seeing 60-item
    walls of text on every profile open).

    Only fires when GTM_LINKEDIN_LOG_BUTTONS_ON_SUCCESS=1 OR the tag prefix marks
    a failure (contains 'no_', 'fail', 'unreadable', 'error'). Success-path
    button dumps used to run on every profile open and drowned the logs."""
    tag_lower = (tag or "").lower()
    is_failure_tag = any(k in tag_lower for k in ("no_", "fail", "unreadable", "error"))
    always_on = os.getenv("GTM_LINKEDIN_LOG_BUTTONS_ON_SUCCESS", "0").strip() == "1"
    if not (is_failure_tag or always_on):
        return
    with contextlib.suppress(Exception):
        items = page.evaluate(
            "() => Array.from(document.querySelectorAll('button,[aria-label],[role=button]')).slice(0,60)"
            ".map(b => ({tag:b.tagName.toLowerCase(), t:(b.innerText||'').trim().slice(0,28), "
            "a:(b.getAttribute('aria-label')||'').slice(0,48)}))"
            ".filter(x => x.t || x.a)"
        )
        logger.warning("clickables[%s]: %d items — dumping one per line ↓", tag, len(items or []))
        for i, it in enumerate(items or [], 1):
            logger.warning(
                "  [%s #%02d] <%s> text=%r aria-label=%r",
                tag, i, it.get("tag", "?"), it.get("t", ""), it.get("a", ""),
            )


def _click_first_visible(page: Any, selectors, *, timeout_ms: int = 6000) -> bool:
    """Click the first VISIBLE element matching any selector (tried in order,
    up to 6 matches each). Returns True if something was clicked."""
    for sel in selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if el.is_visible():
                    # visible != clickable (cookie banner / spinner / tooltip can
                    # overlay) — scroll it in, then let click() auto-wait for actionability.
                    with contextlib.suppress(Exception):
                        el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=timeout_ms)
                    return True
    return False


# ── Playwright role-based locators (more robust than CSS + :has-text()) ───────
# Playwright docs recommend role > label > text > test_id > CSS. Role-based
# locators use the accessibility tree — they survive DOM churn as long as the
# element's ARIA role/name is stable. LinkedIn's Message/Send/etc. all expose
# accessible names, so role lookup is our strongest first attempt. Every helper
# below RETURNS a bool: True if it acted, False if it didn't find anything —
# callers fall back to CSS selectors on False.
def _click_by_role(page: Any, role: str, name: str, *, timeout_ms: int = 6000) -> bool:
    """Click page.get_by_role(role, name=...) if visible. Returns True on success."""
    try:
        loc = page.get_by_role(role, name=name).first
        if loc.is_visible(timeout=2000):
            with contextlib.suppress(Exception):
                loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=timeout_ms)
            logger.info("locator: clicked by role=%r name=%r", role, name)
            return True
    except Exception as e:  # noqa: BLE001
        logger.debug("locator: get_by_role(%r, name=%r) miss — %s", role, name, e)
    return False


def _fill_by_role(page: Any, role: str, name: str, value: str, *, timeout_ms: int = 6000) -> bool:
    """Fill page.get_by_role(role, name=...) if visible. Returns True on success."""
    try:
        loc = page.get_by_role(role, name=name).first
        if loc.is_visible(timeout=2000):
            loc.fill(value, timeout=timeout_ms)
            logger.info("locator: filled by role=%r name=%r", role, name)
            return True
    except Exception as e:  # noqa: BLE001
        logger.debug("locator: get_by_role(%r, name=%r).fill miss — %s", role, name, e)
    return False


def _fill_by_placeholder(page: Any, placeholder: str, value: str, *, timeout_ms: int = 6000) -> bool:
    """Fill page.get_by_placeholder(...) if visible. Returns True on success."""
    try:
        loc = page.get_by_placeholder(placeholder).first
        if loc.is_visible(timeout=2000):
            loc.fill(value, timeout=timeout_ms)
            logger.info("locator: filled by placeholder=%r", placeholder)
            return True
    except Exception as e:  # noqa: BLE001
        logger.debug("locator: get_by_placeholder(%r) miss — %s", placeholder, e)
    return False


# ── Retry wrappers for transient Playwright failures ─────────────────────────
def _with_retries(fn, *, retries: int = 3, delay_s: float = 0.8, what: str = "op"):
    """Run `fn` up to `retries` times, sleeping between attempts. Re-raises the
    last error if all attempts fail. For flaky timeouts / transient detaches."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning("gtm_linkedin: %s failed (attempt %d/%d): %s", what, attempt, retries, str(e)[:120])
            time.sleep(delay_s)
    raise last


def safe_click(page: Any, selector: str, *, retries: int = 3, timeout_ms: int = 6000) -> bool:
    def _do():
        el = page.locator(selector).first
        with contextlib.suppress(Exception):
            el.scroll_into_view_if_needed(timeout=2000)
        el.click(timeout=timeout_ms)
        return True
    return _with_retries(_do, retries=retries, what=f"click {selector[:40]}")


def safe_fill(page: Any, selector: str, value: str, *, retries: int = 3, timeout_ms: int = 6000) -> bool:
    def _do():
        page.fill(selector, value, timeout=timeout_ms)
        return True
    return _with_retries(_do, retries=retries, what=f"fill {selector[:40]}")


def _open_invite(page: Any, named_connect: Optional[str], top: str, settings: Any) -> bool:
    """Click THIS lead's Connect — direct in the top card, else under the '•••'
    More menu — to open the invite dialog. Returns True if clicked, False if there
    is no Connect (Follow-only / restricted). Top-card scoped so it never invites a
    'More profiles' sidebar suggestion. Reusable so the no-note fallback can re-open."""
    if _click_first_visible(page, [s for s in [
        named_connect,
        f"{top} [aria-label*='invite' i][aria-label*='to connect' i]",
        f"{top} [aria-label*='to connect' i]",
        f"{top} button:has-text('Connect')",
    ] if s]):
        return True
    connect_sels = [s for s in [
        named_connect,
        "div.artdeco-dropdown__item:has-text('Connect')",
        "[role='menuitem']:has-text('Connect')",
    ] if s]
    # ONLY the profile action-bar 'More' (•••). Use exact text 'More' / aria-label —
    # NOT has-text('More'), which also matches feed-post '… more' expanders and
    # other controls that can navigate away (e.g. to the home feed).
    more = page.locator(
        "main button[aria-label='More'], main button[aria-label*='More actions' i], "
        "main button:text-is('More')"
    )
    for i in range(min(more.count(), 6)):
        btn = more.nth(i)
        if not btn.is_visible():
            continue
        with contextlib.suppress(Exception):
            btn.scroll_into_view_if_needed(timeout=2000)
            btn.click(timeout=6000)
        human_pause(settings, lo=1, hi=2)
        if _click_first_visible(page, connect_sels):
            return True
        with contextlib.suppress(Exception):
            page.keyboard.press("Escape")  # close this menu, try the next More
    return False


def do_connect(page: Any, profile_url: str, note: str, settings: Any) -> Dict[str, Any]:
    """Open profile → Connect → Add note → Send. Returns {ok, member_id?, detail?}.

    Scoped to <main> (the profile top card) so the sidebar "More profiles"
    Connect buttons and the "…more" text-expander don't interfere; waits for the
    action bar to render; tries text + aria-label selectors, first-visible wins.
    Logs the real buttons on a miss so the selector can be pinned exactly."""
    try:
        open_profile(page, profile_url, settings)
        member_id = capture_member_id(page)
        identity = capture_profile_identity(page)  # final URL + name (self-heal)
        with contextlib.suppress(Exception):
            page.evaluate("() => window.scrollTo(0, 0)")  # bring the action bar into view
        # Wait for the top-card actions to render (React renders them late).
        with contextlib.suppress(Exception):
            page.wait_for_selector(
                "main button:has-text('Connect'), main button:has-text('Message'), "
                "main button[aria-label*='More actions' i]",
                timeout=15000,
            )
        human_pause(settings, lo=1, hi=2)

        # Gate: read the state first (same open) — only send a request if the
        # profile is actually NOT_CONNECTED. Skip (and let the worker route) when
        # already pending/connected; bail on an ambiguous read so we never guess.
        ps = detect_profile_state(page)
        logger.info("connect: profile state=%s confidence=%s signals=%s (%s)",
                    ps["state"], ps["confidence"], ps["signals"], profile_url)
        if ps["confidence"] < 50:
            logger.warning("connect: ambiguous profile state (confidence %s) — will retry. Saved li_debug_state_unknown.*",
                           ps["confidence"])
            _debug_shot(page, "state_unknown")
            return {"ok": False, "state": "UNKNOWN", "confidence": ps["confidence"],
                    "member_id": member_id, "identity": identity, "signals": ps["signals"],
                    "detail": "profile state ambiguous"}
        if ps["state"] in ("PENDING", "CONNECTED"):
            logger.info("connect: already %s — no new request sent", ps["state"])
            return {"ok": True, "state": ps["state"], "confidence": ps["confidence"],
                    "member_id": member_id, "identity": identity, "signals": ps["signals"]}

        # IMPORTANT: target THIS lead's own Connect — its aria-label is
        # "Invite <Lead Name> to connect". A generic "to connect" match also hits
        # the "More profiles for you" / "People also follow" suggestion cards and
        # would invite the WRONG person. So we scope by the lead's name.
        lead_name = ((identity or {}).get("name") or "").strip()
        safe_name = lead_name.replace("'", "").replace('"', "").strip()
        named_connect = (f"[aria-label*='{safe_name}' i][aria-label*='to connect' i]"
                         if safe_name else None)

        # The lead's OWN Connect is matched first by name (named_connect) — that's
        # the precise disambiguator vs the "More profiles" sidebar. _TOP scopes the
        # name-less fallback to <main> (the sidebar suggestions live in an <aside>).
        # NOTE: we deliberately do NOT use `section:has(h1)` — the Connect <a> isn't
        # always inside that section, which made the click miss and fall to the
        # fragile More-menu path that could navigate to the feed.
        _TOP = "main"

        # Open the invite dialog (direct Connect or under the '•••' More menu).
        if not _open_invite(page, named_connect, _TOP, settings):
            # No Connect anywhere → Follow-only / 'connect needs their email' /
            # restricted profile. Not a bug — signal so the engine routes to InMail.
            logger.warning("connect: no Connect for %s (Follow-only / restricted). Saved li_debug_no_connect.*",
                           lead_name or profile_url)
            _log_buttons(page, "no_connect")
            return {"ok": False, "state": "NOT_CONNECTED", "not_connectable": True,
                    "confidence": ps["confidence"], "member_id": member_id, "identity": identity,
                    "detail": "profile does not offer Connect (Follow-only/restricted)"}
        logger.info("connect: opened the invite dialog for %s", lead_name or profile_url)
        human_pause(settings, lo=2, hi=5)
        # Wait for the invite dialog (Add a note / Send) to render.
        with contextlib.suppress(Exception):
            page.wait_for_selector(
                "div[role='dialog'] button[aria-label*='Send' i], "
                "button[aria-label='Add a note'], div[role='dialog']",
                timeout=8000,
            )

        # SPECIFIC out-of-notes upsell phrases ONLY — must not match the ubiquitous
        # nav "Try Premium" button (that false-positive made us skip the note even
        # on Premium accounts that have note quota).
        _UPSELL = [
            ":text('out of free custom notes')",
            ":text('unlimited personalized invites')",
            ":text('monthly custom invites')",
        ]

        _SEND_SELS = [
            "[aria-label*='Send invitation' i]",
            "[aria-label*='Send without a note' i]",
            "[aria-label*='Send now' i]",
            "div[role='dialog'] [aria-label*='Send' i]",
            "div[role='dialog'] button:has-text('Send without a note')",
            "div[role='dialog'] button:has-text('Send')",
            "button:has-text('Send without a note')",
            "[aria-label*='Send' i]",
            "button:has-text('Send')",
        ]

        def _try_send():
            return _click_first_visible(page, _SEND_SELS)

        def _force_click_send():
            """Try to click Send/Send-without-a-note using force=True (bypasses
            actionability — works even if the button is behind an overlay)."""
            for sel in _SEND_SELS:
                with contextlib.suppress(Exception):
                    loc = page.locator(sel)
                    for i in range(min(loc.count(), 4)):
                        el = loc.nth(i)
                        if el.count() > 0:
                            el.click(force=True, timeout=3000)
                            return True
            return False

        def _dismiss_upsell():
            """Close the Premium upsell modal."""
            _click_first_visible(page, [
                "div[role='dialog'] button[aria-label*='Dismiss' i]",
                "div[role='dialog'] button[aria-label*='close' i]",
                "div[role='dialog'] button[aria-label*='Close' i]",
                "button[aria-label*='Dismiss' i]",
                "button:has-text('Got it')",
                "button:has-text('Not now')",
            ])
            human_pause(settings, lo=0.5, hi=1)

        global _notes_exhausted

        with_note = False
        upsell_hit = False

        if _notes_exhausted:
            # We already know notes are exhausted — skip "Add a note" entirely
            # to avoid triggering the upsell which nukes the Connect button.
            logger.info("connect: notes exhausted (prior upsell) — sending WITHOUT a note")
        elif note and _click_first_visible(page, ["[aria-label='Add a note']", "button:has-text('Add a note')"]):
            human_pause(settings, lo=1, hi=2)
            if _fill_first_visible(page, (
                "div[role='dialog'] textarea, textarea[name=message], "
                "div[role='dialog'] [contenteditable=true], #custom-message"
            ), note[:280]):
                with_note = True
                logger.info("connect: added a personalized note")
            elif _any_visible(page, _UPSELL):
                logger.info("connect: out of free custom notes — trying Send without a note")
                _notes_exhausted = True
                upsell_hit = True
                _debug_shot(page, "upsell_moment")
                _log_buttons(page, "upsell_moment")
            else:
                logger.info("connect: note box not found and no upsell — sending WITHOUT a note")

        # Normal send attempt.
        sent = _try_send()

        # If upsell just fired, the normal click may have been blocked by the
        # overlay. Force-click to reach the button behind it.
        if not sent and upsell_hit:
            logger.info("connect: trying force-click Send behind upsell overlay")
            sent = _force_click_send()

        # Dismiss the upsell and try again.
        if not sent and _any_visible(page, _UPSELL):
            _dismiss_upsell()
            sent = _try_send()

        if not sent:
            # The dialog is gone. Close any leftover overlays, re-open Connect,
            # and send immediately (no note).
            logger.info("connect: send not found — reopening invite to send without a note")
            with contextlib.suppress(Exception):
                page.keyboard.press("Escape")
            human_pause(settings, lo=1, hi=2)
            if _open_invite(page, named_connect, _TOP, settings):
                human_pause(settings, lo=1, hi=2)
                if _any_visible(page, _UPSELL):
                    _dismiss_upsell()
                sent = _try_send()

        if not sent and upsell_hit:
            # The upsell nuked the Connect button. Navigate AWAY completely
            # (not reload — LinkedIn tracks the upsell in SPA state) and come back.
            logger.info("connect: upsell nuked Connect — navigating away and back")
            with contextlib.suppress(Exception):
                page.keyboard.press("Escape")
            human_pause(settings, lo=0.5, hi=1)
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            human_pause(settings, lo=2, hi=4)
            page.goto(profile_url, wait_until="domcontentloaded")
            human_pause(settings, lo=2, hi=4)
            with contextlib.suppress(Exception):
                page.evaluate("() => window.scrollTo(0, 0)")
            with contextlib.suppress(Exception):
                page.wait_for_selector(
                    "main button:has-text('Connect'), main button:has-text('Message'), "
                    "main button[aria-label*='More actions' i]",
                    timeout=10000,
                )
            human_pause(settings, lo=1, hi=2)
            if _open_invite(page, named_connect, _TOP, settings):
                human_pause(settings, lo=1, hi=2)
                # This time do NOT click "Add a note" — just Send.
                if _any_visible(page, _UPSELL):
                    _dismiss_upsell()
                sent = _try_send()

        if not sent:
            logger.warning("connect: Send button not found on the invite dialog. Saved li_debug_no_send.*")
            _log_buttons(page, "no_send")
            raise RuntimeError("Send button not found on the invite dialog")
        verified = verify_connect_pending(page)  # confirm it became Pending
        logger.info("connect: invitation SENT (%s) to %s (verified-pending=%s)",
                    "with note" if with_note else "no note", profile_url, verified)
        return {"ok": True, "state": "NOT_CONNECTED", "verified": verified, "with_note": with_note,
                "confidence": ps["confidence"], "member_id": member_id, "identity": identity}
    except Exception as e:  # noqa: BLE001
        logger.exception("connect: EXCEPTION for %s: %s. Saved li_debug_connect_failed.*", profile_url, e)
        _debug_shot(page, "connect_failed")
        return {"ok": False, "detail": str(e)[:200]}


# ── State detection (one detector all actions consult) ───────────────────────
# Centralized signal selectors — when LinkedIn changes its UI, add a fallback
# HERE (one place) rather than editing each flow.
# Tag-agnostic: LinkedIn's componentized UI renders these as <div aria-label=…>
# styled as buttons, NOT <button> — so match by aria-label on ANY element (plus
# a text fallback). Scoped to <main> + visibility-gated by the callers.
_SIG = {
    "connect": [
        "main [aria-label*='invite' i][aria-label*='to connect' i]",
        "main [aria-label*='to connect' i]",
        "main button:has-text('Connect')",
    ],
    "pending": [
        "main [aria-label*='Pending' i]",
        "main [aria-label*='withdraw' i]",
        "main button:has-text('Pending')",
    ],
    "message": ["main [aria-label*='Message' i]", "main button:has-text('Message')"],
}


def _any_visible(page: Any, selectors) -> bool:
    for sel in selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return True
    return False


def _degree(page: Any) -> Optional[str]:
    """Best-effort connection-distance badge ('1st' | '2nd' | '3rd') from the TOP
    CARD only. Matches the '· 1st' badge form (with the leading middot) inside the
    first ~1200 chars of <main> — so body text like 'Ranked 1st in Sales' (no
    middot, deep in the page) can't be mistaken for a degree."""
    with contextlib.suppress(Exception):
        return page.evaluate(
            "() => { const el = document.querySelector('main'); if(!el) return null;"
            " const t = (el.innerText||'').slice(0, 1200);"
            " const m = t.match(/[·•]\\s*(1st|2nd|3rd)\\b/); return m ? m[1] : null; }"
        )
    return None


def detect_profile_state(page: Any) -> Dict[str, Any]:
    """Read the profile ONCE → {state, confidence, signals}. The shared gate every
    action consults. Confidence = how many signals agree:
      Pending button                → PENDING 100
      '1st' degree badge            → CONNECTED 100
      Connect button                → NOT_CONNECTED 100
      '2nd'/'3rd' badge (no connect)→ NOT_CONNECTED 90
      Message only (no degree)      → CONNECTED 60 (ambiguous)
      nothing recognized            → UNKNOWN 0
    """
    pending = _any_visible(page, _SIG["pending"])
    connect = _any_visible(page, _SIG["connect"])
    message = _any_visible(page, _SIG["message"])
    degree = _degree(page)
    signals = []
    if pending:
        signals.append("pending_button")
    if connect:
        signals.append("connect_button")
    if message:
        signals.append("message_button")
    if degree:
        signals.append(f"degree_{degree}")

    if pending:
        return {"state": "PENDING", "confidence": 100, "signals": signals}
    if degree == "1st":
        return {"state": "CONNECTED", "confidence": 100, "signals": signals}
    if connect:
        return {"state": "NOT_CONNECTED", "confidence": 100, "signals": signals}
    if degree in ("2nd", "3rd"):
        return {"state": "NOT_CONNECTED", "confidence": 90, "signals": signals}
    if message:
        return {"state": "CONNECTED", "confidence": 60, "signals": signals}
    return {"state": "UNKNOWN", "confidence": 0, "signals": signals}


def read_profile_state(page: Any, profile_url: str, settings: Any) -> Dict[str, Any]:
    """Open the profile, scroll to the top card, and return
    {state, confidence, signals, identity}. UNKNOWN + a screenshot on failure."""
    out: Dict[str, Any] = {"state": "UNKNOWN", "confidence": 0, "signals": [], "identity": {"url": None, "name": None}}
    try:
        open_profile(page, profile_url, settings)
        with contextlib.suppress(Exception):
            page.evaluate("() => window.scrollTo(0, 0)")
        out.update(detect_profile_state(page))
        out["identity"] = capture_profile_identity(page)
        logger.info("check: profile %s → state=%s confidence=%s signals=%s",
                    profile_url, out["state"], out["confidence"], out["signals"])
    except Exception:  # noqa: BLE001
        logger.warning("check: read_profile_state FAILED for %s — returning UNKNOWN. Saved li_debug_state_unknown.*",
                       profile_url, exc_info=True)
        _debug_shot(page, "state_unknown")
    return out


def detect_acceptance(page: Any, profile_url: str, settings: Any) -> str:
    """Back-compat wrapper → 'accepted' | 'pending' | 'declined' | 'unknown'.
    NOT_CONNECTED maps to 'declined' (the invite is gone), never 'pending'."""
    ps = read_profile_state(page, profile_url, settings)
    if ps["confidence"] < 50:
        return "unknown"
    return {"CONNECTED": "accepted", "PENDING": "pending", "NOT_CONNECTED": "declined"}.get(ps["state"], "unknown")


# ── Post-action verification (confirm the click actually worked) ─────────────
def verify_connect_pending(page: Any) -> bool:
    """After sending a connect: the top card should flip to Pending/Withdraw.
    LinkedIn can take 1-5s (React refresh) — poll up to 5× 1s instead of one wait."""
    for _ in range(5):
        with contextlib.suppress(Exception):
            if detect_profile_state(page)["state"] == "PENDING" or _any_visible(page, _SIG["pending"]):
                return True
        with contextlib.suppress(Exception):
            page.wait_for_timeout(1000)
    return False


def verify_message_in_thread(page: Any, body: str) -> bool:
    """After sending a message: confirm it landed. Two independent signals —
    either counts as sent (a hit on ONE is enough):

      A) The post-Send URL is /messaging/thread/… — LinkedIn redirects to the
         real thread as soon as it accepts the message. If we're not on
         /compose/ anymore, the compose form is gone and the message was sent.
      B) The body appears in the thread's rendered messages. Depends on
         LinkedIn rendering the new event within the poll window."""
    # Signal A: URL check — no timing dependency, no selector fragility.
    with contextlib.suppress(Exception):
        url = (page.url or "").lower()
        if "/messaging/thread/" in url:
            return True
    # Signal B: DOM check — waits for LinkedIn to render the new event.
    snippet = (body or "").strip()[:40]
    if not snippet:
        return False
    with contextlib.suppress(Exception):
        return page.locator(
            "li.msg-s-message-list__event, div.msg-s-event-listitem"
        ).filter(has_text=snippet).count() > 0
    return False


def verify_inmail_success(page: Any) -> bool:
    """After sending an InMail: a success toast / confirmation appears."""
    with contextlib.suppress(Exception):
        page.wait_for_timeout(1500)
        return _any_visible(page, [
            "div.artdeco-toast-item:has-text('sent')",
            ":text('InMail sent')",
            ":text('Your message')",
        ])
    return False


def _detect_message_upsell(page: Any) -> Dict[str, Any]:
    """Inspect the profile's Message element(s) and return
        {upsell: bool, compose_url: str|None, reason: str, href: str, label: str}
    A "compose_url" is present whenever LinkedIn attaches its own
    `/messaging/compose/?profileUrn=...&recipient=...` URL to the upsell — the
    ideal fallback path since the recipient is already resolved (no typeahead,
    no name matching needed).

    Detection rules:
      1. aria-label contains both "message" and "premium" (English UI) → upsell
      2. Message-adjacent <a href='/messaging/compose/...'> → upsell w/ compose_url
      3. Message-adjacent <a> pointing at /premium/ or upgrade → upsell (no compose)
    """
    try:
        result = page.evaluate("""() => {
            const els = document.querySelectorAll('main [aria-label]');
            // We may see multiple compose URLs on one profile:
            //   1. "Say hello" congratulations button — a compose URL WITHOUT
            //      recipient=/profileUrn= (e.g. ?body=Congrats+on+the+new+job)
            //   2. The real Message button — a compose URL WITH recipient=
            //      and profileUrn= for the actual person.
            // The real Message button is what we want. Prefer the URL with
            // recipient=, only fall back to the recipient-less one if that's all
            // we saw (which usually means the compose won't work anyway).
            let composeUrlWithRecipient = null;
            let composeUrlAny = null;
            let out = {upsell: false};
            for (const el of els) {
                const al = (el.getAttribute('aria-label') || '').toLowerCase();
                if (!al.includes('message')) continue;
                const href = el.getAttribute('href') || '';
                if (href.includes('/messaging/compose/')) {
                    if (!composeUrlAny) composeUrlAny = href;
                    if (!composeUrlWithRecipient &&
                        href.includes('recipient=') && href.includes('profileUrn=')) {
                        composeUrlWithRecipient = href;
                    }
                }
                if (al.includes('premium')) {
                    // Prefer the FIRST upsell that carries a compose URL with a
                    // recipient — otherwise we'll still capture the upsell but
                    // may end up with a recipient-less URL.
                    if (!out.upsell || (href.includes('recipient=') && href.includes('profileUrn='))) {
                        out = {upsell: true, reason: 'aria-label-en',
                               tag: el.tagName, label: el.getAttribute('aria-label'),
                               href: href};
                    }
                    if (out.upsell && out.href.includes('recipient=')) break;
                    continue;
                }
                if (el.tagName === 'A' &&
                    (href.includes('/premium/') || href.includes('upgrade'))) {
                    out = {upsell: true, reason: 'anchor-to-premium',
                           tag: el.tagName, label: el.getAttribute('aria-label'),
                           href: href};
                    break;
                }
            }
            out.compose_url = composeUrlWithRecipient || composeUrlAny;
            return out;
        }""") or {}
        upsell = bool(result.get("upsell"))
        compose_url = result.get("compose_url") or None
        if upsell:
            logger.info(
                "message: Premium upsell detected — reason=%s label=%r compose_url=%s",
                result.get("reason", "?"), result.get("label", "?"),
                (compose_url or "")[:120],
            )
        else:
            logger.info(
                "message: no Premium upsell — standard Message button available "
                "(compose_url=%s)", (compose_url or "")[:120] or "none",
            )
        return {"upsell": upsell, "compose_url": compose_url,
                "reason": result.get("reason"), "href": result.get("href", ""),
                "label": result.get("label", "")}
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "message: upsell detection FAILED with error=%s — assuming upsell "
            "so we take the safer fallback path", e,
        )
        return {"upsell": True, "compose_url": None, "reason": "eval-error"}


# Back-compat shim so any caller still checking a bool keeps working.
def _is_message_premium_upsell(page: Any) -> bool:
    return _detect_message_upsell(page).get("upsell", False)


def _clean_display_name(name: str) -> str:
    """Strip LinkedIn H1 decorations so the name works in the To-field typeahead.

    LinkedIn's profile H1 often reads e.g. "Jane Doe · 1st" or "Jane Doe (She/Her) · 2nd".
    Typing the raw H1 into the typeahead misses because the trailing " · 1st" isn't
    part of the actual name index. We keep the leading name only."""
    if not name:
        return name
    # Cut at the first middle-dot / bullet separator LinkedIn injects for the degree.
    for sep in (" · ", " • ", " – ", " — "):
        if sep in name:
            name = name.split(sep, 1)[0]
    # Drop pronoun parentheticals like "(She/Her)" — the typeahead ignores them anyway.
    import re
    name = re.sub(r"\s*\([^)]{1,32}\)\s*", " ", name).strip()
    return name


def _send_message_via_compose_url(page: Any, compose_url: str, body: str, settings: Any) -> Dict[str, Any]:
    """Navigate to LinkedIn's own `/messaging/compose/?profileUrn=...&recipient=...`
    URL. LinkedIn opens the composer with the recipient PRE-RESOLVED — no
    typeahead, no name-matching, no chance of picking the wrong person.

    This is the preferred fallback for free-account "Message with Premium"
    upsells whenever the compose URL is present in the upsell's href."""
    # Some upsell hrefs are relative (start with '/'). Absolute-ify.
    if compose_url.startswith("/"):
        compose_url = "https://www.linkedin.com" + compose_url
    logger.info("message[compose-url]: START — url=%s body_len=%d",
                compose_url[:160], len(body or ""))

    # Step 1: Navigate to the compose URL.
    logger.info("message[compose-url]: step 1/4 — navigating to compose URL")
    page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
    human_pause(settings, lo=3, hi=6)
    logger.info("message[compose-url]: page loaded — url=%s", page.url)

    # Step 2: Wait for the compose box.
    logger.info("message[compose-url]: step 2/4 — waiting for compose box")
    compose_selectors = [
        "div.msg-form__contenteditable[contenteditable=true]",
        "div[role='textbox'][contenteditable=true]",
        "div[contenteditable=true]",
    ]
    with contextlib.suppress(Exception):
        page.wait_for_selector(", ".join(compose_selectors), state="visible", timeout=15000)

    # Step 3: Fill the body — LinkedIn pre-fills a default via the `body=` query
    # param on some hrefs; clear it first so our text replaces it (page.fill
    # already replaces content, but role-based fill is more reliable).
    logger.info("message[compose-url]: step 3/4 — filling body (%d chars)", len(body or ""))
    filled = _fill_by_role(page, "textbox", "Write a message", body)
    if not filled:
        for sel in compose_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    loc.fill(body, timeout=5000)
                    filled = True
                    logger.info("message[compose-url]: body filled via selector %r", sel)
                    break
            except Exception:  # noqa: BLE001
                continue
    if not filled:
        logger.error(
            "message[compose-url]: FAILED at step 3 — compose box not found. "
            "Tried selectors: %s. Current url=%s",
            compose_selectors, page.url,
        )
        _log_buttons(page, "compose_url_no_compose")
        _debug_shot(page, "compose_url_no_compose")
        return {"ok": False, "detail": "compose-url: compose box not found"}

    human_pause(settings, lo=1, hi=3)

    # Step 4: Send.
    logger.info("message[compose-url]: step 4/4 — clicking Send")
    send_selectors = [
        # Send button. Icon-only <button type="submit"> with an SVG child —
        # no text, no aria-label. role="button" name="Send" DOES NOT MATCH.
        # Selector priority (per Playwright docs for icon-only buttons):
        #   1. LinkedIn's own SVG test ID  → most stable
        #   2. Current class name (msg-form__send-btn — renamed 2026-08-05)
        #   3. Old class (msg-form__send-button) — backup
        #   4. Form-scoped type=submit    → semantic HTML fallback
        "button:has(svg[data-test-icon='send-privately-small'])",
        "button:has(svg[data-test-icon*='send'])",
        "button.msg-form__send-btn",
        "button.msg-form__send-button",
        "form.msg-form button[type=submit]",
        "div.msg-form button[type=submit]",
        "[aria-label*='Send' i]",
        "button:has-text('Send')",
    ]
    clicked_send = (
        _click_by_role(page, "button", "Send")
        or _click_first_visible(page, send_selectors)
    )
    if not clicked_send:
        logger.error(
            "message[compose-url]: FAILED at step 4 — Send button not found. "
            "Tried role='button' name='Send' and selectors: %s. Current url=%s",
            send_selectors, page.url,
        )
        _log_buttons(page, "compose_url_no_send")
        _debug_shot(page, "compose_url_no_send")
        return {"ok": False, "detail": "compose-url: Send button not found"}

    # Verify.
    human_pause(settings, lo=2, hi=4)
    verified = verify_message_in_thread(page, body)
    if not verified:
        logger.warning("message[compose-url]: Send clicked but body NOT found in thread — "
                       "may still have sent; LinkedIn sometimes lags rendering.")
        _debug_shot(page, "compose_url_send_unverified")
        return {"ok": True, "verified": False}
    logger.info("message[compose-url]: SUCCESS — DM sent + verified")
    return {"ok": True, "verified": True}


def _send_message_via_inbox(page: Any, recipient_name: str, body: str, settings: Any) -> Dict[str, Any]:
    """Fallback for free accounts: compose a message via LinkedIn's messaging
    page instead of the profile Message button (which is a Premium upsell).
    Works for 1st-degree connections only.

    Flow: /messaging/thread/new/ → type name in To field → wait for typeahead →
    click the suggestion WHOSE VISIBLE TEXT contains the name → type body → Send
    → verify body is in the resulting thread before returning ok."""
    clean_name = _clean_display_name(recipient_name)
    logger.info(
        "message[inbox-fallback]: START — recipient=%r (cleaned=%r) body_len=%d",
        recipient_name, clean_name, len(body or ""),
    )

    # Step 1: Navigate to new-message compose page.
    logger.info("message[inbox-fallback]: step 1/6 — navigating to /messaging/thread/new/")
    page.goto("https://www.linkedin.com/messaging/thread/new/", wait_until="domcontentloaded", timeout=30000)
    human_pause(settings, lo=3, hi=6)
    logger.info("message[inbox-fallback]: page loaded — url=%s", page.url)

    # Step 2: Type the recipient name in the "To" search field. Try role-based
    # locators first (accessibility tree — survives DOM churn), then placeholder,
    # then raw CSS. Wait for any input to appear before touching them so early
    # hits don't silently miss.
    logger.info("message[inbox-fallback]: step 2/6 — filling recipient field with %r", clean_name)
    to_selectors = [
        "input[name='search-terms']",
        "input[placeholder*='name' i]",
        "input[aria-label*='recipient' i]",
        "input[aria-label*='Type a name' i]",
        "input[role='combobox']",
    ]
    with contextlib.suppress(Exception):
        page.wait_for_selector(", ".join(to_selectors), state="visible", timeout=10000)
    to_filled = (
        _fill_by_role(page, "combobox", "Type a name or multiple names", clean_name)
        or _fill_by_role(page, "combobox", "Type a name", clean_name)
        or _fill_by_placeholder(page, "Type a name or multiple names", clean_name)
    )
    if not to_filled:
        for sel in to_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    loc.fill(clean_name, timeout=5000)
                    to_filled = True
                    logger.info("message[inbox-fallback]: recipient filled via selector %r", sel)
                    break
            except Exception:  # noqa: BLE001
                continue
    if not to_filled:
        logger.error(
            "message[inbox-fallback]: FAILED at step 2 — recipient field not found. "
            "Tried selectors: %s. Current url=%s",
            to_selectors, page.url,
        )
        _log_buttons(page, "inbox_no_to_field")
        _debug_shot(page, "inbox_fallback_failed")
        return {"ok": False, "detail": "messaging page: recipient field not found"}

    human_pause(settings, lo=2, hi=4)

    # Step 3: Wait for the typeahead panel, then click the SUGGESTION MATCHING
    # THE NAME. Role-based first (get_by_role('option', name=<clean_name>) hits
    # the accessibility tree and only inside the listbox), then scoped CSS
    # fallbacks. NEVER use unscoped `li:has-text('John')` — the sidebar also
    # renders <li>s and a plain match would click the wrong element.
    logger.info("message[inbox-fallback]: step 3/6 — waiting for typeahead panel")
    typeahead_container = (
        "div.msg-connections-typeahead__search-results, "
        "ul[role='listbox'], "
        "div[role='listbox']"
    )
    with contextlib.suppress(Exception):
        page.wait_for_selector(typeahead_container, state="visible", timeout=10000)
    first = clean_name.split()[0] if clean_name else ""
    # Try role-based (scoped by definition to the listbox) first.
    clicked = (
        _click_by_role(page, "option", clean_name)
        or (_click_by_role(page, "option", first) if first else False)
    )
    if not clicked:
        # CSS fallbacks — all scoped INSIDE the typeahead panel.
        suggestion_selectors = [
            f"div.msg-connections-typeahead__search-results li:has-text('{clean_name}')",
            f"ul[role='listbox'] li:has-text('{clean_name}')",
            f"div[role='listbox'] [role='option']:has-text('{clean_name}')",
            f"div.msg-connections-typeahead__search-results li:has-text('{first}')",
            f"ul[role='listbox'] li:has-text('{first}')",
            "div.msg-connections-typeahead__search-results li:first-child",
            "ul[role='listbox'] li:first-child",
            "div[role='listbox'] [role='option']:first-child",
        ]
        clicked = _click_first_visible(page, suggestion_selectors)
    if not clicked:
        logger.error(
            "message[inbox-fallback]: FAILED at step 3 — no typeahead suggestion for %r. "
            "The recipient may not be a 1st-degree connection, or the name differs from "
            "what LinkedIn indexes.",
            clean_name,
        )
        _log_buttons(page, "inbox_no_suggestion")
        _debug_shot(page, "inbox_no_suggestion")
        return {"ok": False, "detail": f"messaging page: no suggestion for {clean_name}"}
    logger.info("message[inbox-fallback]: suggestion clicked for %r", clean_name)

    human_pause(settings, lo=2, hi=4)

    # Step 4: Wait for the compose form to appear, then type body.
    # Role-based (textbox) first, then CSS fallback.
    logger.info("message[inbox-fallback]: step 4/6 — waiting for compose box")
    compose_selectors = [
        "div.msg-form__contenteditable[contenteditable=true]",
        "div[role='textbox'][contenteditable=true]",
        "div[contenteditable=true]",
    ]
    with contextlib.suppress(Exception):
        page.wait_for_selector(", ".join(compose_selectors), state="visible", timeout=10000)
    logger.info("message[inbox-fallback]: typing message body (%d chars)", len(body or ""))
    filled = _fill_by_role(page, "textbox", "Write a message", body)
    if not filled:
        for sel in compose_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    loc.fill(body, timeout=5000)
                    filled = True
                    logger.info("message[inbox-fallback]: body filled via selector %r", sel)
                    break
            except Exception:  # noqa: BLE001
                continue
    if not filled:
        logger.error(
            "message[inbox-fallback]: FAILED at step 4 — compose box not found. "
            "Tried selectors: %s. Current url=%s",
            compose_selectors, page.url,
        )
        _log_buttons(page, "inbox_no_compose")
        _debug_shot(page, "inbox_no_compose")
        return {"ok": False, "detail": "messaging page: compose box not found"}

    human_pause(settings, lo=1, hi=3)

    # Step 5: Send. Role-based first, then CSS fallback.
    logger.info("message[inbox-fallback]: step 5/6 — clicking Send")
    send_selectors = [
        # Send button. Icon-only <button type="submit"> with an SVG child —
        # no text, no aria-label. role="button" name="Send" DOES NOT MATCH.
        # Selector priority (per Playwright docs for icon-only buttons):
        #   1. LinkedIn's own SVG test ID  → most stable
        #   2. Current class name (msg-form__send-btn — renamed 2026-08-05)
        #   3. Old class (msg-form__send-button) — backup
        #   4. Form-scoped type=submit    → semantic HTML fallback
        "button:has(svg[data-test-icon='send-privately-small'])",
        "button:has(svg[data-test-icon*='send'])",
        "button.msg-form__send-btn",
        "button.msg-form__send-button",
        "form.msg-form button[type=submit]",
        "div.msg-form button[type=submit]",
        "[aria-label*='Send' i]",
        "button:has-text('Send')",
    ]
    clicked_send = (
        _click_by_role(page, "button", "Send")
        or _click_first_visible(page, send_selectors)
    )
    if not clicked_send:
        logger.error(
            "message[inbox-fallback]: FAILED at step 5 — Send button not found. "
            "Tried role='button' name='Send' and selectors: %s. Current url=%s",
            send_selectors, page.url,
        )
        _log_buttons(page, "inbox_no_send")
        _debug_shot(page, "inbox_no_send")
        return {"ok": False, "detail": "messaging page: Send button not found"}

    # Step 6: Verify the message actually landed in the thread. Without this,
    # a broken selector or a network hiccup returns ok:True with no message sent.
    logger.info("message[inbox-fallback]: step 6/6 — verifying message appears in thread")
    human_pause(settings, lo=2, hi=4)
    verified = verify_message_in_thread(page, body)
    if not verified:
        logger.warning(
            "message[inbox-fallback]: Send clicked but body NOT found in thread within timeout "
            "(may still have sent; LinkedIn sometimes lags rendering). Returning ok with verified=false.",
        )
        _debug_shot(page, "inbox_send_unverified")
        return {"ok": True, "verified": False}

    logger.info(
        "message[inbox-fallback]: SUCCESS — DM sent + verified in thread for %r", clean_name,
    )
    return {"ok": True, "verified": True}


def do_send_message(page: Any, profile_url: str, body: str, settings: Any) -> Dict[str, Any]:
    """Open the 1st-degree message composer and send `body`.  Tag-agnostic (the
    Message button + Send are <div>s in the new UI, like Connect).

    Free-account fallback: if the profile shows "Message with Premium" (upsell
    <a> instead of a real button), we navigate to linkedin.com/messaging/ and
    compose from there — works for 1st-degree connections without Premium."""
    try:
        logger.info("message: START — profile=%s body_len=%d", profile_url, len(body or ""))
        open_profile(page, profile_url, settings)
        with contextlib.suppress(Exception):
            page.evaluate("() => window.scrollTo(0, 0)")
        logger.info("message: profile loaded — url=%s", page.url)

        # ── Free-account check: is the Message element a Premium upsell? ──
        upsell = _detect_message_upsell(page)
        if upsell.get("upsell"):
            # Preferred fallback: the upsell href carries LinkedIn's own
            # `/messaging/compose/?profileUrn=...&recipient=...` URL — navigate
            # to it, LinkedIn resolves the recipient for us (no typeahead).
            compose_url = upsell.get("compose_url")
            if compose_url:
                logger.info("message: using compose-URL fallback (recipient pre-resolved)")
                return _send_message_via_compose_url(page, compose_url, body, settings)

            # No compose URL — fall back to typing the recipient name into the
            # messaging page typeahead.
            identity = capture_profile_identity(page)
            raw_name = (identity.get("name") or "").strip()
            clean_name = _clean_display_name(raw_name)
            logger.info(
                "message: profile identity — name=%r (cleaned=%r) url=%s",
                raw_name, clean_name, identity.get("url"),
            )
            if not clean_name:
                logger.error(
                    "message: FAILED — Premium upsell without compose URL and could not "
                    "read profile name from H1 on %s. Cannot use messaging-page fallback.",
                    profile_url,
                )
                _debug_shot(page, "message_no_name")
                return {"ok": False, "detail": "Premium upsell and profile name not readable"}
            return _send_message_via_inbox(page, clean_name, body, settings)

        # ── Standard path: click the Message button on the profile ──
        # Prefer Playwright's role-based locator (accessibility tree, survives
        # DOM churn), fall back to CSS selectors.
        logger.info("message: standard path — clicking Message button on profile")
        clicked = (
            _click_by_role(page, "button", "Message")
            or _click_first_visible(page, [
                "main [aria-label*='Message' i]",
                "main button:has-text('Message')",
            ])
        )
        if not clicked:
            logger.error(
                "message: FAILED — Message button not found on %s (and no Premium upsell either). "
                "The profile may not be a 1st-degree connection or LinkedIn changed the UI.",
                profile_url,
            )
            _log_buttons(page, "no_message_button")
            return {"ok": False, "detail": "Message button not found"}
        logger.info("message: Message button clicked — waiting for compose box")
        human_pause(settings, lo=2, hi=5)

        # Wait for the compose overlay to actually render (LinkedIn animates it in).
        with contextlib.suppress(Exception):
            page.wait_for_selector(
                "div.msg-form__contenteditable[contenteditable=true], div[role='textbox'][contenteditable=true]",
                state="visible", timeout=10000,
            )
        # Fill the compose box. Try role-based first (textbox with any name),
        # then fall back to CSS.
        filled = _fill_by_role(page, "textbox", "", body)
        if not filled:
            for sel in ("div.msg-form__contenteditable[contenteditable=true]",
                        "div[role='textbox'][contenteditable=true]",
                        "div[contenteditable=true]"):
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible():
                        loc.fill(body, timeout=5000)
                        filled = True
                        logger.info("message: body filled via selector %r", sel)
                        break
                except Exception:  # noqa: BLE001
                    continue
        if not filled:
            logger.error(
                "message: FAILED — compose box not found on %s after clicking Message button. "
                "The overlay may not have opened. Current url=%s",
                profile_url, page.url,
            )
            _log_buttons(page, "no_msg_box")
            _debug_shot(page, "message_failed")
            return {"ok": False, "detail": "message composer not found"}

        human_pause(settings, lo=1, hi=3)

        # Send. Prefer role-based, fall back to CSS.
        logger.info("message: clicking Send button")
        clicked_send = (
            _click_by_role(page, "button", "Send")
            or _click_first_visible(page, [
                # Send button. Icon-only <button type="submit"> with an SVG child —
        # no text, no aria-label. role="button" name="Send" DOES NOT MATCH.
        # Selector priority (per Playwright docs for icon-only buttons):
        #   1. LinkedIn's own SVG test ID  → most stable
        #   2. Current class name (msg-form__send-btn — renamed 2026-08-05)
        #   3. Old class (msg-form__send-button) — backup
        #   4. Form-scoped type=submit    → semantic HTML fallback
        "button:has(svg[data-test-icon='send-privately-small'])",
        "button:has(svg[data-test-icon*='send'])",
        "button.msg-form__send-btn",
        "button.msg-form__send-button",
        "form.msg-form button[type=submit]",
        "div.msg-form button[type=submit]",
                "div[role='dialog'] [aria-label*='Send' i]",
                "[aria-label*='Send' i]",
                "button:has-text('Send')",
            ])
        )
        if not clicked_send:
            logger.error(
                "message: FAILED — Send button not found on %s after filling compose box. "
                "Current url=%s",
                profile_url, page.url,
            )
            _log_buttons(page, "no_msg_send")
            _debug_shot(page, "message_failed")
            return {"ok": False, "detail": "message Send button not found"}

        logger.info("message: SUCCESS — DM sent to %s via profile page", profile_url)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "message: EXCEPTION — profile=%s error=%s. Debug screenshots saved as li_debug_message_failed.*",
            profile_url, e,
        )
        _debug_shot(page, "message_failed")
        return {"ok": False, "detail": str(e)[:200]}


def _detect_inmail_upsell(page: Any) -> bool:
    """True if LinkedIn is showing an out-of-credits / no-Premium upsell
    instead of a working InMail composer.

    Two-tier detection so we don't false-positive on the credit counter:
      1. POSITIVE signals (composer IS available):
         - "Premium InMail composer" label
         - "Use N of M InMail credit(s)" credit counter (with M > 0)
      2. NEGATIVE signals (composer NOT available):
         - "0 InMail credit(s)" / "You have 0 InMail"
         - "out of InMail credits"
         - "Upgrade to send an InMail" / "Upgrade to send message"
         - "Reactivate to send InMail" / "unlock InMail credits"

    If a positive signal fires, we override negative detection: the counter
    text "Use 1 of 9 InMail credits" contains the substring "InMail credit"
    but obviously means credits ARE available.

    Returns False on eval error so we err on the side of TRYING the send
    rather than silently skipping a good composer."""
    try:
        result = page.evaluate("""() => {
            const t = document.body.innerText || '';
            // Positive signals — composer is available, credits > 0.
            const positive = (
                /Premium InMail composer/i.test(t) ||
                /Use \\d+ of [1-9]\\d* InMail credits?/i.test(t)
            );
            if (positive) return {upsell: false, reason: 'composer-visible'};
            // Negative signals — actual upsell.
            const negative = (
                /You have 0 InMail/i.test(t) ||
                /0 InMail credits? (?:left|remaining|available)/i.test(t) ||
                /out of InMail credits?/i.test(t) ||
                /Upgrade to send (?:an? )?InMail/i.test(t) ||
                /Upgrade to send a message/i.test(t) ||
                /Reactivate to send InMail/i.test(t) ||
                /Unlock InMail credits?/i.test(t)
            );
            return {upsell: negative, reason: negative ? 'upsell-visible' : 'no-signal'};
        }""") or {}
        return bool(result.get("upsell"))
    except Exception:  # noqa: BLE001
        return False


def _composer_recipient_matches(page: Any, profile_url: str) -> bool:
    """SAFETY: verify the open composer is addressed to the intended profile.

    Waits for the compose overlay to render (subject/body field visible),
    then SCOPES the name lookup INSIDE the overlay container so unrelated
    page elements (ads, other dialogs, the profile H1) can't spoof the check.

    Returns True if positively-confirmed OR if the overlay didn't render a
    readable recipient (we trust the click; verify_message_in_thread post-send
    catches any misfire).  Only returns False on POSITIVE MISMATCH — a name
    found INSIDE the compose overlay that doesn't match the recipient slug.
    """
    recipient_slug = _recipient_slug(profile_url).lower()
    if not recipient_slug:
        return True
    slug_tokens = [t for t in recipient_slug.split("-") if t and not t.isdigit()]
    if not slug_tokens:
        return True

    # 1. Give the compose overlay time to fully render its recipient card.
    #    The overlay is one of several classes depending on LinkedIn variant:
    #      msg-overlay-bubble-* (Premium InMail bubble)
    #      msg-form / msg-compose-form (older DM compose)
    #      div[role='dialog'] with msg-* descendants
    with contextlib.suppress(Exception):
        page.wait_for_selector(
            "div[class*='msg-overlay-bubble'], div.msg-form, "
            "div.msg-compose-form, div[role='dialog'] [class*='msg-']",
            state="visible", timeout=8000,
        )

    # 2. Read names ONLY from inside the compose overlay container.  This
    #    excludes ad dialogs, profile H1, sidebar cards, etc. — anything
    #    outside the compose can't spoof a positive/negative match.
    try:
        names = page.evaluate("""() => {
            const containers = document.querySelectorAll(
                "div[class*='msg-overlay-bubble'], " +
                "div.msg-form, div.msg-compose-form, " +
                "form.msg-compose-form, " +
                "div[role='dialog'] [class*='msg-']"
            );
            const out = new Set();
            for (const container of containers) {
                // Recipient card / header text within this compose only.
                const inners = container.querySelectorAll(
                    "[class*='msg-overlay-bubble-header'] h2, " +
                    "[class*='msg-overlay-bubble-header'] span, " +
                    "[class*='recipient-info'], [class*='participant'], " +
                    "[class*='typeahead__pill'], " +
                    "[data-anonymize='person-name'], " +
                    "h2"
                );
                inners.forEach(el => {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t) out.add(t);
                });
            }
            return Array.from(out);
        }""") or []
    except Exception:  # noqa: BLE001
        return True

    if not names:
        # Compose overlay didn't expose a readable recipient — trust the
        # click (Patrick's specific button was targeted).  Post-send
        # verification via /messaging/thread/ URL still catches misfires.
        logger.info(
            "inmail: composer recipient card not readable — trusting the "
            "targeted button click (post-send URL verify still applies)",
        )
        return True

    for n in names:
        low = n.lower()
        if all(tok in low for tok in slug_tokens):
            logger.info(
                "inmail: composer recipient OK — displayed %r matches slug %r",
                n, recipient_slug,
            )
            return True
    logger.warning(
        "inmail: composer recipient MISMATCH — expected slug %r, but overlay "
        "shows names %s", recipient_slug, names[:5],
    )
    return False


def _ensure_recipient_selected(page: Any, profile_url: str, settings: Any) -> None:
    """After clicking Message on a 2nd/3rd-degree profile the New Message
    dialog can open with an EMPTY recipient field (LinkedIn's context race:
    the click fires before the profile is bound to the compose form).  This
    helper detects that state and drives the typeahead: type the recipient's
    name → wait for suggestions → click the FIRST option whose visible text
    contains the name → LinkedIn binds the recipient → subject + Send button
    render.

    No-op when the compose already has a recipient (1st-degree overlays skip
    the To field entirely) — cheap enough to always run."""
    # Only act if a visible, empty To/typeahead input is present.
    to_selectors = [
        "input[placeholder*='Type a name' i]",
        "input[placeholder*='name or multiple names' i]",
        "input[name='search-terms']",
        "input[aria-label*='recipient' i]",
        "input[aria-label*='To' i]",
        "input[role='combobox']",
    ]
    to_field = None
    for sel in to_selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500) and (loc.input_value() or "").strip() == "":
                to_field = loc
                logger.info(
                    "inmail[typeahead]: empty To field detected via %r — will resolve via typeahead",
                    sel,
                )
                break
    if to_field is None:
        # No empty To field → recipient already bound (1st-degree convo overlay).
        return

    # Recipient name from the profile H1 (cheaper + more reliable than the URL slug).
    identity = capture_profile_identity(page)
    raw_name = (identity.get("name") or "").strip()
    clean_name = _clean_display_name(raw_name)
    if not clean_name:
        # Fall back to the URL slug's first token (best-effort).
        slug = _recipient_slug(profile_url).replace("-", " ").strip()
        clean_name = slug.split()[0].title() if slug else ""
        logger.warning(
            "inmail[typeahead]: no H1 name — falling back to slug=%r", clean_name,
        )
    if not clean_name:
        logger.warning(
            "inmail[typeahead]: could not resolve a name for %s — leaving To empty; "
            "the send will fail at Step 5 and burn no credit", profile_url,
        )
        return

    logger.info("inmail[typeahead]: typing recipient=%r into To field", clean_name)
    with contextlib.suppress(Exception):
        to_field.fill(clean_name, timeout=5000)
    human_pause(settings, lo=2, hi=4)

    # Wait for typeahead suggestions to appear, then click the first one whose
    # visible text matches the name.  Scoped to the typeahead panel so we can't
    # click a sidebar match by accident (learned the hard way for DMs).
    typeahead_container = (
        "div.msg-connections-typeahead__search-results, "
        "ul[role='listbox'], div[role='listbox']"
    )
    with contextlib.suppress(Exception):
        page.wait_for_selector(typeahead_container, state="visible", timeout=8000)
    first = clean_name.split()[0] if clean_name else ""
    picked = (
        _click_by_role(page, "option", clean_name)
        or (_click_by_role(page, "option", first) if first else False)
        or _click_first_visible(page, [
            f"div.msg-connections-typeahead__search-results li:has-text('{clean_name}')",
            f"ul[role='listbox'] li:has-text('{clean_name}')",
            f"div[role='listbox'] [role='option']:has-text('{clean_name}')",
            f"div.msg-connections-typeahead__search-results li:has-text('{first}')",
            f"ul[role='listbox'] li:has-text('{first}')",
        ])
    )
    if not picked:
        logger.warning(
            "inmail[typeahead]: NO SUGGESTION matched %r — Send will not fire (no credit burn). "
            "The prospect may be out of reach for this account.",
            clean_name,
        )
        _log_buttons(page, "inmail_no_typeahead")
        _debug_shot(page, "inmail_no_typeahead")
        return
    logger.info("inmail[typeahead]: recipient %r selected — proceeding to subject + body", clean_name)
    # Give LinkedIn a moment to render the InMail-specific subject field.
    human_pause(settings, lo=2, hi=3)


def do_send_inmail(page: Any, profile_url: str, subject: str, body: str, settings: Any) -> Dict[str, Any]:
    """Send an InMail (subject + body) to a non-connection on a Premium /
    Sales-Navigator account.  Returns {ok, no_credit?, verified?, detail?}.

    Mirrors do_send_message's compose-URL approach: navigate directly to
    /messaging/compose/?profileUrn=… when the profile exposes it, otherwise
    fall back to clicking the profile Message button.  Both paths use
    Playwright role-based locators for compose box + Send, and verify the
    Send actually landed via the /messaging/thread/ URL redirect (the same
    signal we trust for DMs).

    Graceful skips (return {ok:false, no_credit:true}) — the SEQUENCE moves
    on instead of burning retries:
      - No Message element on the profile (not Premium / not Open Profile).
      - LinkedIn shows an upsell prompt instead of a composer.
      - Composer never opens (silent gate: often no InMail capability).

    Hard failures (return {ok:false} without no_credit) — the SEQUENCE
    retries:
      - Send button not found after fill.
      - Any exception in the flow."""
    try:
        logger.info(
            "inmail: START — profile=%s subject=%r body_len=%d",
            profile_url, (subject or "")[:60], len(body or ""),
        )
        open_profile(page, profile_url, settings)
        with contextlib.suppress(Exception):
            page.evaluate("() => window.scrollTo(0, 0)")
        logger.info("inmail: profile loaded — url=%s", page.url)

        # Step 1: try compose-URL shortcut.  On some Premium accounts the
        # profile Message button carries a `/messaging/compose/?profileUrn=…&recipient=…`
        # href we can navigate to directly — cheaper (no click race) and
        # cleaner than driving the profile overlay.
        upsell = _detect_message_upsell(page)
        compose_url = upsell.get("compose_url")
        is_upsell = bool(upsell.get("upsell"))
        opened_via = None

        # ⚠️ CRITICAL:  Only use the compose URL fallback when the profile
        # Message button is an actual UPSELL LINK (free-account "Message with
        # Premium" href).  On Premium accounts the Message button is a real
        # button — navigating to `/messaging/compose/?recipient=…` opens the
        # full messaging center WITHOUT binding to the URL's recipient, and
        # the compose panel defaults to whoever was last focused → subject+
        # body get sent to the WRONG PERSON.  Always click the profile
        # Message button on Premium so the overlay is bound to this profile.
        # STRATEGY: try the profile Message button FIRST (safest — the overlay
        # is bound to THIS profile).  If we can't find/click it (LinkedIn
        # sometimes doesn't render one for 3rd-degree Premium profiles until
        # after a delay), fall back to the compose URL and rely on the
        # recipient safety check to catch mismatches BEFORE burning a credit.
        if is_upsell and compose_url:
            if compose_url.startswith("/"):
                compose_url = "https://www.linkedin.com" + compose_url
            logger.info("inmail: step 1/5 — upsell detected, opening composer via compose-URL")
            page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
            opened_via = "compose-url"
        else:
            # The profile page has MULTIPLE "Message" buttons — the one we want
            # is Patrick's own (blue, in his profile action bar), NOT any of:
            #   • sidebar "Message [Name]" buttons in "More profiles for you"
            #   • the persistent "Messaging" overlay at the bottom-right
            #   • the top-nav "Messaging" link
            # The BEST signal is the aria-label — LinkedIn labels the profile
            # Message button as "Message <FullName>". Read the H1 first,
            # then click the button whose aria-label targets THAT name.
            identity = capture_profile_identity(page)
            raw_name = (identity.get("name") or "").strip()
            recipient_name = _clean_display_name(raw_name) or ""
            logger.info(
                "inmail: step 1/5 — clicking profile Message button (recipient=%r)",
                recipient_name,
            )

            # DOM structure (verified 2026-08-05 via user-provided HTML):
            #   Profile action bar layout:
            #     <section aria-label="Primary content">   ← Patrick's top card
            #       ...
            #       <a href="/messaging/compose/?profileUrn=...&recipient=...">
            #         <svg id="send-privately-medium">      ← paper-plane icon
            #         <span>Message</span>
            #       </a>
            #       <a href="/preload/custom-invite/?vanityName=patrickmichalina"
            #          aria-label="Invite Patrick Michalina to connect">Connect</a>
            #       <button aria-label="More">...</button>
            #
            #   Sidebar layout:
            #     <aside aria-label="Aside">                ← recommendations
            #       ...
            #       <a href="/messaging/compose/?...(Anik's URN)"
            #          aria-label="Message Anik Nagpal">     ← labeled by name!
            #         <svg id="send-privately-medium">      ← same icon reused
            #
            # UNIQUE signal for the profile Message button:
            #   • It's an <a> (not <button>) inside <section aria-label="Primary content">
            #   • It has NO aria-label (sidebar buttons have "Message [Name]")
            #   • Its href contains the profile's own recipient URN
            clicked = _click_first_visible(page, [
                # Most specific — <a> inside primary content, not aria-labeled
                "section[aria-label='Primary content'] a:has(svg#send-privately-medium):not([aria-label])",
                # Slightly broader — <a> inside primary content with the icon
                "section[aria-label='Primary content'] a:has(svg#send-privately-medium)",
                # Fallback — any <a> in primary content with 'Message' text (no aria-label)
                "section[aria-label='Primary content'] a:has-text('Message'):not([aria-label])",
            ])
            if not clicked:
                # Secondary — Playwright role='link' with exact 'Message' name
                # (sidebar buttons have accessible name 'Message [FullName]',
                # so exact 'Message' matches only the profile button)
                import re as _re
                for role in ("link", "button"):
                    with contextlib.suppress(Exception):
                        loc = page.get_by_role(role, name=_re.compile(r"^Message$")).first
                        if loc.is_visible(timeout=2000):
                            with contextlib.suppress(Exception):
                                loc.scroll_into_view_if_needed(timeout=2000)
                            loc.click(timeout=6000)
                            logger.info("inmail: clicked via role=%r name=/^Message$/", role)
                            clicked = True
                            break
            if not clicked:
                logger.info(
                    "inmail: no Message option on %s — skipping gracefully "
                    "(likely not Premium / not Open Profile)", profile_url,
                )
                _log_buttons(page, "no_inmail_message_btn")
                return {"ok": False, "no_credit": True, "detail": "no message option"}
            opened_via = "profile-button"
        human_pause(settings, lo=2, hi=5)

        # Step 2: wait for the composer form to appear.
        logger.info("inmail: step 2/5 — waiting for composer (opened_via=%s)", opened_via)
        with contextlib.suppress(Exception):
            page.wait_for_selector(
                "div.msg-form__contenteditable[contenteditable=true], "
                "div[role='textbox'][contenteditable=true], "
                "input[name=subject], input[placeholder*='Subject' i], "
                "input[placeholder*='name' i], input[aria-label*='recipient' i]",
                state="visible", timeout=10000,
            )

        # Step 2b: bail early if LinkedIn shows an upsell instead of composer.
        # This must run BEFORE fill so we never burn a credit on a broken UI.
        if _detect_inmail_upsell(page):
            logger.info(
                "inmail: upsell prompt visible on %s — no credits / not Premium; skipping",
                profile_url,
            )
            _debug_shot(page, "inmail_upsell")
            return {"ok": False, "no_credit": True, "detail": "upsell shown"}

        # Step 2c: If for any reason the To field is still empty (LinkedIn
        # occasionally races the recipient binding), fall back to typeahead
        # to type + select the recipient.  No-op when recipient is already
        # bound (the common case now that we click the name-scoped button).
        _ensure_recipient_selected(page, profile_url, settings)

        # Step 2d: SAFETY CHECK — before we fill subject/body, verify the
        # composer is actually addressed to the intended recipient.  If we
        # clicked a wrong Message button (e.g. a sidebar "Message <SomeoneElse>"),
        # the composer will show SomeoneElse's name — sending would burn a
        # credit on the wrong person.  Abort here rather than pay for a
        # misfire.
        if not _composer_recipient_matches(page, profile_url):
            logger.error(
                "inmail: ABORTING — composer recipient does NOT match intended "
                "profile %s. Likely clicked a sidebar Message button. "
                "No credit burned.", profile_url,
            )
            _debug_shot(page, "inmail_wrong_recipient")
            return {"ok": False, "detail": "composer recipient mismatch"}

        # Step 3: subject (InMail-only field — DMs don't have one).  If the
        # field doesn't render, this is a regular DM composer and we skip the
        # subject fill silently — the body still goes through.
        if subject:
            logger.info("inmail: step 3/5 — filling subject (%d chars)", len(subject))
            filled_subject = (
                _fill_by_role(page, "textbox", "Subject", subject[:120])
                or _fill_by_placeholder(page, "Subject", subject[:120])
                or _fill_first_visible(
                    page,
                    "input[name=subject], input[placeholder*='Subject' i]",
                    subject[:120],
                )
            )
            if filled_subject:
                logger.info("inmail: subject filled")
            else:
                logger.info("inmail: no subject field visible — proceeding as body-only DM")

        # Step 4: body.  Prefer Playwright role-based locator ("Write a
        # message" textbox), then fall back to CSS.
        logger.info("inmail: step 4/5 — filling body (%d chars)", len(body or ""))
        filled = _fill_by_role(page, "textbox", "Write a message", body)
        if not filled:
            for sel in ("div.msg-form__contenteditable[contenteditable=true]",
                        "div[role='textbox'][contenteditable=true]",
                        "div[contenteditable=true]"):
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible():
                        loc.fill(body, timeout=5000)
                        filled = True
                        logger.info("inmail: body filled via selector %r", sel)
                        break
                except Exception:  # noqa: BLE001
                    continue
        if not filled:
            logger.info(
                "inmail: composer body not fillable on %s — skipping gracefully "
                "(likely no Premium/credit)", profile_url,
            )
            _log_buttons(page, "no_inmail_body")
            _debug_shot(page, "inmail_no_body")
            return {"ok": False, "no_credit": True, "detail": "composer body not available"}

        human_pause(settings, lo=1, hi=3)

        # Step 5: Send.  Role-based first, then CSS fallback.
        logger.info("inmail: step 5/5 — clicking Send")
        clicked_send = (
            _click_by_role(page, "button", "Send")
            or _click_first_visible(page, [
                # Send button. Icon-only <button type="submit"> with an SVG child —
        # no text, no aria-label. role="button" name="Send" DOES NOT MATCH.
        # Selector priority (per Playwright docs for icon-only buttons):
        #   1. LinkedIn's own SVG test ID  → most stable
        #   2. Current class name (msg-form__send-btn — renamed 2026-08-05)
        #   3. Old class (msg-form__send-button) — backup
        #   4. Form-scoped type=submit    → semantic HTML fallback
        "button:has(svg[data-test-icon='send-privately-small'])",
        "button:has(svg[data-test-icon*='send'])",
        "button.msg-form__send-btn",
        "button.msg-form__send-button",
        "form.msg-form button[type=submit]",
        "div.msg-form button[type=submit]",
                "div[role='dialog'] [aria-label*='Send' i]",
                "[aria-label*='Send' i]",
                "button:has-text('Send')",
            ])
        )
        if not clicked_send:
            logger.warning(
                "inmail: FAILED — Send button not found on %s (opened_via=%s). "
                "Saved li_debug_inmail_failed.*", profile_url, opened_via,
            )
            _log_buttons(page, "no_inmail_send")
            _debug_shot(page, "inmail_failed")
            return {"ok": False, "detail": "InMail Send button not found"}

        # Post-send verification — LinkedIn redirects to /messaging/thread/…
        # only when it accepted the message.  InMail redirects can be slower
        # than DM redirects (server-side credit debit + delivery), so poll
        # up to ~12s before falling back to "sent but unverified".
        human_pause(settings, lo=2, hi=4)
        verified = verify_message_in_thread(page, body)
        if not verified:
            # Extra polling window for InMail's slower server round-trip.
            for _ in range(5):
                with contextlib.suppress(Exception):
                    page.wait_for_timeout(2000)
                if verify_message_in_thread(page, body):
                    verified = True
                    break
        if not verified:
            logger.warning(
                "inmail: Send clicked but thread URL not detected on %s — "
                "may still have sent (LinkedIn sometimes lags rendering).",
                profile_url,
            )
            _debug_shot(page, "inmail_send_unverified")
            return {"ok": True, "verified": False}

        logger.info("inmail: SUCCESS — InMail sent + verified to %s", profile_url)
        return {"ok": True, "verified": True}

    except Exception as e:  # noqa: BLE001
        logger.exception("inmail: EXCEPTION — profile=%s error=%s", profile_url, e)
        _debug_shot(page, "inmail_failed")
        return {"ok": False, "detail": str(e)[:200]}


# ── Reply detection & inbox sweep ─────────────────────────────────────────────
# Merged version — combines the teammate's richer architecture (sweep_inbox,
# rich read_reply_thread return dict, human-takeover-friendly last_self_body,
# read_ok signal) with the two free-account fixes proven locally:
#   1. Compose-URL bypass — free accounts see "Message with Premium" on the
#      profile Message button; navigating to the /messaging/compose/?recipient=
#      URL LinkedIn embeds in that upsell's href lands us on the existing
#      thread anyway.
#   2. URL-slug authorship — LinkedIn removed the ``--self`` class marker
#      from outgoing bubbles.  We identify the sender by comparing each
#      message-group's profile-link href to the recipient's known slug
#      instead of relying on a class that no longer exists.
def sweep_inbox(page: Any, settings: Any, max_threads: int = 30) -> List[Dict[str, Any]]:
    """Read the messaging thread LIST — one page load, no thread opened.

    Returns [{"name": str, "preview": str, "unread": bool}, ...], newest first.

    This is a cheap DETECTOR, not a reader.  Its job is to say WHICH
    conversations have new activity so the caller can open only those.
    Cost therefore scales with replies received, not with leads enrolled: 500
    enrolled leads and 3 replies today is one list load plus three thread
    reads, not 500.

    Deliberately does not open threads — opening marks them read on LinkedIn,
    and a thread we open but don't answer leaves the prospect looking at a
    read receipt with no reply.

    Returns [] rather than raising: a failed sweep must not stop the send
    pipeline, and the send-time reply gate still catches anything missed here.
    """
    out: List[Dict[str, Any]] = []
    try:
        logger.info("inbox-sweep: opening messaging list")
        page.goto("https://www.linkedin.com/messaging/",
                  wait_until="domcontentloaded", timeout=30000)
        human_pause(settings, lo=2, hi=5)
        with contextlib.suppress(Exception):
            page.wait_for_selector(
                "li.msg-conversation-listitem, li.msg-conversations-container__convo-item",
                timeout=10000,
            )
        items = page.locator(
            "li.msg-conversation-listitem, li.msg-conversations-container__convo-item"
        )
        n = min(items.count(), max_threads)
        if n == 0:
            logger.info("inbox-sweep: no conversations visible")
            _debug_shot(page, "inbox_sweep_empty")
            return []
        for i in range(n):
            with contextlib.suppress(Exception):
                it = items.nth(i)
                name = _first_text(it, [
                    ".msg-conversation-listitem__participant-names",
                    ".msg-conversation-card__participant-names",
                    "h3",
                ])
                preview = _first_text(it, [
                    ".msg-conversation-card__message-snippet",
                    ".msg-conversation-listitem__message-snippet",
                ])
                unread = False
                with contextlib.suppress(Exception):
                    cls = (it.get_attribute("class") or "").lower()
                    unread = ("unread" in cls) or it.locator(
                        ".msg-conversation-card__unread-count, .notification-badge"
                    ).count() > 0
                if name:
                    out.append({"name": name, "preview": preview or "",
                                "unread": bool(unread)})
        logger.info("inbox-sweep: read %d conversation(s), %d unread",
                    len(out), sum(1 for c in out if c["unread"]))
        return out
    except Exception:  # noqa: BLE001 — a failed sweep must never break sending
        logger.warning("inbox-sweep: failed to read the messaging list", exc_info=True)
        _debug_shot(page, "inbox_sweep_failed")
        return []


def _first_text(scope: Any, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        with contextlib.suppress(Exception):
            loc = scope.locator(sel)
            if loc.count() > 0:
                txt = (loc.first.inner_text() or "").strip()
                if txt:
                    return " ".join(txt.split())[:300]
    return None


def _recipient_slug(profile_url: str) -> str:
    """Extract the vanity slug from a `/in/<slug>/` LinkedIn profile URL."""
    if not profile_url:
        return ""
    m = profile_url.lower().rstrip("/").split("/in/", 1)
    if len(m) < 2:
        return ""
    return m[1].split("/", 1)[0].split("?", 1)[0]


def read_reply_thread(page: Any, profile_url: str, settings: Any,
                      *, self_name: Optional[str] = None) -> Dict[str, Any]:
    """Open the conversation and read it — authorship AND text.

    Returns:
        {
          "replied":       bool,          # the LAST bubble is theirs
          "last_body":     str | None,    # text of their last message
          "last_self_body":str | None,    # text of OUR last message (human-takeover check)
          "prospect_msgs": int,
          "total_msgs":    int,
          "read_ok":       bool,          # False ⇒ authorship known, TEXT was not
        }

    ``self_name`` — the account holder's LinkedIn display name (from
    ``GtmLinkedInAccount.linkedin_display_name``). Used as the primary
    authorship signal because LinkedIn's message-group profile-link is now a
    ``<span>`` (not ``<a>``) with no ``href`` attribute — URL-slug comparison
    isn't available and name matching against a known self_name is the only
    reliable option. Falls back to positional heuristic if omitted.

    RAISES if the thread can't be read at all (thread never rendered) — the
    caller then defers and retries rather than treating an UNREADABLE thread
    as 'no reply' and messaging someone who already answered.

    `read_ok=False` is the narrower failure: we could tell WHO sent last but
    not WHAT they said.  That degrades to the un-classified path (stop), never
    to a guessed intent — a wrong intent is worse than no intent.

    Free-account bypass: the profile Message button is a "Message with
    Premium" upsell link on free accounts and won't open a compose overlay.
    We instead read the compose URL LinkedIn attaches to that upsell's
    ``href`` and navigate directly to it, which opens the existing thread.
    """
    logger.info("reply-check: opening thread for %s", profile_url)
    open_profile(page, profile_url, settings)
    with contextlib.suppress(Exception):
        page.evaluate("() => window.scrollTo(0, 0)")

    # Path 1: compose-URL (free-account safe).  Navigating to /messaging/
    # compose/?recipient=<urn> lands on the EXISTING thread when one exists.
    upsell = _detect_message_upsell(page)
    compose_url = upsell.get("compose_url")
    opened_via = None
    if compose_url:
        if compose_url.startswith("/"):
            compose_url = "https://www.linkedin.com" + compose_url
        logger.info("reply-check: opening thread via compose-URL — %s",
                    compose_url[:160])
        page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
        opened_via = "compose-url"
    else:
        # Path 2: fall back to the profile Message button (Premium accounts).
        logger.info("reply-check: no compose URL — falling back to profile Message button")
        if not _click_by_role(page, "button", "Message") and not _click_first_visible(
            page, ["main [aria-label*='Message' i]", "main button:has-text('Message')"]
        ):
            _log_buttons(page, "reply_no_message_btn")
            raise RuntimeError("reply check: Message button not found")
        opened_via = "profile-button"
    human_pause(settings, lo=2, hi=5)

    with contextlib.suppress(Exception):
        page.wait_for_selector(
            "li.msg-s-message-list__event, div.msg-s-event-listitem, div.msg-s-message-list",
            timeout=10000,
        )
    _debug_shot(page, "reply_thread")  # capture authorship markup for tuning

    # Extract each message group's sender NAME + body via one JS trip so the
    # (sender, body) pairing is consistent even if the DOM re-renders.
    #
    # LinkedIn's authorship signals have shifted twice recently:
    #  1. ``--self`` class markers removed → can't use CSS-class matching.
    #  2. ``msg-s-message-group__profile-link`` is now a ``<span>`` (not
    #     ``<a>``) → no ``href`` to compare against the recipient's URL.
    # What remains stable: the sender's display name inside
    # ``msg-s-message-group__name``. So authorship is name matching against
    # ``self_name`` (the account holder's known display name).
    scan = page.evaluate("""() => {
        const nameEls = document.querySelectorAll('.msg-s-message-group__name');
        const names = Array.from(nameEls).map(n => (n.textContent || '').trim());
        const bodyEls = document.querySelectorAll('p.msg-s-event-listitem__body, .msg-s-event-listitem__body');
        const bodies = Array.from(bodyEls).map(b => (b.innerText || b.textContent || '').trim());
        return {names: names, bodies: bodies};
    }""") or {}

    sender_names = scan.get("names") or []
    bodies = scan.get("bodies") or []
    total_msgs = len(bodies)

    logger.info(
        "reply-check: opened_via=%s bubbles=%d groups=%d senders=%s",
        opened_via, total_msgs, len(sender_names), sender_names[:4],
    )

    if not sender_names:
        raise RuntimeError("reply check: no message-group senders found")

    last_name = sender_names[-1].strip()
    first_name = sender_names[0].strip()

    # Authorship: primary path is name comparison against ``self_name``. If we
    # know our display name, the answer is unambiguous — the last sender is
    # either us (no reply) or not us (reply). If self_name isn't provided,
    # fall back to positional ("first sender = us" heuristic) which
    # false-positives when the prospect messaged us first, so callers should
    # always try to supply self_name.
    if self_name:
        norm_self = self_name.strip().lower()
        replied = (last_name.lower() != norm_self)
        decision = f"self-name (self={self_name!r}, last={last_name!r})"
    else:
        replied = (last_name != first_name)
        decision = (
            f"positional-fallback (first={first_name!r}, last={last_name!r}) "
            "— self_name not supplied, may false-positive if prospect messaged first"
        )

    # Walk bodies/senders in parallel to pick out the LAST prospect body and
    # LAST self body — the takeover check and intent classifier need both.
    last_body = last_self_body = None
    prospect_msgs = 0
    norm_self_lower = (self_name or "").strip().lower()
    for i in range(min(len(sender_names), len(bodies)) - 1, -1, -1):
        sender = sender_names[i].strip().lower()
        is_self = bool(norm_self_lower) and (sender == norm_self_lower)
        body = (bodies[i] or "").strip()
        if is_self:
            if last_self_body is None and body:
                last_self_body = body
        else:
            # If self_name isn't known, treat "not the first sender" as prospect.
            # This matches the positional decision above so counts stay consistent.
            if not norm_self_lower:
                is_prospect = (sender != first_name.strip().lower())
            else:
                is_prospect = True
            if is_prospect:
                prospect_msgs += 1
                if last_body is None and body:
                    last_body = body
        if last_body is not None and last_self_body is not None and prospect_msgs > 0:
            break

    read_ok = True
    if replied and not last_body:
        # We know they replied but couldn't read a single character — degrade
        # to the un-classified path so the caller stops rather than guessing.
        read_ok = False

    logger.info(
        "reply-check: %s → %s (decision=%s bubbles=%d prospect=%d read_ok=%s opened_via=%s last_body=%r)",
        profile_url,
        "REPLY FOUND" if replied else "no reply yet",
        decision, total_msgs, prospect_msgs, read_ok, opened_via,
        (last_body or "")[:120] + ("…" if last_body and len(last_body) > 120 else ""),
    )
    return {
        "replied": replied,
        "last_body": last_body,
        "last_self_body": last_self_body,
        "prospect_msgs": prospect_msgs,
        "total_msgs": total_msgs,
        "read_ok": read_ok,
    }


def _bubble_text(bubble: Any) -> Optional[str]:
    """Text of one message bubble, preferring the body element so we don't pick
    up the sender name / timestamp chrome that wraps it.

    Kept as a public helper — the batched JS in ``read_reply_thread`` already
    extracts every bubble's text in one round trip, but ``_bubble_text`` is
    still used by callers that walk one bubble at a time (tests, debug tools)."""
    for sel in ("p.msg-s-event-listitem__body", ".msg-s-event-listitem__body",
                ".msg-s-event__content", "p"):
        with contextlib.suppress(Exception):
            loc = bubble.locator(sel)
            if loc.count() > 0:
                txt = (loc.first.inner_text() or "").strip()
                if txt:
                    return " ".join(txt.split())[:4000]
    with contextlib.suppress(Exception):
        txt = (bubble.inner_text() or "").strip()
        if txt:
            return " ".join(txt.split())[:4000]
    return None


def detect_reply(page: Any, member_id: Optional[str], profile_url: str,
                 settings: Any) -> bool:
    """Back-compat shim for the legacy ``check_reply_status`` job path.

    Prefer ``read_reply_thread`` when the caller needs the reply body /
    authorship / read_ok signal — the reply-aware pipeline
    (``sequence_engine.on_reply_read`` → intent classification →
    re-planning) requires the richer dict this shim discards.

    ``member_id`` is accepted for signature compatibility with old call sites
    (we open the thread via the profile URL, not the member id)."""
    try:
        return bool(read_reply_thread(page, profile_url, settings)["replied"])
    except Exception:  # noqa: BLE001 — legacy callers expect the boolean shape
        logger.warning(
            "reply-check: could not read thread for %s — will RETRY "
            "(not treated as 'no reply'). Saved li_debug_reply_failed.*",
            profile_url, exc_info=True,
        )
        _debug_shot(page, "reply_failed")
        raise
