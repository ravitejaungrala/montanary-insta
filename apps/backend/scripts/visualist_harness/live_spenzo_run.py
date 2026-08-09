"""Live end-to-end agent-post run for Spenzo AI, saving images to disk.

Bypasses S3 upload so the outputs land in scripts/visualist_harness/out/live/
where we can review + iterate without burning a real dashboard campaign.

Usage:
    cd apps/backend
    python scripts/visualist_harness/live_spenzo_run.py "<your campaign brief>"

If no brief is passed, a default Spenzo webinar brief is used.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).parent
BACKEND_DIR = HERE.parents[1]
sys.path.insert(0, str(BACKEND_DIR))

OUT_DIR = HERE / "out" / "live"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------------
# Monkey-patch S3 upload to save bytes locally instead.
# Must happen BEFORE importing ai_service so the patched symbol is used.
# -------------------------------------------------------------------------
def _local_save_jpeg(data: bytes, key: str) -> str:
    """Replaces services.ai_service._upload_jpeg during this run."""
    # Flatten the S3 key into a local filename
    local_name = key.replace("/", "__")
    local_path = OUT_DIR / local_name
    local_path.write_bytes(data)
    return f"file://{local_path.as_posix()}"


from core.database import SessionLocal  # noqa: E402
from models import User  # noqa: E402
from services import ai_service  # noqa: E402


def main(brief: str, product_name: str | None = "Spenzo AI") -> None:
    # Swap S3 upload for local save.
    # `_upload_jpeg` is defined INSIDE `_generate_visuals_v2`, but the
    # upload loop uses `pool.submit(_upload_jpeg, ...)` so we need a
    # different hook. Easiest: patch the module's `get_s3_client` so any
    # call becomes a no-op, then also override `_upload_jpeg` behaviour by
    # replacing the ThreadPoolExecutor.submit with a closure — too hairy.
    #
    # Simpler path: override `services.ai_service.get_s3_client` to a
    # stub with a mocked `put_object` + `generate_presigned_url`.
    class _LocalS3Stub:
        def put_object(self, Bucket, Key, Body, ContentType="image/jpeg"):
            local_name = Key.replace("/", "__")
            (OUT_DIR / local_name).write_bytes(Body)
            return {}
        def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None):
            local_name = Key.replace("/", "__")
            Fileobj.seek(0)
            (OUT_DIR / local_name).write_bytes(Fileobj.read())
            return None
        def generate_presigned_url(self, op, Params, ExpiresIn=3600):
            local_name = Params["Key"].replace("/", "__")
            return f"file://{(OUT_DIR / local_name).as_posix()}"

    ai_service.get_s3_client = lambda: _LocalS3Stub()
    # get_s3_url calls generate_presigned_url on the real client; override
    # to return a file:// URL based purely on the key.
    ai_service.get_s3_url = lambda key: f"file://{(OUT_DIR / key.replace('/', '__')).as_posix()}"

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == 1).first()
        if not user:
            print("User id=1 not found.")
            return
        print(f"Running as: {user.email}")
        print(f"Company:    {(user.business_dna or {}).get('company_name')}")
        print(f"Product:    {product_name!r}")
        print(f"Brief:      {brief[:120]}{'...' if len(brief) > 120 else ''}")
        print()

        # Pull the logo bytes from the product DNA so the render matches
        # what the live dashboard would use.
        dna = user.business_dna or {}
        products = dna.get("products") or {}
        product = products.get(product_name) if product_name else None
        logo_url = (product or {}).get("logo_url") or dna.get("logo_url")
        logo_bytes = None
        if logo_url and logo_url.startswith("http"):
            try:
                import requests
                r = requests.get(logo_url, timeout=10)
                if r.status_code == 200:
                    logo_bytes = r.content
                    print(f"Logo:       {logo_url} ({len(logo_bytes)} bytes)")
            except Exception as e:
                print(f"Logo fetch failed: {e}")
        else:
            print("Logo:       (none in DNA, will use placeholder)")
        print()

        primary_color = ((product or {}).get("primary_color")
                         or dna.get("primary_color") or "#FF5722")
        print(f"Primary:    {primary_color}")
        print()
        print("Running generate_strategic_content (may take 30-60s)...")
        print("-" * 60)

        data = ai_service.generate_strategic_content(
            campaign_brief=brief,
            platforms=["linkedin"],
            user=user,
            logo_bytes=logo_bytes,
            extra_context={"aspect_ratio": "1:1", "product_name": product_name},
            product_name=product_name,
            post_type="image",
        )

        print("-" * 60)
        print("DONE.")
        print()
        meta = data.get("visualist_meta") or {}
        overlay = meta.get("overlay_copy") or {}
        camp = meta.get("campaign_analysis") or {}
        print("Campaign analysis:")
        print(f"  product_name:    {camp.get('product_name')!r}")
        print(f"  product_tagline: {camp.get('product_tagline')!r}")
        print(f"  audience:        {camp.get('audience')!r}")
        print(f"  campaign_moment: {camp.get('campaign_moment')!r}")
        print()
        print("Overlay copy:")
        print(f"  headline:   {overlay.get('headline')!r}")
        print(f"  subheading: {overlay.get('subheading')!r}")
        print(f"  cta:        {overlay.get('cta')!r}")
        print()

        variants = data.get("visual_variants") or data.get("visuals") or []
        print(f"Visual variants: {len(variants)} backgrounds")
        for v in variants:
            bg_i = v.get("background_index")
            scene = v.get("scene_type")
            templates = v.get("templates") or {}
            print(f"  bg {bg_i}  scene={scene!r}  templates={list(templates.keys())}")
        print()
        print(f"All assets saved under: {OUT_DIR}")
        print("Listing:")
        for p in sorted(OUT_DIR.glob("*.jpg")):
            print(f"  {p.name}  ({p.stat().st_size} bytes)")

    finally:
        db.close()


if __name__ == "__main__":
    default_brief = (
        "Announce Spenzo AI's upcoming live webinar: 'From Reports to "
        "Answers — Conversational Marketing Mix Modeling'. Audience: "
        "CMOs and marketing analysts at mid-to-large B2C brands who "
        "struggle with slow, technical MMM tooling. Angle: Spenzo lets "
        "you just ask questions in plain English and get decisions, not "
        "dashboards. Tone: confident, editorial, business-class. Goal: "
        "drive registrations."
    )
    brief = sys.argv[1] if len(sys.argv) > 1 else default_brief
    main(brief, product_name="Spenzo AI")
