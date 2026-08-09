"""image_lab_freeform.py — Freeform image-prompt method.

Hypothesis: drop ALL template/PIL constraints. Let an agent decide the layout
freely based on brief + DNA. The agent produces ONE image prompt with EVERY
literal piece of text wrapped in double quotes (so Gemini renders them as
real letters, not concepts). Then Gemini Flash Image generates the finished
post in one call, with the brand logo image attached as the ONLY reference.

Pipeline:
  brief + DNA → agent → image_prompt (text in double quotes) →
  gemini-2.5-flash-image(image_prompt + logo) → finished post

Usage:
    cd apps/backend
    python image_lab_freeform.py --user "shaiksalmanisha@gmail.com" \
        --label freeform-spenzo-pulse \
        --brief "..."

Output:
    apps/backend/image_lab_out/<label>/
        01_brief.txt
        02_dna_summary.txt
        03_agent_image_prompt.txt
        04_logo.png
        99_output.png
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import requests
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sqlalchemy import create_engine, text as sql_text
from google import genai
from google.genai import types
#testing the repo push


OUT_BASE = HERE / "image_lab_out"
# Only create the lab output dir when the lab harness is invoked as a CLI
# (`python image_lab_freeform.py …`). When this module is IMPORTED by the
# production freeform_visual_service, we MUST NOT touch the filesystem —
# AWS Lambda mounts /var/task as read-only and any mkdir there raises
# Errno 30 and bricks the import. The dir is recreated lazily inside
# `_run_once` only when needed by the CLI lab.
if __name__ == "__main__":
    try:
        OUT_BASE.mkdir(exist_ok=True)
    except OSError:
        # Read-only FS (Lambda, container) — fine, lab won't write here.
        pass


# ---------------------------------------------------------------------------
# DB load
# ---------------------------------------------------------------------------

def _load_user(email: str) -> SimpleNamespace:
    eng = create_engine(os.environ["DATABASE_URL"])
    with eng.connect() as c:
        row = c.execute(sql_text("""
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


def _resolve_product_dna(user: SimpleNamespace, product_name: str | None) -> dict:
    dna = dict(user.business_dna or {})
    if not product_name:
        return dna
    products = dna.get("products") or {}
    if product_name in products:
        merged = dict(dna)
        merged.update(products[product_name] or {})
        return merged
    return dna


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


def _summarize_dna(dna: dict) -> str:
    lines = []
    name = dna.get("product_name") or dna.get("company_name", "")
    lines.append(f"Brand name: {name}")
    if dna.get("tagline") or dna.get("product_tagline"):
        lines.append(f"Tagline: {dna.get('tagline') or dna.get('product_tagline')}")
    if dna.get("overview"):
        ov = str(dna["overview"]).strip().replace("\n", " ")
        lines.append(f"Overview: {ov[:500]}")
    colors = dna.get("colors") or {}
    primary = ""
    if isinstance(colors, dict):
        primary = colors.get("primary") or colors.get("brand") or ""
        secondary = colors.get("secondary") or ""
        if primary:
            lines.append(f"Primary color: {primary}")
        if secondary:
            lines.append(f"Secondary color: {secondary}")
    elif isinstance(colors, list) and colors:
        primary = colors[0]
        lines.append(f"Primary color: {primary}")
        if len(colors) > 1:
            lines.append(f"Secondary color: {colors[1]}")
    if dna.get("brand_tone"):
        bt = dna["brand_tone"]
        if isinstance(bt, list):
            bt = ", ".join(str(v) for v in bt[:6])
        lines.append(f"Brand tone: {str(bt)[:200]}")
    if dna.get("brand_values"):
        bv = dna["brand_values"]
        if isinstance(bv, list):
            bv = ", ".join(str(v) for v in bv[:6])
        lines.append(f"Brand values: {str(bv)[:200]}")
    fonts = dna.get("fonts") or {}
    if isinstance(fonts, dict) and fonts:
        f_primary = fonts.get("primary") or fonts.get("heading") or ""
        if f_primary:
            lines.append(f"Brand font: {f_primary}")
    return "\n".join(lines), primary


# ---------------------------------------------------------------------------
# Step 1 — Freeform image-prompt agent
# ---------------------------------------------------------------------------

from services.freeform_prompts import AGENT_SYSTEM  # noqa: F401



def _call_agent_text(brief: str, dna_summary: str, primary_hex: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY or GOOGLE_API_KEY in env")
    client = genai.Client(api_key=api_key)

    prompt = f"""{AGENT_SYSTEM}

----- CAMPAIGN BRIEF -----
{brief}

----- BRAND DNA -----
{dna_summary}

BRAND PRIMARY COLOR (HEX): {primary_hex or '(unspecified — use a visible accent in the DNA-derived palette)'}

Produce the JSON now.
"""
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
    )
    raw = resp.text.strip() if hasattr(resp, "text") else str(resp)
    # Strip code fences if any
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Try to extract first { ... } JSON object
    import re
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    body = m.group(0) if m else raw
    try:
        return json.loads(body)
    except Exception:
        pass
    # Last resort — extract each field with a regex against the raw text.
    # The agent often gets the structure right but slips on an internal
    # unescaped double quote inside image_prompt. Pull each field by name.
    out = {"headline": "", "subheading": "", "cta": "", "image_prompt": ""}
    for key in ("headline", "subheading", "cta"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
        if m:
            out[key] = m.group(1).encode("utf-8").decode("unicode_escape")
    # image_prompt is greedy from "image_prompt": " up to the LAST " before }
    m = re.search(r'"image_prompt"\s*:\s*"(.*)"\s*}\s*$', body, re.DOTALL)
    if m:
        out["image_prompt"] = m.group(1)
    return out


# ---------------------------------------------------------------------------
# Step 2 — Image generation with logo as reference
# ---------------------------------------------------------------------------

def _gen_image(image_prompt: str, logo_bytes: bytes | None) -> bytes:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    contents = [image_prompt]
    if logo_bytes:
        mime = "image/png"
        sniff = logo_bytes[:12]
        if sniff[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif sniff[:4] == b"GIF8":
            mime = "image/gif"
        elif sniff[:4] == b"RIFF" and sniff[8:12] == b"WEBP":
            mime = "image/webp"
        contents.append(types.Part.from_bytes(data=logo_bytes, mime_type=mime))

    image_bytes = None
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
                break
        if image_bytes:
            break
    if not image_bytes:
        raise RuntimeError("No image returned by Gemini")
    return image_bytes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--product", default=None)
    p.add_argument("--brief", required=True)
    p.add_argument("--label", default=None)
    p.add_argument("--logo", default=None, help="Optional logo path override")
    args = p.parse_args()

    label = args.label or _dt.datetime.now().strftime("freeform_%Y%m%d_%H%M%S")
    run_dir = OUT_BASE / label
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== Run: {label} ===")

    user = _load_user(args.user)
    dna = _resolve_product_dna(user, args.product)
    dna_summary, primary_hex = _summarize_dna(dna)

    if args.logo:
        with open(args.logo, "rb") as f:
            logo_bytes = f.read()
    else:
        logo_url = dna.get("logo_url") or (user.business_dna or {}).get("logo_url")
        logo_bytes = _fetch_logo_bytes(logo_url) if logo_url else None
    if not logo_bytes:
        print("[warn] no logo bytes available — Gemini will design without a logo")

    (run_dir / "01_brief.txt").write_text(args.brief, encoding="utf-8")
    (run_dir / "02_dna_summary.txt").write_text(dna_summary, encoding="utf-8")
    if logo_bytes:
        (run_dir / "04_logo.png").write_bytes(logo_bytes)

    # Step 1 — agent writes the image prompt
    print(f"Calling text agent (gemini-2.5-flash) for image prompt...")
    agent_out = _call_agent_text(args.brief, dna_summary, primary_hex)
    (run_dir / "03_agent_output.json").write_text(
        json.dumps(agent_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    image_prompt = agent_out.get("image_prompt", "")
    (run_dir / "03_agent_image_prompt.txt").write_text(image_prompt, encoding="utf-8")
    print(f"  Headline:   {agent_out.get('headline', '')!r}")
    print(f"  Subheading: {agent_out.get('subheading', '')!r}")
    print(f"  CTA:        {agent_out.get('cta', '')!r}")
    print(f"  Prompt len: {len(image_prompt)} chars")

    # Step 2 — image gen with logo as reference
    print("Calling gemini-2.5-flash-image...")
    out_bytes = _gen_image(image_prompt, logo_bytes)
    out_path = run_dir / "99_output.png"
    out_path.write_bytes(out_bytes)
    print(f"[OK] saved {out_path}")


if __name__ == "__main__":
    main()
