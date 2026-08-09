"""image_lab_oneshot.py — One-shot Gemini Flash Image test.

Hypothesis: skip the multi-agent pipeline (no separate visualist V2, no PIL
compositor). Hand Gemini Flash Image:

  • the campaign brief
  • the brand DNA (name, tagline, colors, tone, values)
  • a TEMPLATE REFERENCE image (e.g. Rimberio split-card sample)
  • the BRAND LOGO image

Let Gemini do everything in one call:
  1. choose to follow the template's structure
  2. generate headline / subheading / CTA copy
  3. render the finished, ready-to-post image with all overlays baked in

If output quality matches or beats the multi-agent pipeline, we collapse
the pipeline to one Gemini call.

Usage:
    cd apps/backend
    python image_lab_oneshot.py \
        --user "Info@z-ninth.com" \
        --product "Zyntegrate" \
        --template image_lab_out/t32-smoke/rimberio-style.png \
        --label oneshot-zyntegrate-rimberio \
        --brief "..."

Output:
    apps/backend/image_lab_out/<label>/
        01_brief.txt
        02_dna_summary.txt
        03_full_prompt.txt
        04_template_ref.png       (copy of input template)
        05_logo.png               (copy of input logo)
        99_output.png             (Gemini's finished image)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sqlalchemy import create_engine, text
from google import genai
from google.genai import types

OUT_BASE = HERE / "image_lab_out"
OUT_BASE.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# DB load — same as image_lab.py
# ---------------------------------------------------------------------------

def _load_user(email: str) -> SimpleNamespace:
    eng = create_engine(os.environ["DATABASE_URL"])
    with eng.connect() as c:
        row = c.execute(text("""
            SELECT id, email, company_name, business_url, business_dna
            FROM users WHERE email = :e
        """), {"e": email}).fetchone()
    if not row:
        sys.exit(f"User not found: {email}")
    dna = row.business_dna
    if isinstance(dna, str):
        try:
            dna = json.loads(dna)
        except Exception:
            dna = {}
    return SimpleNamespace(
        id=row.id, email=row.email,
        company_name=row.company_name, business_url=row.business_url,
        business_dna=dna or {},
    )


# ---------------------------------------------------------------------------
# Pull active product DNA — Zyntegrate inside Z-Ninth's products, etc.
# ---------------------------------------------------------------------------

def _resolve_product_dna(user: SimpleNamespace, product_name: str | None) -> dict:
    """Merge company-level DNA with product-level overrides if product_name
    is set and exists in business_dna.products."""
    dna = dict(user.business_dna or {})
    if not product_name:
        return dna
    products = dna.get("products") or {}
    if product_name in products:
        merged = dict(dna)
        merged.update(products[product_name] or {})
        return merged
    return dna


# ---------------------------------------------------------------------------
# Logo download
# ---------------------------------------------------------------------------

def _fetch_logo_bytes(logo_url: str) -> bytes | None:
    if not logo_url:
        return None
    try:
        r = requests.get(logo_url, timeout=15)
        if r.ok and r.content:
            return r.content
    except Exception as e:
        print(f"[warn] logo download failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Build the one-shot prompt
# ---------------------------------------------------------------------------

def _summarize_dna(dna: dict) -> str:
    lines = []
    lines.append(f"Product name: {dna.get('product_name') or dna.get('company_name', '')}")
    if dna.get("tagline") or dna.get("product_tagline"):
        lines.append(f"Tagline: {dna.get('tagline') or dna.get('product_tagline')}")
    if dna.get("overview"):
        ov = str(dna["overview"]).strip().replace("\n", " ")
        lines.append(f"Overview: {ov[:400]}")
    colors = dna.get("colors") or {}
    if isinstance(colors, dict):
        primary = colors.get("primary") or colors.get("brand")
        if primary:
            lines.append(f"Primary color: {primary}")
    elif isinstance(colors, list) and colors:
        lines.append(f"Primary color: {colors[0]}")
    if dna.get("brand_tone"):
        bt = str(dna["brand_tone"]).strip().replace("\n", " ")
        lines.append(f"Brand tone: {bt[:200]}")
    if dna.get("brand_values"):
        bv = dna["brand_values"]
        if isinstance(bv, list):
            bv = ", ".join(str(v) for v in bv[:6])
        lines.append(f"Brand values: {str(bv)[:200]}")
    if dna.get("url"):
        lines.append(f"Website: {dna['url']}")
    return "\n".join(lines)


def _build_prompt(brief: str, dna: dict) -> str:
    dna_summary = _summarize_dna(dna)
    return f"""You are a senior brand designer producing a finished, ready-to-post
LinkedIn / Instagram square (1:1) social media image.

You will be given:
  1. A campaign brief (text)
  2. The brand DNA (product name, tagline, colors, tone, values)
  3. A TEMPLATE REFERENCE image — use this as your LAYOUT GUIDE. Match its
     overall structure (where the logo sits, where the headline sits, where
     the photo zone is, where the CTA pill is, where any accent strips/
     gradients are). Do NOT copy its content — invent fresh content for our
     brief — but follow its layout grid.
  4. A BRAND LOGO image — render the logo top-left as-is, no recolor, no
     restyle. Preserve its aspect ratio.

Your job is to produce ONE finished 1:1 square image (1024×1024 or larger)
that contains:
  • The brand logo placed in the position shown by the template
  • A short bold headline (4–7 words) that captures the brief's outcome
  • A one-line subheading (8–15 words) that explains the mechanism / proof
  • A CTA pill / button (3–5 words) — verb + concrete object
  • A photo zone matching the template's photo placement, illustrating
    the brief's subject (NOT the template's example subject)
  • Any decorative gradient strips or accent shapes in the BRAND PRIMARY
    COLOR from the DNA — never the template's example colors

Hard rules:
  • Use ONLY the brand's primary color for accents — do NOT carry over the
    template reference's example palette (purple/teal/etc.)
  • Spell the brand name and tagline EXACTLY as the DNA gives them.
  • Do NOT render the parent product name as a header on any internal UI
    surface in the image — the brand mark is just the logo top-left.
  • Headlines have NO trailing period / question mark / exclamation.
  • Banned CTAs: "Learn more", "Click here", "Read more", "Submit",
    "Get started", "Find out more".
  • If the brief is a brand manifesto / hiring / quote / values statement
    (no specific feature), the photo zone should hold an editorial
    portrait or environmental shot rather than a product UI.
  • If the brief is a feature / service / metric announcement, the photo
    zone should hold a clean product UI surface OR an editorial scene
    with a monitor showing the feature.

----- CAMPAIGN BRIEF -----
{brief}

----- BRAND DNA -----
{dna_summary}

Produce the finished image now. Do not return text-only — return the
rendered image.
"""


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True, help="User email")
    p.add_argument("--product", default=None, help="Sub-product name in DNA.products")
    p.add_argument("--template", required=True, help="Path to a template reference PNG/JPG")
    p.add_argument("--brief", required=True, help="Campaign brief")
    p.add_argument("--label", default=None, help="Run label (default: timestamp)")
    p.add_argument("--logo", default=None, help="Optional override path to a logo PNG (skips DNA logo_url fetch)")
    args = p.parse_args()

    label = args.label or _dt.datetime.now().strftime("oneshot_%Y%m%d_%H%M%S")
    run_dir = OUT_BASE / label
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== Run: {label} ===")
    print(f"Output: {run_dir}")

    # Load user + DNA
    user = _load_user(args.user)
    dna = _resolve_product_dna(user, args.product)
    print(f"User: {user.email}  product={args.product or '(company-level)'}")

    # Logo bytes
    if args.logo:
        with open(args.logo, "rb") as f:
            logo_bytes = f.read()
    else:
        logo_url = dna.get("logo_url") or (user.business_dna or {}).get("logo_url")
        logo_bytes = _fetch_logo_bytes(logo_url) if logo_url else None
    if not logo_bytes:
        print("[warn] no logo bytes available — Gemini will design without a logo")

    # Template ref
    with open(args.template, "rb") as f:
        template_bytes = f.read()

    # Save inputs
    (run_dir / "01_brief.txt").write_text(args.brief, encoding="utf-8")
    (run_dir / "02_dna_summary.txt").write_text(_summarize_dna(dna), encoding="utf-8")
    full_prompt = _build_prompt(args.brief, dna)
    (run_dir / "03_full_prompt.txt").write_text(full_prompt, encoding="utf-8")
    (run_dir / "04_template_ref.png").write_bytes(template_bytes)
    if logo_bytes:
        (run_dir / "05_logo.png").write_bytes(logo_bytes)

    # Gemini call
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY or GOOGLE_API_KEY in env")
    client = genai.Client(api_key=api_key)

    contents = [full_prompt]
    contents.append(types.Part.from_bytes(data=template_bytes, mime_type="image/png"))
    if logo_bytes:
        # Detect mime from logo bytes
        mime = "image/png"
        try:
            sniff = logo_bytes[:12]
            if sniff[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            elif sniff[:4] == b"GIF8":
                mime = "image/gif"
            elif sniff[:4] == b"RIFF" and sniff[8:12] == b"WEBP":
                mime = "image/webp"
        except Exception:
            pass
        contents.append(types.Part.from_bytes(data=logo_bytes, mime_type=mime))

    print(f"Calling gemini-2.5-flash-image with {len(contents)} content parts...")
    image_bytes = None
    text_parts = []
    try:
        for chunk in client.models.generate_content_stream(
            model="gemini-2.5-flash-image",
            contents=contents,
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        ):
            if not chunk.parts:
                continue
            for part in chunk.parts:
                if getattr(part, "inline_data", None):
                    image_bytes = part.inline_data.data
                elif getattr(part, "text", None):
                    text_parts.append(part.text)
            if image_bytes:
                break
    except Exception as e:
        print(f"[error] Gemini call failed: {e}")
        sys.exit(1)

    if text_parts:
        (run_dir / "98_gemini_text.txt").write_text("\n".join(text_parts), encoding="utf-8")

    if not image_bytes:
        sys.exit("No image returned by Gemini")

    out_path = run_dir / "99_output.png"
    out_path.write_bytes(image_bytes)
    print(f"[OK] saved {out_path}")


if __name__ == "__main__":
    main()
