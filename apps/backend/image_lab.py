"""Image Lab — iterate on the visualist V2 prompt.

Runs the SAME pipeline as production (refiner → visualist V2 → image gen) on a
single brief, dumps every artifact (prompts, JSON, images) to a labeled folder
so you can diff runs and tune the prompt until output is acceptable.

Usage:
    cd apps/backend
    python image_lab.py --brief "announce Spenzo budget planning v2 with TikTok Ads"
    python image_lab.py --brief "..." --user contact@neuzenai.com --aspect 9:16
    python image_lab.py --brief "..." --skip-images        # text-only fast iteration
    python image_lab.py --brief "..." --no-refine          # skip refiner, raw brief in
    python image_lab.py --brief "..." --label run-after-tone-tweak
    python image_lab.py --brief "..." --runs 3             # 3 runs back-to-back

Output structure:
    apps/backend/image_lab_out/
        spenzo_2026-05-04_15-30-21_run-1/
            01_user_context.txt
            02_raw_brief.txt
            03_refined_brief.txt
            04_visualist_output.json
            05_overlay_copy.md         <- headline / sub / CTA in a table
            06_image_prompts.md
            bg_0_<scene_type>.png
            bg_1_<scene_type>.png
            bg_2_<scene_type>.png
            SUMMARY.md
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
load_dotenv()

# Make `services.*` importable from this script run.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sqlalchemy import create_engine, text
from services.ai_service import (
    _build_user_context,
    _refine_brief_agent,
    _visualist_v2_agent,
    _gen_background_with_critic,
    _dna_product_name_tagline,
)

OUT_BASE = HERE / "image_lab_out"
OUT_BASE.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# DB load — reconstruct enough of a User object for _build_user_context
# ---------------------------------------------------------------------------

def _load_user_obj(email: str) -> SimpleNamespace:
    """Pull the user row from prod DB and return a duck-typed object that
    looks enough like a SQLAlchemy User to keep _build_user_context happy."""
    eng = create_engine(os.environ["DATABASE_URL"])
    with eng.connect() as c:
        row = c.execute(text("""
            SELECT id, email, company_name, business_url, business_dna,
                   product_url, product_details
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
        id=row.id,
        email=row.email,
        company_name=row.company_name,
        business_url=row.business_url,
        business_dna=dna or {},
        product_url=row.product_url,
        product_details=row.product_details,
    )


# ---------------------------------------------------------------------------
# Pretty rendering helpers
# ---------------------------------------------------------------------------

def _word_count(s: str) -> int:
    return len([w for w in (s or "").split() if w.strip()])


def _render_overlay_md(visualist_out: dict, primary_color: str = "", aspect_ratio: str = "") -> str:
    overlay = visualist_out.get("overlay_copy") or {}
    ca = visualist_out.get("campaign_analysis") or {}
    # `brand` block was removed from visualist output (was just echoing the
    # backend's own input values). Lab now displays the actual values used.
    brand = {"primary_color": primary_color, "aspect_ratio": aspect_ratio}

    h = overlay.get("headline", "")
    s = overlay.get("subheading", "")
    cta = overlay.get("cta", "")

    # Validation flags so we can spot issues at a glance
    flags = []
    h_words = _word_count(h)
    if not (4 <= h_words <= 7):
        flags.append(f"Headline word count {h_words} outside 4-7")
    if h.endswith((".", "!", "?")):
        flags.append("Headline has trailing punctuation")
    s_words = _word_count(s)
    if not (8 <= s_words <= 15):
        flags.append(f"Subheading word count {s_words} outside 8-15")
    cta_words = _word_count(cta)
    if not (3 <= cta_words <= 5):
        flags.append(f"CTA word count {cta_words} outside 3-5")
    BANNED_CTA = {"learn more", "click here", "read more", "find out more",
                  "submit", "get started", "more info", "more details"}
    if cta.lower().strip() in BANNED_CTA:
        flags.append(f"CTA hits banned list: '{cta}'")
    for fld_name, fld in [("headline", h), ("subheading", s), ("cta", cta)]:
        if "{{" in fld or "}}" in fld:
            flags.append(f"{fld_name} leaks literal placeholder")

    out = []
    out.append("# Overlay Copy\n")
    out.append("| Field | Value | Word count |")
    out.append("|---|---|---|")
    out.append(f"| **Headline**   | {h or '_<empty>_'} | {h_words} |")
    out.append(f"| **Subheading** | {s or '_<empty>_'} | {s_words} |")
    out.append(f"| **CTA**        | {cta or '_<empty>_'} | {cta_words} |")
    out.append("")
    if flags:
        out.append("## ⚠️ Validation flags")
        for f in flags:
            out.append(f"- {f}")
        out.append("")
    else:
        out.append("✅ Passes all validation rules")
        out.append("")

    out.append("# Campaign Analysis\n")
    for k, v in ca.items():
        out.append(f"- **{k}**: {v}")
    out.append("")
    out.append("# Run inputs (NOT from LLM)\n")
    for k, v in brand.items():
        out.append(f"- **{k}**: {v}")
    return "\n".join(out)


def _render_prompts_md(image_prompts: list) -> str:
    out = ["# Image Prompts\n"]
    for i, p in enumerate(image_prompts):
        scene = p.get("scene_type", "unknown")
        text = p.get("prompt", "")
        out.append(f"## {i + 1}. {scene}\n")
        out.append(text)
        out.append("\n---\n")
    return "\n".join(out)


def _render_summary_md(args, user_obj, visualist_out, image_results, run_dir) -> str:
    overlay = visualist_out.get("overlay_copy") or {}
    out = []
    out.append(f"# Image Lab Run — {run_dir.name}\n")
    out.append(f"- **Time**: {_dt.datetime.now().isoformat()}")
    out.append(f"- **User**: {user_obj.email}  (company: {user_obj.company_name})")
    out.append(f"- **Aspect ratio**: {args.aspect}")
    out.append(f"- **Refiner**: {'skipped' if args.no_refine else 'ran'}")
    out.append(f"- **Brief**: {args.brief!r}")
    out.append("")
    out.append("## Overlay Copy\n")
    out.append(f"- Headline: **{overlay.get('headline', '')}**")
    out.append(f"- Subheading: {overlay.get('subheading', '')}")
    out.append(f"- CTA: **{overlay.get('cta', '')}**")
    out.append("")
    if image_results:
        out.append("## Images\n")
        out.append("| # | scene_type | rating | attempts | file |")
        out.append("|---|---|---|---|---|")
        for r in image_results:
            ok = "✅" if r["bytes_ok"] else "❌"
            out.append(
                f"| {r['idx']} | {r['scene_type']} | {r['rating']} | "
                f"{r['attempts']} | {ok} {r['filename'] or '-'} |"
            )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Image generation orchestration
# ---------------------------------------------------------------------------

def _gen_one(idx: int, p: dict, refined_brief: str, primary_color: str,
             aspect_ratio: str, run_dir: Path) -> dict:
    scene = p.get("scene_type", "unknown")
    prompt = p.get("prompt", "")
    safe_scene = "".join(ch if ch.isalnum() else "_" for ch in scene)
    fname = f"bg_{idx}_{safe_scene}.png"
    try:
        bytes_, rating, attempts = _gen_background_with_critic(
            prompt=prompt,
            refined_brief=refined_brief,
            primary_color=primary_color,
            aspect_ratio=aspect_ratio,
        )
        if bytes_:
            (run_dir / fname).write_bytes(bytes_)
            return {
                "idx": idx, "scene_type": scene, "rating": rating,
                "attempts": attempts, "filename": fname, "bytes_ok": True,
                "error": None,
            }
        return {
            "idx": idx, "scene_type": scene, "rating": rating,
            "attempts": attempts, "filename": None, "bytes_ok": False,
            "error": "no bytes returned",
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "idx": idx, "scene_type": scene, "rating": 0, "attempts": 0,
            "filename": None, "bytes_ok": False, "error": str(e),
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_once(args, user_obj, run_label: str) -> Path:
    run_dir = OUT_BASE / run_label
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Run: {run_label} ===")
    print(f"Output dir: {run_dir}")

    # 1. Build user_context exactly like prod
    # _build_user_context returns (user_context, domain_name, primary_color)
    product_name = getattr(args, "product", None) or None
    user_context, domain_name, primary_color = _build_user_context(user_obj, product_name=product_name)
    (run_dir / "01_user_context.txt").write_text(user_context, encoding="utf-8")
    print(f"User: {user_obj.email}   primary_color={primary_color}   domain={domain_name}")

    # 2. Save raw brief
    (run_dir / "02_raw_brief.txt").write_text(args.brief, encoding="utf-8")

    # 3. Refiner
    if args.no_refine:
        refined_brief = args.brief
        print("(refiner skipped — using raw brief)")
    else:
        print("Running refiner...")
        try:
            ref_out = _refine_brief_agent(args.brief, user_context)
            if isinstance(ref_out, dict):
                refined_brief = ref_out.get("refined_brief") or args.brief
            else:
                refined_brief = str(ref_out)
        except Exception as e:
            print(f"Refiner failed ({e}); falling back to raw brief")
            refined_brief = args.brief
        (run_dir / "03_refined_brief.txt").write_text(refined_brief, encoding="utf-8")
        print(f"Refined brief saved ({len(refined_brief)} chars)")

    # 4. Visualist V2 (raw LLM output)
    print(f"Running visualist V2 (aspect={args.aspect})...")
    visualist_out = _visualist_v2_agent(
        refined_brief=refined_brief,
        user_context=user_context,
        primary_color=primary_color,
        aspect_ratio=args.aspect,
    )

    # 4b. DNA overrides — same as production. The LLM may have extracted the
    # campaign subject as product_name/tagline (e.g. "Pulse module") instead
    # of the actual brand product (e.g. "Spenzo AI"). _dna_product_name_tagline
    # pulls the authoritative values from the user's DNA.
    dna_name, dna_tagline = _dna_product_name_tagline(user_obj, product_name=product_name)
    if dna_name or dna_tagline:
        visualist_out.setdefault("dna_overrides", {})
        if dna_name:
            visualist_out["dna_overrides"]["product_name"] = dna_name
        if dna_tagline:
            visualist_out["dna_overrides"]["product_tagline"] = dna_tagline
        print(f"DNA overrides applied — product_name={dna_name!r}  tagline={dna_tagline!r}")

    (run_dir / "04_visualist_output.json").write_text(
        json.dumps(visualist_out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4c. Compute the FINAL post-override values that the compositor will use,
    # so the lab report matches the actual on-screen rendering.
    ca = visualist_out.get("campaign_analysis") or {}
    overrides = visualist_out.get("dna_overrides") or {}
    final_product_name = overrides.get("product_name") or ca.get("product_name") or ""
    final_product_tagline = overrides.get("product_tagline") or ca.get("product_tagline") or ""
    print(f"\n--- FINAL VALUES (after DNA override) ---")
    print(f"  product_name:    {final_product_name!r}")
    print(f"  product_tagline: {final_product_tagline!r}")

    # 5/6. Render overlay + prompt markdown
    overlay_md = _render_overlay_md(visualist_out, primary_color, args.aspect)
    (run_dir / "05_overlay_copy.md").write_text(overlay_md, encoding="utf-8")

    image_prompts = visualist_out.get("image_prompts") or []
    prompts_md = _render_prompts_md(image_prompts)
    (run_dir / "06_image_prompts.md").write_text(prompts_md, encoding="utf-8")

    # Console preview
    overlay = visualist_out.get("overlay_copy") or {}
    print("\n--- OVERLAY COPY ---")
    print(f"  Headline:   {overlay.get('headline', '')!r}  ({_word_count(overlay.get('headline', ''))} words)")
    print(f"  Subheading: {overlay.get('subheading', '')!r}  ({_word_count(overlay.get('subheading', ''))} words)")
    print(f"  CTA:        {overlay.get('cta', '')!r}  ({_word_count(overlay.get('cta', ''))} words)")
    print(f"\n--- IMAGE PROMPTS ({len(image_prompts)}) ---")
    for i, p in enumerate(image_prompts):
        head = (p.get("prompt") or "").split("\n", 1)[0][:120]
        print(f"  {i}. [{p.get('scene_type', '?')}] {head}...")

    # 7. Image generation
    image_results = []
    if not args.skip_images and image_prompts:
        print(f"\nGenerating {len(image_prompts)} images in parallel...")
        with ThreadPoolExecutor(max_workers=3) as exe:
            futures = {
                exe.submit(_gen_one, i, p, refined_brief, primary_color, args.aspect, run_dir): i
                for i, p in enumerate(image_prompts)
            }
            for fut in as_completed(futures):
                r = fut.result()
                image_results.append(r)
        image_results.sort(key=lambda r: r["idx"])
        for r in image_results:
            mark = "[OK]" if r["bytes_ok"] else "[FAIL]"
            err = f"  ERROR: {r['error']}" if r["error"] else ""
            print(f"  {mark} bg_{r['idx']} [{r['scene_type']}] rating={r['rating']}/10 attempts={r['attempts']}{err}")
    elif args.skip_images:
        print("\n(--skip-images set; no images generated)")

    # 8. Summary
    summary = _render_summary_md(args, user_obj, visualist_out, image_results, run_dir)
    (run_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
    return run_dir


def main():
    parser = argparse.ArgumentParser(description="Iterate on visualist V2 prompts + image gen.")
    parser.add_argument("--brief", required=True, help="Campaign brief text")
    parser.add_argument("--user", default="shaiksalmanisha@gmail.com",
                        help="User email whose Business DNA to use (default: Spenzo)")
    parser.add_argument("--aspect", default="1:1", choices=["1:1", "9:16", "16:9"])
    parser.add_argument("--product", default=None,
                        help="Optional sub-product name within the user's business_dna.products (e.g. 'Zyntegrate')")
    parser.add_argument("--skip-images", action="store_true",
                        help="Skip image generation, just headline/sub/CTA + prompts")
    parser.add_argument("--no-refine", action="store_true",
                        help="Skip brief refiner, pass raw brief directly to visualist")
    parser.add_argument("--label", default=None,
                        help="Run label for output dir (default: timestamp)")
    parser.add_argument("--runs", type=int, default=1,
                        help="How many times to run the same brief back-to-back (for variability check)")
    args = parser.parse_args()

    user_obj = _load_user_obj(args.user)
    print(f"\nUser loaded: {user_obj.email}  (company={user_obj.company_name})")
    print(f"DNA keys: {list((user_obj.business_dna or {}).keys())[:12]}")

    base_label = args.label or _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dirs = []
    for i in range(args.runs):
        label = base_label if args.runs == 1 else f"{base_label}_run-{i+1}"
        run_dirs.append(_run_once(args, user_obj, label))

    print("\n=== ALL RUNS DONE ===")
    for d in run_dirs:
        print(f"  {d}")


if __name__ == "__main__":
    main()
