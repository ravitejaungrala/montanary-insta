"""Storyboard agent — Phase 1 (v4 character-driven 4-scene framework).

Given a campaign brief + the user's business_dna, produces a reel-promo
storyboard whose JSON shape exactly matches
`apps/video-renderer/src/storyboard-types.ts::ReelPromoStoryboard` (v4).

The v4 arc is character-driven, not motion-graphics:
  1. logo_intro       (0-2s)   — brand stamp
  2. pain_avatar      (8s)     — a person STRUGGLING with the problem
  3. solution_avatar  (12s)    — a different person EXPLAINING the product
  4. cta_card         (8s)     — action close

The agent runs ONE Gemini 2.5 Pro call with strict JSON-mode and a
prompt walked through the framework beat by beat. We defensively
validate the output before returning so the renderer can never receive
malformed JSON.

The avatar scenes carry an `avatar_prompt` that downstream
`avatar_image_agent.generate_avatar_image()` consumes to produce real
photographic backgrounds. The agent does NOT fill `avatar_url` — that
is done by the worker after image generation completes.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from google.genai import types as gemini_types

from services.ai_service import client as gemini_client

logger = logging.getLogger("pipelyt.video.storyboard")


_ALLOWED_RATIOS = {"9:16", "1:1", "16:9"}
_REQUIRED_SCENE_SEQUENCE = [
    "logo_intro",
    "pain_avatar",
    "solution_avatar",
    "cta_card",
]


def _build_prompt(
    campaign_brief: str,
    company_name: str,
    company_url: str,
    tagline: Optional[str],
    primary_color: str,
    aspect_ratio: str,
) -> str:
    return f"""You are a senior video script director for {company_name}.

YOUR TASK
Produce a 30-second short-form video storyboard in STRICT JSON for the
campaign brief below. Use the V4 CHARACTER-DRIVEN FRAMEWORK — the
audience needs to see a HUMAN like them struggling, then a HUMAN like
who they want to become explaining the answer:

  1. LOGO INTRO       (0-2s)    Brand stamp. Render handles this.
  2. PAIN AVATAR      (2-10s)   A photo of a person clearly struggling
                                with the problem. They speak ONE
                                first-person quote describing the pain.
  3. SOLUTION AVATAR  (10-22s)  A DIFFERENT person — the confident
                                user who has adopted the product. They
                                speak the solution in first person and
                                cite ONE proof point.
  4. CTA              (22-30s)  Action close.

Total = 30s. Durations BELOW are fixed — do not change them.

CAMPAIGN BRIEF
\"\"\"
{campaign_brief.strip()}
\"\"\"

BRAND CONTEXT
  Company name : {company_name}
  Company URL  : {company_url or '(not provided)'}
  Tagline      : {tagline or '(none)'}
  Primary col  : {primary_color}
  Aspect ratio : {aspect_ratio}

OUTPUT JSON SHAPE (return ONLY this, no markdown fences, no commentary)
{{
  "template": "reel_promo",
  "aspect_ratio": "{aspect_ratio}",
  "brand": {{
    "primary_color": "{primary_color}",
    "secondary_color": "#0f172a",
    "background_color": "#ffffff",
    "text_color": "#0f172a",
    "logo_url": "",
    "company_name": "{company_name}",
    "tagline": "{tagline or ''}"
  }},
  "scenes": [
    {{ "type": "logo_intro", "duration_sec": 2 }},

    {{ "type": "pain_avatar", "duration_sec": 8,
       "avatar_prompt": "<photographic prompt for Gemini 2.5 Flash Image — describe the person, their facial expression of struggle, the setting, lighting, framing. Specify {aspect_ratio} portrait, photorealistic editorial style. NO text, NO logos in the photo. The character should look like the typical PERSONA who experiences this problem.>",
       "label": "The Problem",
       "quote": "<first-person, 12-22 word quote describing the pain in the voice of the persona. Example: 'I waste 30% of my marketing budget every quarter and have no idea which channels actually work.'>"
    }},

    {{ "type": "solution_avatar", "duration_sec": 12,
       "avatar_prompt": "<photographic prompt for a DIFFERENT person — confident, professional, the persona AFTER adopting the product. Specify expression, setting, lighting, framing. {aspect_ratio} portrait, photorealistic editorial. NO text, NO logos in the photo.>",
       "label": "The Solution",
       "quote": "<first-person 14-24 word quote explaining how {company_name} solves the pain stated above. Must reference the PRODUCT name once and the OUTCOME concretely.>",
       "proof": "<≤6 word proof badge. Example: 'Save 60% on wasted spend' / '10x faster reporting'. Optional — omit field entirely if no honest proof point fits.>"
    }},

    {{ "type": "cta_card", "duration_sec": 8,
       "headline": "<3-5 word verb-led action: 'See How It Works', 'Start Free Today', 'Try {company_name} Free'>",
       "subline": "<{company_url or company_name + '.com'}>"
    }}
  ]
}}

WRITING RULES (strict)
- Pain quote and solution quote MUST address the SAME pain domain. If
  pain talks about wasted spend, solution talks about saving spend.
  Never two unrelated topics.
- Quotes are FIRST PERSON ("I…", "We…"), conversational, like a
  testimonial — NOT marketing copy.
- The two avatars must be DIFFERENT people (different gender, age,
  ethnicity, or setting) so the cut between them feels meaningful.
- Avatar prompts must be PHOTOGRAPHIC and SPECIFIC: include age range,
  occupation cue, expression, setting, lighting, framing. They must
  explicitly state "no text, no logos, no UI in the image".
- CTA headline MUST start with a verb. NEVER "Learn more", "Click here".
- All visible text in TITLE CASE OR SENTENCE CASE — no SHOUTING ALL CAPS.
- Never fabricate stats. If you cite a number in the proof, it should be
  defensible from the brief.

RETURN ONLY THE JSON OBJECT.
"""


def _validate_storyboard(sb: dict, aspect_ratio: str) -> None:
    """Cheap structural checks before we ship the JSON to Remotion."""
    if not isinstance(sb, dict):
        raise ValueError("storyboard is not a dict")
    if sb.get("template") != "reel_promo":
        raise ValueError(f"unsupported template {sb.get('template')!r}")
    if sb.get("aspect_ratio") != aspect_ratio:
        raise ValueError(
            f"aspect_ratio mismatch: prompt asked {aspect_ratio} but model returned {sb.get('aspect_ratio')!r}"
        )
    brand = sb.get("brand")
    if not isinstance(brand, dict):
        raise ValueError("brand block missing or wrong shape")
    for k in ("primary_color", "company_name"):
        if not brand.get(k):
            raise ValueError(f"brand.{k} is empty")
    scenes = sb.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 4:
        raise ValueError(f"expected 4 scenes, got {len(scenes) if isinstance(scenes, list) else type(scenes)}")
    for i, (sc, expected_type) in enumerate(zip(scenes, _REQUIRED_SCENE_SEQUENCE)):
        if sc.get("type") != expected_type:
            raise ValueError(f"scene[{i}] type mismatch: expected {expected_type!r}, got {sc.get('type')!r}")
        if not isinstance(sc.get("duration_sec"), (int, float)) or sc["duration_sec"] <= 0:
            raise ValueError(f"scene[{i}] has invalid duration_sec")
        # Per-type required fields
        if expected_type == "pain_avatar":
            for f in ("avatar_prompt", "quote"):
                if not (sc.get(f) or "").strip():
                    raise ValueError(f"pain_avatar missing {f!r}")
        elif expected_type == "solution_avatar":
            for f in ("avatar_prompt", "quote"):
                if not (sc.get(f) or "").strip():
                    raise ValueError(f"solution_avatar missing {f!r}")
        elif expected_type == "cta_card":
            if not (sc.get("headline") or "").strip():
                raise ValueError("cta_card missing headline")


def generate_storyboard(
    *,
    campaign_brief: str,
    company_name: str,
    company_url: str = "",
    tagline: str = "",
    logo_url: str = "",
    primary_color: str = "#FF4500",
    aspect_ratio: str = "9:16",
) -> dict:
    if not gemini_client:
        raise RuntimeError("Gemini client is not configured (GEMINI_API_KEY missing).")
    if aspect_ratio not in _ALLOWED_RATIOS:
        raise ValueError(f"aspect_ratio must be one of {_ALLOWED_RATIOS}")

    prompt = _build_prompt(
        campaign_brief=campaign_brief, company_name=company_name,
        company_url=company_url, tagline=tagline,
        primary_color=primary_color, aspect_ratio=aspect_ratio,
    )

    logger.info(f"[storyboard] generating for {company_name!r} ratio={aspect_ratio} (v4 character-driven)")

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[prompt],
        config=gemini_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
        ),
    )

    raw = (response.text or "").strip()
    if not raw:
        raise ValueError("Gemini returned an empty response.")

    try:
        sb = json.loads(raw)
    except json.JSONDecodeError as e:
        cleaned = raw.strip().lstrip("`json").rstrip("`").strip()
        try:
            sb = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"Gemini returned non-JSON: {e}; head={raw[:200]!r}")

    # Server-authoritative brand overrides — never trust the LLM with
    # the customer's actual logo URL or canonical name.
    sb.setdefault("brand", {})
    sb["brand"]["company_name"] = company_name
    sb["brand"]["primary_color"] = primary_color
    if tagline:
        sb["brand"]["tagline"] = tagline
    if logo_url:
        sb["brand"]["logo_url"] = logo_url

    _validate_storyboard(sb, aspect_ratio=aspect_ratio)
    logger.info("[storyboard] validated OK (4 scenes, v4)")
    return sb
