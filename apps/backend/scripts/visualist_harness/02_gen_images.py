"""Test harness — Step 2.

Reads ./out/visualist_output.json (produced by 01_run_visualist.py) and calls
Gemini 2.5 Flash Image on each of the 3 image_prompts. Saves the raw PNGs to
./out/img_1.png, ./out/img_2.png, ./out/img_3.png for human review.

Usage (from repo root):
    cd apps/backend
    source venv/Scripts/activate
    python scripts/visualist_harness/02_gen_images.py
    # optional: python scripts/visualist_harness/02_gen_images.py --prompt 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")

from google import genai  # type: ignore
from google.genai import types  # type: ignore

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("Set GEMINI_API_KEY in apps/backend/.env")

client = genai.Client(api_key=API_KEY)

HERE = Path(__file__).parent
OUT = HERE / "out"
INPUT_JSON = OUT / "visualist_output.json"

IMAGE_MODEL = "gemini-2.5-flash-image"
# Pull aspect ratio from the visualist output if present, else env, else 1:1.
# Loaded lazily in main() so the value reflects the latest JSON.
DEFAULT_ASPECT_RATIO = os.getenv("ASPECT_RATIO", "1:1")


def _generate_one(prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO) -> bytes:
    """Call Gemini 2.5 Flash Image with aspect_ratio passed via API config.

    Tries the newer image_config.aspect_ratio param first. Falls back to the
    older response_modalities-only shape if the SDK version doesn't support
    image_config yet (so this script works on whatever google-genai is
    installed).
    """
    # Try the modern path (aspect_ratio via API)
    config_modern = None
    try:
        image_cfg = types.ImageConfig(aspect_ratio=aspect_ratio)  # type: ignore[attr-defined]
        config_modern = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=image_cfg,
        )
    except Exception as exc:
        print(f"  (ImageConfig not available in SDK: {exc}; falling back)")
        config_modern = None

    config = config_modern or types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"]
    )

    image_bytes: bytes | None = None
    for chunk in client.models.generate_content_stream(
        model=IMAGE_MODEL,
        contents=[prompt],
        config=config,
    ):
        if chunk.parts:
            for part in chunk.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    break
        if image_bytes:
            break

    if not image_bytes:
        raise RuntimeError("Gemini returned no image bytes")
    return image_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt",
        type=int,
        default=0,
        help="1, 2, or 3 to generate only that scene. 0 (default) = all 3.",
    )
    args = parser.parse_args()

    if not INPUT_JSON.exists():
        raise SystemExit(
            f"{INPUT_JSON} not found. Run 01_run_visualist.py first."
        )

    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    aspect_ratio = (data.get("brand") or {}).get("aspect_ratio") or DEFAULT_ASPECT_RATIO
    print(f"Using aspect_ratio: {aspect_ratio}")
    raw_prompts = data.get("image_prompts", [])
    # Accept both old shape (list of strings) and new shape (list of
    # {scene_type, prompt} dicts).
    prompts: list[tuple[str, str]] = []
    for item in raw_prompts:
        if isinstance(item, dict):
            prompts.append((item.get("scene_type", "?"), item.get("prompt", "")))
        else:
            prompts.append(("(legacy-string)", str(item)))
    if len(prompts) < 3:
        raise SystemExit(f"Expected 3 image_prompts, got {len(prompts)}")

    indices = [args.prompt - 1] if args.prompt in (1, 2, 3) else [0, 1, 2]

    for i in indices:
        scene, prompt = prompts[i]
        print(f"\n[scene_type: {scene}]")
        out_path = OUT / f"img_{i + 1}.png"
        print(f"\n--- Scene {i + 1} ---")
        print(prompt[:160] + ("..." if len(prompt) > 160 else ""))
        print(f"Generating -> {out_path.name} (aspect_ratio={aspect_ratio})")
        try:
            img = _generate_one(prompt, aspect_ratio)
            out_path.write_bytes(img)
            print(f"  OK saved {len(img):,} bytes")
        except Exception as e:
            print(f"  FAILED: {e}")


if __name__ == "__main__":
    main()
