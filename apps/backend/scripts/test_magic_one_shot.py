"""One-shot test of the magic image pipeline with the NeuzenAI brief
the user pasted. Runs Agent 1 (GPT-5) → Agent 2 (gpt-image-2 high) → S3.

Usage:
    venv/Scripts/python.exe scripts/test_magic_one_shot.py
"""
import os
import sys
from pathlib import Path

# Ensure repo backend root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env so we get OPENAI_API_KEY without exporting manually
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests
from services.magic_image_pipeline import run_magic_image_pipeline

# ── Inputs from the user's message ───────────────────────────────────
POST_TEXT = (
    "The era of 'AI experimentation' is closing. The era of 'AI operationalization' is here.\n\n"
    "For enterprise leaders, the challenge is no longer about finding a model that works — "
    "it's about finding a partner that understands how to integrate AI into a human-centric, "
    "ethical ecosystem.\n\n"
    "At NeuZenAI, we've built our foundation on four pillars:\n\n"
    "• Innovation First: We don't just follow trends; we engineer solutions that move the needle.\n"
    "• Human-Centered Design: We build for the people using the tool, ensuring adoption and sustainable growth.\n"
    "• Ethical Governance: Accountability is the bedrock of our data and modeling practices.\n"
    "• Operational Excellence: We deliver high-performance, scalable systems that respect your ROI goals.\n\n"
    "We're documenting our journey in enterprise AI. Follow our page for insights on balancing "
    "rapid innovation with responsible, human-first growth. https://neuzenai.com/"
)

CAMPAIGN_BRIEF = (
    "Position NeuzenAI as an enterprise AI partner that bridges experimentation to "
    "operationalization. Emphasize the four pillars: innovation, human-centered design, "
    "ethical governance, operational excellence. Audience: enterprise leaders, CTOs, "
    "Heads of AI. Tone: authoritative, confident, human-centric."
)

PRIMARY_BRAND_COLOR = "#ff4500"
ASPECT_RATIO = "1:1"

# Build a single-platform content dict so the magic pipeline picks LinkedIn first.
# Use the SAME post_text for all 3 variants — the user wants to see how Agent 1
# interprets THIS specific brief; we're not running them through the multi-variant
# content agent for this test.
CONTENT_DICT = {
    "linkedin": {
        "viral_reach": POST_TEXT,
    }
}

# Fetch the logo
LOGO_URL = "https://neuzenai.com/logo.png"
print(f"→ Fetching logo from {LOGO_URL}", flush=True)
logo_bytes = requests.get(LOGO_URL, timeout=10).content
print(f"✓ Logo fetched: {len(logo_bytes):,} bytes", flush=True)

# Run the pipeline (just viral_reach variant — single image)
print("\n→ Running magic image pipeline (GPT-5 → gpt-image-2 high)…", flush=True)
results = run_magic_image_pipeline(
    campaign_brief=CAMPAIGN_BRIEF,
    content_dict=CONTENT_DICT,
    selected_platforms=["linkedin"],
    primary_brand_color=PRIMARY_BRAND_COLOR,
    aspect_ratio=ASPECT_RATIO,
    logo_bytes=logo_bytes,
    business_dna_label="NeuzenAI",
)

# Print the outputs
out_file = Path(__file__).resolve().parent.parent / "_one_shot_test_output.txt"
with out_file.open("w", encoding="utf-8") as f:
    for r in results:
        f.write("=" * 80 + "\n")
        f.write(f"variant={r['variant_type']}\n")
        f.write(f"platform_used={r['platform_used']}\n")
        f.write(f"S3 URL: {r['url']}\n\n")
        f.write("--- MAGIC PROMPT (Agent 1's output, sent to gpt-image-2) ---\n")
        f.write(r["magic_prompt"] + "\n\n")

print(f"\n✓ Wrote results to {out_file}")
print(f"\nGenerated {len(results)} image(s):")
for r in results:
    print(f"  {r['variant_type']:20s} → {r['url']}")
