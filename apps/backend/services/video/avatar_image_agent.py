"""Avatar image generator for the v4 character-driven reel.

Each `pain_avatar` and `solution_avatar` scene carries an `avatar_prompt`
seeded by the storyboard agent. This module:
  1. Calls Gemini 2.5 Flash Image with that prompt
  2. Strips EXIF / metadata from the JPEG
  3. Uploads the bytes to S3 under ai_gen/video_avatars/<uuid>.jpg
  4. Returns the public S3 URL

Fast-fail: if image generation throws, we return None so the dispatcher
can render the video with the gradient fallback in PainAvatar /
SolutionAvatar instead of failing the whole pipeline.
"""
from __future__ import annotations

import logging
import uuid
from io import BytesIO
from typing import Optional

from google.genai import types as gemini_types

from core.config import S3_BUCKET_NAME
from core.s3_utils import get_s3_client, get_s3_url
from services.ai_service import client as gemini_client, _strip_metadata

logger = logging.getLogger("pipelyt.video.avatars")


def _augment_prompt(seed: str, aspect_ratio: str) -> str:
    """Wrap the agent-supplied seed with framing + safety directives so
    the generated photo is consistent across calls. Same belt-and-braces
    approach the social-image pipeline uses."""
    ratio_hint = {
        "9:16": "9:16 vertical portrait, subject framed centre-upper third",
        "1:1":  "1:1 square, subject centred",
        "16:9": "16:9 landscape, subject framed left-third",
    }.get(aspect_ratio, "9:16 vertical portrait")
    return (
        "Photorealistic editorial portrait photograph. "
        "DO NOT render any text, logos, watermarks, captions, or UI labels "
        "anywhere in the image — the compositor adds typography afterwards. "
        f"{ratio_hint}. Natural light, magazine-quality colour grade, "
        "shallow depth of field. "
        f"Subject: {seed.strip()}"
    )


def generate_avatar_image(prompt: str, aspect_ratio: str = "9:16") -> Optional[str]:
    """Generate ONE avatar photo and return its S3 URL, or None on failure.

    Sync call — blocks until Gemini returns. The video pipeline runs
    avatar generation in the background-worker thread so the API response
    stays fast.
    """
    if not gemini_client:
        logger.warning("[avatar] gemini_client missing; skipping image gen")
        return None
    full_prompt = _augment_prompt(prompt, aspect_ratio)

    try:
        image_bytes: Optional[bytes] = None
        for chunk in gemini_client.models.generate_content_stream(
            model="gemini-2.5-flash-image",
            contents=[full_prompt],
            config=gemini_types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        ):
            if chunk.parts:
                for part in chunk.parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break
            if image_bytes:
                break

        if not image_bytes:
            logger.warning("[avatar] gemini returned no image bytes")
            return None

        clean_bytes = _strip_metadata(image_bytes)

        s3 = get_s3_client()
        key = f"ai_gen/video_avatars/{uuid.uuid4().hex}.jpg"
        s3.upload_fileobj(
            BytesIO(clean_bytes),
            S3_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        url = get_s3_url(key)
        logger.info(f"[avatar] uploaded -> {url}")
        return url
    except Exception:
        logger.exception("[avatar] generation failed")
        return None
