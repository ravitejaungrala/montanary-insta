"""Image Check Agent — local-only post-generation audit.

After image_agent_v4 produces 3 image variants, this agent visits each URL,
downloads the rendered PNG, and asks Gemini Vision to spot issues:

  • Misspelled / hallucinated words rendered on the image
  • Duplicated icons, logos, headlines, phrases
  • Hex codes (#RRGGBB) rendered as visible text
  • Brand-guide meta-data (palette swatches, "Inter Family", "Tone", etc.)
  • Invented or hallucinated brand marks
  • Glass-morphism / 3D-render / glow-on-void AI archetype
  • Gibberish text inside rendered device screens (laptops, phones)
  • Broken punctuation (unclosed apostrophes, stray symbols)

Returns a short bullet-pointed string per variant, suitable for direct
write into a CSV cell. "No issues detected" when clean.

GUARDED: only runs when AWS_LAMBDA_FUNCTION_NAME is NOT set — production
is untouched. Cost is ~3 Gemini calls per /generate-content request, which
is a meaningful overhead and not something we want firing in Lambda.

Use:
    from services.image_check_agent import audit_image_variants_parallel
    audits = audit_image_variants_parallel(visuals, refined_brief, copy_block, client)
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import requests
from google.genai import types

logger = logging.getLogger("pipelyt.image_check")

# Vision model. Flash is cheap and accurate for this kind of structured
# visual audit. Override via env if the deployment prefers a different
# model id.
AUDIT_MODEL = os.getenv("IMAGE_CHECK_MODEL", "gemini-2.5-flash")


def _is_local() -> bool:
    """True when we're on a dev box (no Lambda env var)."""
    return not os.getenv("AWS_LAMBDA_FUNCTION_NAME")


_AUDIT_PROMPT = """\
You are auditing an AI-generated social-media post image. Your job is to find
specific quality problems so they can be fixed in the next generation.

Inspect the attached image and report EVERY issue you can see. Be specific —
quote exact misspelled words, name exactly which elements repeat, list every
hex code or meta-data string visible.

Categories to check (in priority order):

  1. TYPOS — misspelled or hallucinated words rendered on the image. Quote
     the exact rendered text and (if you can infer it) the word it should be.

  2. DUPLICATED ELEMENTS — repeated icons, repeated logos (same or similar
     brand mark drawn more than once), repeated headlines, repeated phrases,
     or multiple instances of any element that should appear once.

  3. HEX CODES OR PALETTE META-DATA RENDERED AS TEXT — strings like
     "#FF4500", "#212B36", "RGB(255,69,0)", or labels like "Brand Color",
     "Inter Family", "Typography", "Tone", "Primary Colour", "Aspect ratio".
     Palette swatch grids drawn on the post.

  4. THIRD-PARTY BRAND-MARK FAILURES — Pipelyt's policy is "official
     mark or nothing": when the brief mentions a real third-party
     brand (Salesforce, AWS, Google, Meta, Slack, OpenAI, HubSpot,
     Snowflake, TikTok, etc.) AND the image renders something at
     that brand's slot, the rendered element MUST be that brand's
     correct official mark. Anything else is a failure.

     Flag (a) INVENTED — the image renders something as a brand
       logo but it does not match that brand's real official mark.
       Examples:
         • A squirrel rendered as the Spenzo AI logo (wrong shape).
         • A misspelled or wrong-shape "Salesforce" cloud.
         • A fake Google "G" with the wrong colours / wrong shape.
         • A made-up wordmark for the brand the post is about.

     Flag (b) GENERIC SUBSTITUTE — the image uses a generic icon
       (cloud, gear, chart, cube, connector-line, database-stack,
       infinity, building, music-note, etc.) at a slot that is
       clearly meant to represent a specific real brand. The give-
       away is usually a brand NAME / LABEL next to the generic
       icon, OR the layout context (e.g. "AWS · Snowflake · Meta ·
       Google · TikTok · HubSpot" as a connector row where the
       icons are generic versions of those services).
       Examples:
         • A generic cloud icon labelled "AWS" or sitting in the
           AWS slot of a connector diagram.
         • A generic chart icon used in place of the official
           Google Ads / Meta Ads / TikTok Ads mark.
         • A generic database-stack used in place of the official
           Snowflake snowflake mark.

     Do NOT flag generic icons that are decorative and have NO
     brand context — e.g. a generic cloud floating in the
     background with no label and no implied brand role.

     Rule of thumb: if the icon is occupying a slot that the brief
     dedicates to a specific named brand, it MUST be that brand's
     official mark. Otherwise it should be omitted entirely.

  5. AI-RENDER AESTHETIC — heavy 3D glass cubes, glow-on-void backgrounds,
     dark gradient + neon, generic isometric infographic with floating UI
     cards. Note if the image looks unmistakably AI-generated rather than
     designed by a human.

  6. GIBBERISH INSIDE DEVICE SCREENS — when the image contains a laptop,
     phone, or tablet mockup whose screen text is hallucinated nonsense.

  7. BROKEN PUNCTUATION — unclosed apostrophes, mismatched quotes, stray
     decorative dots / plus signs treated as filler.

CAMPAIGN CONTEXT (for reference — issues are still issues even if they
"match" the brief):

CAMPAIGN BRIEF:
{refined_brief}

CAPTION published with this image:
{copy_block}

OUTPUT FORMAT
Return a short bullet list. One bullet per concrete issue. Use the format:

  • <category> — <specific finding>

If you find no issues, output exactly the single line:

  • No issues detected

Do NOT include any other prose, explanation, or summary. Bullets only.
"""


def _audit_one(
    url: str,
    refined_brief: str,
    copy_block: str,
    variant_idx: int,
    client,
) -> str:
    """Audit a single image. Best-effort — returns "" on any failure so the
    CSV row write still succeeds."""
    if not url or not isinstance(url, str):
        return ""
    if client is None:
        return ""
    try:
        # Download the PNG (we wrote it ourselves, should always succeed).
        r = requests.get(url, timeout=15)
        if r.status_code != 200 or not r.content:
            logger.warning(
                f"[image_check] variant {variant_idx + 1}: fetch failed "
                f"status={r.status_code} url={url[:80]}"
            )
            return ""
        from PIL import Image as PILImage
        img = PILImage.open(BytesIO(r.content))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")

        prompt = _AUDIT_PROMPT.format(
            refined_brief=(refined_brief or "")[:2000],
            copy_block=(copy_block or "")[:2000],
        )

        # top_p and top_k pinned to Gemini API defaults — explicit for
        # auditability and reproducibility across SDK versions.
        res = client.models.generate_content(
            model=AUDIT_MODEL,
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                temperature=0.2,
                top_p=0.95,
                top_k=40,
            ),
        )
        text = (res.text or "").strip()
        if not text:
            return "• Audit returned no findings"
        # Normalise to bullet format if the model wandered.
        if not text.startswith("•") and not text.startswith("-") and not text.startswith("*"):
            text = "• " + text
        return text
    except Exception as e:
        logger.warning(f"[image_check] variant {variant_idx + 1} failed: {e}")
        return ""


def audit_image_variants_parallel(
    visuals: list,
    refined_brief: str,
    copy_block: str,
    client,
) -> list[str]:
    """Run image-check audits on every visual in parallel.

    Returns a list of bullet-list strings, same length and order as `visuals`.
    Empty string for slots where audit failed or wasn't applicable.
    No-op (returns empty list) on Lambda.
    """
    if not _is_local():
        return ["" for _ in (visuals or [])]
    if not visuals:
        return []
    urls: list[str] = []
    for v in visuals:
        if isinstance(v, dict):
            urls.append(v.get("url") or "")
        else:
            urls.append("")

    results: list[str] = [""] * len(urls)
    # 3 parallel audit calls — same pattern as the image generator itself.
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        futures = {
            pool.submit(_audit_one, url, refined_brief, copy_block, i, client): i
            for i, url in enumerate(urls)
            if url
        }
        for fut in futures:
            i = futures[fut]
            try:
                results[i] = fut.result(timeout=60)
            except Exception as e:
                logger.warning(f"[image_check] variant {i + 1} future raised: {e}")
                results[i] = ""
    logger.info(
        f"[image_check] audited {sum(1 for r in results if r)} / {len(results)} variants"
    )
    return results
