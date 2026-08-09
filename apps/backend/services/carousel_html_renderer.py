"""Playwright-based HTML to PNG renderer for the HTML Carousel pipeline.

Takes a list of standalone HTML documents (one per slide) and renders each
to a 1024x1024 (or aspect-matching) PNG using headless Chromium.

Why sync API: the carousel pipeline runs in a FastAPI sync endpoint that
already executes in a worker thread, so there's no asyncio event loop in
this thread - sync Playwright is the right choice.

Concurrency model: SEQUENTIAL within one browser instance. Each slide
takes ~1-2s on Chromium, so 6 slides = ~6-12s total - already 30x faster
than gpt-image-2's ~3 minutes. Adding parallelism would require process
isolation (Playwright sync API is not thread-safe within one Playwright
instance) which is heavy for the marginal gain.

LOGO substitution: the HTML produced by the director contains a literal
{{LOGO_DATA_URL}} placeholder where the brand logo <img> src should be.
We substitute that placeholder with a real data: URL built from
logo_bytes before page.set_content(). If logo_bytes is None we substitute
with a transparent 1x1 PNG so the <img> renders blank instead of broken.

One-time setup: the host must run `python -m playwright install chromium`
once before this module works. Caught up-front in _launch() with a clear
hint.
"""
from __future__ import annotations

import base64
import logging
import time
from io import BytesIO
from typing import Any

logger = logging.getLogger("pipelyt.carousel_html_renderer")


# 1x1 transparent PNG (base64). Used when brand has no logo so the
# <img class="brand-logo"> tag still renders something instead of the
# browser's broken-image icon.
_TRANSPARENT_1X1_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_TRANSPARENT_LOGO_URL = "data:image/png;base64," + _TRANSPARENT_1X1_PNG_B64


def _logo_to_data_url(logo_bytes: bytes | None) -> str:
    """Convert brand logo bytes to a data: URL. Always normalises to PNG.
    Returns a transparent 1x1 PNG URL if logo_bytes is missing."""
    if not logo_bytes:
        return _TRANSPARENT_LOGO_URL
    try:
        from PIL import Image as PILImage
        img = PILImage.open(BytesIO(logo_bytes))
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")
        out = BytesIO()
        img.save(out, format="PNG")
        b64 = base64.b64encode(out.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64
    except Exception as exc:
        logger.warning(f"[html_renderer] logo conversion failed ({exc}); using transparent placeholder")
        return _TRANSPARENT_LOGO_URL


def _substitute_logo(html: str, logo_data_url: str) -> str:
    """Replace every {{LOGO_DATA_URL}} placeholder with the real data URL.
    Tolerates whitespace inside the braces."""
    import re as _re
    return _re.sub(r"\{\{\s*LOGO_DATA_URL\s*\}\}", logo_data_url, html)


def _find_full_chromium_exe() -> str | None:
    """Locate the full chromium chrome.exe (NOT headless-shell) inside the
    Playwright browsers directory. Picks the highest-numbered chromium-*
    folder so we always pick the latest install. Returns None if nothing
    is found.

    Looks in multiple base directories to survive cases where the uvicorn
    process has a different HOME / USERPROFILE resolution than the install
    command did.
    """
    import glob
    import os
    from pathlib import Path
    bases: list[str] = []

    def _add(path: str | None):
        if path and path not in bases:
            bases.append(path)

    _add(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or None)
    # Windows: USERPROFILE / LOCALAPPDATA are authoritative regardless of how
    # ~ resolves.
    _add(os.environ.get("LOCALAPPDATA") and
         os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright"))
    if os.environ.get("USERPROFILE"):
        _add(os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "ms-playwright"))
    # pathlib.Path.home() respects USERPROFILE on Windows
    _add(os.path.join(str(Path.home()), "AppData", "Local", "ms-playwright"))
    _add(os.path.join(str(Path.home()), "Library", "Caches", "ms-playwright"))
    _add(os.path.join(str(Path.home()), ".cache", "ms-playwright"))
    # Old-school expanduser as a last resort
    _add(os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"))
    # Per-platform exe path under chromium-*/
    exe_patterns = [
        ("chrome-win64", "chrome.exe"),  # Windows 64-bit
        ("chrome-win32", "chrome.exe"),  # Windows 32-bit (legacy)
        ("chrome-mac",   "Chromium.app/Contents/MacOS/Chromium"),
        ("chrome-linux", "chrome"),
    ]
    tried: list[str] = []
    best_version = -1
    best_exe: str | None = None
    diag: list[str] = []
    for base in bases:
        if not base:
            continue
        tried.append(base)
        # Try BOTH os.listdir (more permissive on Windows path edge cases)
        # and glob.glob, so we capture either way.
        chromium_dirs: list[str] = []
        try:
            for name in os.listdir(base):
                if name.startswith("chromium-") and not name.startswith("chromium_"):
                    chromium_dirs.append(os.path.join(base, name))
        except (OSError, PermissionError) as exc:
            diag.append(f"  {base!r}: listdir failed: {exc!r}")
            # Try glob as fallback
            try:
                chromium_dirs = glob.glob(os.path.join(base, "chromium-*"))
            except Exception as exc2:
                diag.append(f"  {base!r}: glob also failed: {exc2!r}")
                continue
        if not chromium_dirs:
            diag.append(f"  {base!r}: no chromium-* subdirs found")
            continue
        diag.append(f"  {base!r}: found chromium dirs: {chromium_dirs}")
        for chromium_dir in chromium_dirs:
            try:
                version = int(os.path.basename(chromium_dir).split("-", 1)[1])
            except (IndexError, ValueError):
                version = 0
            for subdir, exe in exe_patterns:
                candidate = os.path.join(chromium_dir, subdir, exe)
                exists = os.path.exists(candidate)
                diag.append(f"    -> {candidate} exists={exists} version={version}")
                if exists and version > best_version:
                    best_version = version
                    best_exe = candidate
    if best_exe is None:
        logger.error(
            f"[html_renderer] _find_full_chromium_exe found nothing.\n"
            f"  Searched bases: {tried}\n"
            f"  Diagnostics:\n"
            + "\n".join(diag)
        )
    else:
        logger.info(f"[html_renderer] _find_full_chromium_exe picked: {best_exe}")
    return best_exe


# Viewport sizes per aspect — same map as the image pipeline so PDF page
# sizes line up downstream.
_ASPECT_TO_VIEWPORT = {
    "1:1":  (1024, 1024),
    "4:5":  (1024, 1280),
    "16:9": (1792, 1024),
    "9:16": (1024, 1792),
}
_DEFAULT_VIEWPORT = (1024, 1024)


def render_html_slides_to_pngs(
    html_slides: list[str],
    *,
    logo_bytes: bytes | None,
    aspect_ratio: str = "1:1",
    timeout_ms: int = 20_000,
) -> list[tuple[bytes, float]]:
    """Render each HTML slide to a PNG. Returns [(png_bytes, render_time_s), ...].

    Sequential rendering inside ONE Chromium instance. Each slide gets
    its own context (so styles/fonts/state don't leak between slides).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed in this venv. Run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
            f"underlying error: {exc}"
        ) from exc

    if not html_slides:
        return []

    viewport_w, viewport_h = _ASPECT_TO_VIEWPORT.get(aspect_ratio, _DEFAULT_VIEWPORT)
    logo_url = _logo_to_data_url(logo_bytes)

    results: list[tuple[bytes, float]] = []
    launch_t0 = time.monotonic()
    with sync_playwright() as p:
        # Try default headless launch first. On some Windows setups the
        # headless-shell variant fails with "Executable doesn't exist"
        # even though the file is on disk - falling back to the full
        # chromium binary fixes it. We resolve the explicit path by
        # asking Playwright for it (.executable_path); we use that on the
        # second attempt to bypass any internal registry lookup.
        launch_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        try:
            browser = p.chromium.launch(headless=True, args=launch_args)
        except Exception as first_err:
            logger.warning(
                f"[html_renderer] default chromium launch failed: {first_err!r}. "
                f"Retrying with explicit executable_path to the full chromium binary."
            )
            full_chromium_exe = _find_full_chromium_exe()
            if not full_chromium_exe:
                raise RuntimeError(
                    "Default chromium launch failed AND no full chromium binary "
                    "could be located on disk. Try:\n"
                    "  python -m playwright install chromium --force\n"
                    f"underlying error: {first_err}"
                ) from first_err
            logger.info(f"[html_renderer] using executable_path={full_chromium_exe}")
            browser = p.chromium.launch(
                headless=True,
                args=launch_args,
                executable_path=full_chromium_exe,
            )
        launch_s = round(time.monotonic() - launch_t0, 2)
        logger.info(
            f"[html_renderer] Chromium launched in {launch_s}s; "
            f"viewport={viewport_w}x{viewport_h} slides={len(html_slides)}"
        )
        try:
            for i, raw_html in enumerate(html_slides, 1):
                t0 = time.monotonic()
                html = _substitute_logo(raw_html, logo_url)
                ctx = browser.new_context(
                    viewport={"width": viewport_w, "height": viewport_h},
                    device_scale_factor=1,
                )
                page = ctx.new_page()
                try:
                    # `networkidle` waits for Google Fonts / @import loads.
                    # Cap with timeout_ms so a misbehaving font URL can't
                    # stall the pipeline forever.
                    page.set_content(html, wait_until="networkidle", timeout=timeout_ms)
                    png = page.screenshot(
                        type="png",
                        full_page=False,
                        omit_background=False,
                    )
                except Exception as render_exc:
                    logger.error(
                        f"[html_renderer] slide {i} render failed: {render_exc}"
                    )
                    ctx.close()
                    raise
                ctx.close()
                elapsed = round(time.monotonic() - t0, 2)
                logger.info(
                    f"[html_renderer] slide {i} rendered "
                    f"{len(png):,} bytes in {elapsed}s"
                )
                results.append((png, elapsed))
        finally:
            browser.close()

    return results
