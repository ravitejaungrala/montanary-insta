"""Direct gpt-image-2 test — pass the user's prompt verbatim, no Agent 1.

This bypasses the magic prompt generator entirely and sends the user-supplied
text + logo directly to gpt-image-2 high quality. Useful for testing what
gpt-image-2 produces "raw" from a casually-written brief.

Usage:
    venv/Scripts/python.exe scripts/test_direct_gpt_image.py
"""
import base64
import os
import sys
import time
from io import BytesIO
from pathlib import Path

# Ensure repo backend root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env so OPENAI_API_KEY is available
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests
from PIL import Image as PILImage
from openai import OpenAI

from core.s3_utils import get_s3_client, get_s3_url, S3_BUCKET_NAME

# ── The exact prompt the user provided ────────────────────────────────
DIRECT_PROMPT = """Read the post content below carefully and figure out from the wording

whether NeuzenAI is selling a product, a service, or something else.
Then pick a visual style that fits this kind of content.

Post content: "The era of 'AI experimentation' is closing. The era of 'AI operationalization' is here.
For enterprise leaders, the challenge is no longer about finding a model that works—it's about finding a partner that understands how to integrate AI into a human-centric, ethical ecosystem.
At NeuZenAI, we've built our foundation on four pillars:

• Innovation First: We don't just follow trends; we engineer solutions that move the needle.

• Human-Centered Design: We build for the people using the tool, ensuring adoption and sustainable growth.

• Ethical Governance: Accountability is the bedrock of our data and modeling practices.

• Operational Excellence: We deliver high-performance, scalable systems that respect your ROI goals.

We're documenting our journey in enterprise AI. Follow our page for insights on balancing rapid innovation with responsible, human-first growth. https://neuzenai.com/"

Hard rules:
- Place the attached logo in the TOP-LEFT corner, used exactly as provided
- Place a clear call-to-action button in the BOTTOM-LEFT or BOTTOM-RIGHT corner
- Use brand color #ff4500 tastefully
- Aspect ratio: 1:1


Everything else — composition, imagery, typography, mood — is your choice.

Make it look like a top-quality post a real designer would ship.
"""

LOGO_URL = "https://neuzenai.com/logo.png"
MODEL    = "gpt-image-2"
QUALITY  = "high"
SIZE     = "1024x1024"

# ── Fetch + normalize the logo to PNG (gpt-image-2 rejects octet-stream) ──
print(f"-> Fetching logo from {LOGO_URL}", flush=True)
resp = requests.get(LOGO_URL, timeout=10)
resp.raise_for_status()
print(f"OK Logo: {len(resp.content):,} bytes (content-type: {resp.headers.get('content-type')})", flush=True)

img = PILImage.open(BytesIO(resp.content))
if img.mode not in ("RGB", "RGBA", "L"):
    img = img.convert("RGBA")
logo_buf = BytesIO()
img.save(logo_buf, format="PNG")
logo_buf.seek(0)
logo_upload = ("logo.png", logo_buf, "image/png")

# ── Call gpt-image-2 directly ──
print(f"\n-> Calling {MODEL} (quality={QUALITY}, size={SIZE})…", flush=True)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

t0 = time.monotonic()
api_resp = client.images.edit(
    model=MODEL,
    image=[logo_upload],
    prompt=DIRECT_PROMPT,
    quality=QUALITY,
    size=SIZE,
)
elapsed = round(time.monotonic() - t0, 2)
print(f"OK Image generated in {elapsed}s", flush=True)

# ── Save locally + upload to S3 ──
png_bytes = base64.b64decode(api_resp.data[0].b64_json)
local_path = Path(__file__).resolve().parent.parent / "neuzenai_direct_gpt_image.png"
local_path.write_bytes(png_bytes)
print(f"OK Saved {len(png_bytes):,} bytes to {local_path}", flush=True)

s3 = get_s3_client()
if s3 and S3_BUCKET_NAME:
    import uuid
    key = f"ai_gen/direct_test/neuzenai_{uuid.uuid4().hex}.png"
    s3.upload_fileobj(
        BytesIO(png_bytes),
        S3_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )
    s3_url = get_s3_url(key)
    print(f"\nS3 URL: {s3_url}")
else:
    print("WARN: S3 not configured -- image saved locally only")

print("\nDone.")
