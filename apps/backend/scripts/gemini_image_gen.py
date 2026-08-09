#!/usr/bin/env python3
"""
Standalone Gemini image generator.

Single-prompt image generation against gemini-3.1-flash-image with an automatic
fallback to gemini-2.5-flash-image if the 3.1 model isn't available in your
project. Saves the result as a PNG.

Usage examples:

    # Minimal — uses defaults (1:1, temp 1.0, output.png in cwd)
    python gemini_image_gen.py \
        --prompt "A serene mountain lake at sunrise, photorealistic" \
        --api-key "AIza..."

    # Pick aspect ratio + output path
    python gemini_image_gen.py \
        -p "Editorial portrait of a young Indian SaaS founder, golden hour" \
        -k "$GEMINI_API_KEY" \
        -o results/founder.png \
        --aspect 9:16

    # Bump sampling temperature for more stylistic scatter
    python gemini_image_gen.py -p "..." -k "..." --temperature 1.3

Requirements:
    pip install google-genai
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google import genai
from google.genai import types


# Same primary + fallback model strategy as services/image_agent_v4.py uses.
# Gemini 3.1 image branch may not be GA in every project yet; 2.5 always is.
PRIMARY_MODEL = "gemini-3.1-flash-image"
FALLBACK_MODEL = "gemini-2.5-flash-image"

# Valid aspect ratios per the Gemini image API today. Add/remove here if Google
# expands the list in a future SDK release.
VALID_ASPECTS = ["1:1", "9:16", "16:9", "4:5", "3:4", "2:3", "5:4"]


def generate_image(
    prompt: str,
    api_key: str,
    output_path: Path,
    aspect_ratio: str = "1:1",
    temperature: float = 1.0,
    image_size: str = "1K",
) -> str:
    """Generate one image from `prompt` and save to `output_path`.

    Returns the model id that actually produced the image (primary or fallback).
    Raises RuntimeError if both models failed to return an image.
    """
    client = genai.Client(api_key=api_key)

    # Mirrors the hyperparameters in image_agent_v4.py:2332 except temperature
    # which we keep at the user's choice (default 1.0, not the 1.5 Pipelyt was
    # using — see docs/image_generation_current_approach.md §13/§14 for why
    # 1.5 was flagged as a mistake).
    gen_config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        temperature=temperature,
        top_p=0.95,
        top_k=40,
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        ),
    )

    image_bytes: bytes | None = None
    used_model = PRIMARY_MODEL
    last_error: Exception | None = None

    for model_id in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            print(f"→ Calling {model_id} ...", file=sys.stderr, flush=True)
            stream = client.models.generate_content_stream(
                model=model_id,
                contents=[prompt],
                config=gen_config,
            )
            for chunk in stream:
                if not chunk.parts:
                    continue
                for part in chunk.parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break
                if image_bytes:
                    break

            if image_bytes:
                used_model = model_id
                break

            # Stream finished with no image — treat as soft failure, try fallback
            print(
                f"  {model_id} returned no image bytes — trying fallback",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — we want to fall through to fallback
            last_error = exc
            print(f"  {model_id} failed: {exc}", file=sys.stderr)

    if not image_bytes:
        raise RuntimeError(
            f"Both models failed to return an image. Last error: {last_error}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    print(
        f"✓ Saved {len(image_bytes):,} bytes to {output_path}  "
        f"(model={used_model}, aspect={aspect_ratio}, size={image_size}, temp={temperature})",
        file=sys.stderr,
    )
    return used_model


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one image with Gemini and save it as PNG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--prompt", "-p",
        required=True,
        help="Text prompt for image generation (wrap in quotes).",
    )
    parser.add_argument(
        "--api-key", "-k",
        required=True,
        help="Google Gemini API key. You can also set GEMINI_API_KEY env and "
             "pass `-k \"$GEMINI_API_KEY\"`.",
    )
    parser.add_argument(
        "--output", "-o",
        default="output.png",
        help="Output file path (default: output.png). `.png` extension is "
             "added automatically if missing.",
    )
    parser.add_argument(
        "--aspect",
        default="1:1",
        choices=VALID_ASPECTS,
        help="Aspect ratio (default: 1:1).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Sampling temperature, 0.0–2.0 (default: 1.0). Higher = more "
             "stylistic scatter, lower = more deterministic.",
    )
    parser.add_argument(
        "--image-size",
        default="1K",
        choices=["1K", "2K"],
        help="Image resolution tier (default: 1K = 1024px on long edge).",
    )

    args = parser.parse_args()

    output_path = Path(args.output)
    if output_path.suffix.lower() != ".png":
        output_path = output_path.with_suffix(".png")

    try:
        generate_image(
            prompt=args.prompt,
            api_key=args.api_key,
            output_path=output_path,
            aspect_ratio=args.aspect,
            temperature=args.temperature,
            image_size=args.image_size,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
