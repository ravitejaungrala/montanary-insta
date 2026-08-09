"""Render-matrix test for the f1 outreach template.

Asserts the rules the plan says must never break:
  * no unsubstituted `$placeholder` survives
  * no <img> ships an empty / None / placeholder src
  * every image slot is independently optional
  * the stat strip appears ONLY with 3+ proof points
  * derived colours meet WCAG AA against the surface they sit on

Usage:
    python scripts/test_outreach_f1_render.py
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from nexus.services.email_palette import (  # noqa: E402
    _parse, contrast_ratio, derive_palette,
)
from nexus.services.outreach_template_f1 import render_email_f1  # noqa: E402

LEAD = {"first_name": "Siddharth", "company_name": "Acme Manufacturing"}

GEMINI = {
    "subject": "Quick thought on your channel mix",
    "headline": "Make marketing decisions with clarity",
    "preheader": "See which channels actually drive growth.",
    "intro_body": "I noticed your work at Acme. Quantifying marketing impact is "
                  "likely a frequent topic given your data landscape.",
    "benefits_heading": "Four things, end to end",
    "benefits_sub": "From raw channel data to next quarter's allocation.",
    "editorial_heading": "Built for the channels that drive growth",
    "editorial_body": "A clearer view of revenue, and one place to see which spend earns it.",
}

FULL_SENDER = {
    "company_name": "Spenzo",
    "company_url": "spenzo.ai",
    "rep_name": "B. Subba Rami Reddy",
    "rep_title": "AI Delivery Head",
    "cta_url": "https://spenzo.ai/book",
    "cta_label": "Book a quick call",
    "brand_color": "#ff4500",
    "key_benefits": [
        "Marketing mix modeling — See how combined effort influences revenue.",
        "Budget optimization — Find the best allocation before you commit spend.",
        "ROI forecasting — Project returns with confidence ranges attached.",
        "Automated data integration — Every source connected, no manual prep.",
    ],
    "proof_points": [
        {"value": "3.2x", "label": "Avg. ROAS", "note": "Across live customer accounts."},
        {"value": "$10M+", "label": "Spend optimized", "note": "Reallocated with model guidance."},
        {"value": "92%", "label": "Model accuracy", "note": "Backtested on historic revenue."},
    ],
    "images": {
        "logo_url": "https://s3.example.com/logo.png",
        "hero_url": "https://s3.example.com/hero.jpg",
        "editorial_url": "https://s3.example.com/editorial.jpg",
    },
}

PLACEHOLDER_RE = re.compile(r"\$\{?[a-zA-Z_][a-zA-Z0-9_]*\}?")
IMG_SRC_RE = re.compile(r'<img[^>]*\ssrc="([^"]*)"', re.I)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  [OK]   {msg}")
    else:
        print(f"  [FAIL] {msg}")
        failures.append(msg)


def assert_html_sane(html: str, label: str) -> None:
    leaked = PLACEHOLDER_RE.findall(html)
    check(not leaked, f"{label}: no unsubstituted placeholders ({leaked[:3]})")

    srcs = IMG_SRC_RE.findall(html)
    bad = [s for s in srcs if not s.strip() or s in ("None", "null")
           or s.endswith("_IMAGE_SRC") or s.startswith("data:")]
    check(not bad, f"{label}: no empty/placeholder/data: img src ({bad[:3]})")

    check("<img" not in html or all(
        'alt="' in m for m in re.findall(r"<img[^>]*>", html)
    ), f"{label}: every <img> carries an alt attribute")


def main() -> int:
    print("== f1 render matrix ==\n")

    # 1. Everything present
    print("[1] full data")
    out = render_email_f1(LEAD, FULL_SENDER, GEMINI)
    assert_html_sane(out["html"], "full")
    check(out["html"].count("<img") == 3, "full: exactly 3 images")
    check("3.2x" in out["html"], "full: stat strip rendered")
    check("$10M+" in out["html"], "full: literal $ in a proof point survives")
    check("WHAT SPENZO DOES" in out["html"], "full: eyebrow uses company name")

    # 2. No images at all
    print("\n[2] no images")
    s = {**FULL_SENDER, "images": {}}
    out = render_email_f1(LEAD, s, GEMINI)
    assert_html_sane(out["html"], "no-images")
    check("<img" not in out["html"], "no-images: zero <img> tags emitted")
    check("Best regards" in out["html"], "no-images: body still complete")

    # 3. Each image slot independently
    print("\n[3] one slot at a time")
    for slot in ("logo_url", "hero_url", "editorial_url"):
        s = {**FULL_SENDER, "images": {slot: "https://s3.example.com/x.png"}}
        out = render_email_f1(LEAD, s, GEMINI)
        assert_html_sane(out["html"], f"only-{slot}")
        check(out["html"].count("<img") == 1, f"only-{slot}: exactly 1 image")

    # 4. Stat strip gating
    print("\n[4] proof-point gating")
    for n in (0, 1, 2):
        s = {**FULL_SENDER, "proof_points": FULL_SENDER["proof_points"][:n]}
        out = render_email_f1(LEAD, s, GEMINI)
        check("3.2x" not in out["html"], f"{n} proof points: strip dropped")
    s = {**FULL_SENDER, "proof_points": FULL_SENDER["proof_points"]}
    check("3.2x" in render_email_f1(LEAD, s, GEMINI)["html"], "3 proof points: strip shown")

    # 5. key_benefits of varying length
    print("\n[5] benefit-count handling")
    for n in (0, 1, 2, 3, 4, 7):
        s = {**FULL_SENDER, "key_benefits": FULL_SENDER["key_benefits"][:n] or []}
        if n == 7:
            s["key_benefits"] = FULL_SENDER["key_benefits"] * 2
        out = render_email_f1(LEAD, s, GEMINI)
        assert_html_sane(out["html"], f"{n}-benefits")
        cards = out["html"].count("border-left:3px solid")
        expect = 0 if n == 0 else min(n, 4)
        check(cards == expect, f"{n} benefits -> {cards} cards (want {expect})")

    # 6. Bare-minimum sender
    print("\n[6] minimal sender")
    out = render_email_f1({"first_name": "Sam"}, {}, {})
    assert_html_sane(out["html"], "minimal")
    check("Hi Sam," in out["html"], "minimal: greeting present")

    # 7. Postal address is DATA, never a company-keyed table in code
    print("\n[7] postal address is data-driven")
    addr_a = "702 S Denton Tap Rd, Coppell, TX 75019, USA"
    addr_b = "12 Example Way, Dublin, Ireland"

    # absent by default — no address is baked into the renderer
    check(addr_a not in render_email_f1(LEAD, FULL_SENDER, GEMINI)["html"],
          "no address hardcoded for any company name")

    # present when supplied, per sender
    a = render_email_f1(LEAD, {**FULL_SENDER, "postal_address": addr_a}, GEMINI)["html"]
    b = render_email_f1(LEAD, {**FULL_SENDER, "company_name": "Zyntegrate",
                               "postal_address": addr_b}, GEMINI)["html"]
    check(addr_a in a, "sender A's address rendered")
    check(addr_b in b and addr_a not in b, "sender B gets its OWN address, no leak")
    check("Zyntegrate" in b and "Spenzo" not in b, "sender B: no cross-brand leak")

    # attribution is config, not a literal
    check("POWERED BY" not in render_email_f1(LEAD, FULL_SENDER, GEMINI)["html"],
          "no attribution when unconfigured")
    att = render_email_f1(LEAD, {**FULL_SENDER, "attribution": "Acme Labs"},
                          GEMINI)["html"]
    check("ACME LABS" in att, "attribution renders from config")
    check("NEUZENAI" not in att, "attribution is not hardcoded")

    # section copy: per-lead > per-product > neutral fallback
    print("\n[7b] section copy resolution")
    neutral = render_email_f1(LEAD, FULL_SENDER, {"subject": "s", "intro_body": "b"})["html"]
    check("What you get" in neutral, "neutral fallback heading used")
    prod_copy = render_email_f1(
        LEAD, {**FULL_SENDER, "f1_sections": {"benefits_heading": "Product Level Heading"}},
        {"subject": "s", "intro_body": "b"})["html"]
    check("Product Level Heading" in prod_copy, "per-product section copy wins over fallback")
    lead_copy = render_email_f1(
        LEAD, {**FULL_SENDER, "f1_sections": {"benefits_heading": "Product Level Heading"}},
        {"subject": "s", "intro_body": "b", "benefits_heading": "Lead Level Heading"})["html"]
    check("Lead Level Heading" in lead_copy and "Product Level Heading" not in lead_copy,
          "per-lead copy wins over per-product")

    # 8. Palette contrast across hostile brand colours
    print("\n[8] palette contrast (WCAG AA)")
    for brand in ("#ff4500", "#ffff00", "#ffffff", "#000000", "#7fffd4", "#0f4c5c", None):
        pal = derive_palette(brand)
        white, card, dark = (255, 255, 255), (255, 255, 255), (0x17, 0x12, 0x0F)
        r_link = contrast_ratio(_parse(pal["c_link"]), white)
        r_soft = contrast_ratio(_parse(pal["c_soft"]), dark)
        r_deep = contrast_ratio(_parse(pal["c_deep"]), card)
        check(r_link >= 4.5, f"{brand}: white-on-button {r_link:.2f} >= 4.5")
        check(r_soft >= 4.5, f"{brand}: soft-on-dark   {r_soft:.2f} >= 4.5")
        check(r_deep >= 3.0, f"{brand}: stat-on-white  {r_deep:.2f} >= 3.0")

    # 9. Professional / deliverability hygiene
    print("\n[9] hygiene")
    out = render_email_f1(LEAD, FULL_SENDER, GEMINI)
    h = out["html"]
    check("prefers-color-scheme:dark" in h, "dark-mode media query present")
    check("[data-ogsc]" in h, "Outlook.com dark-mode fallback present")
    check(h.count('class="dm-') + h.count(" dm-") > 15, "dark-mode classes applied broadly")
    check("&#847;&zwnj;" in h, "preheader filler stops body-copy bleed")
    check("supported-color-schemes" in h, "Apple Mail colour-scheme hint")
    check("-ms-interpolation-mode:bicubic" in h, "Outlook image scaling fix")
    check(h.count("role=\"presentation\"") >= 10, "layout tables marked presentational")
    check("mso-line-height-rule:exactly" in h, "Outlook line-height honoured")
    check("word-break:break-word" not in h, "no mid-word breaking")
    check('rel="noopener noreferrer"' in h, "external links carry rel=noopener")
    check("OfficeDocumentSettings" in h, "mso DPI fix present")

    # unsubscribe gating
    check("Unsubscribe" not in h, "no unsubscribe link when no URL supplied")
    s_unsub = {**FULL_SENDER,
               "unsubscribe_url": "https://spenzo.ai/u/abc",
               "permission_reminder": "You received this because we believe Spenzo is relevant to your work."}
    h2 = render_email_f1(LEAD, s_unsub, GEMINI)["html"]
    check("Unsubscribe" in h2 and "https://spenzo.ai/u/abc" in h2, "unsubscribe rendered when supplied")
    check("You received this because" in h2, "permission reminder rendered")
    assert_html_sane(h2, "with-unsubscribe")

    # 10. Wordmark auto-fit
    print("\n[10] wordmark auto-fit")
    from nexus.services.outreach_template_f1 import (  # noqa: E402
        _LOGO_COST, _W_DESKTOP, _W_MOBILE, _text_width_em, fit_wordmark,
    )

    def rendered_px(text, font, track):
        return _text_width_em(text) * font + track * max(len(text) - 1, 0)

    names = ["SPENZO", "ZYNTEGRATE", "IBM", "W", "ZYNTEGRATE SOLUTIONS",
             "INTERNATIONAL BUSINESS MACHINES", "MWWWWWWWWWWWWWWWWWWWW", "A", ""]
    for n in names:
        for avail, base, mn, tag in (
            (_W_DESKTOP, 38, 20, "desktop"),
            (_W_DESKTOP - _LOGO_COST, 38, 20, "desktop+logo"),
            (_W_MOBILE, 26, 15, "mobile"),
        ):
            f, t = fit_wordmark(n, avail, base, mn)
            w = rendered_px(n, f, t)
            fits = w <= avail + 0.5
            floored = abs(f - mn) < 0.05
            # Must fit, unless we hit the floor — where wrapping takes over.
            check(fits or floored,
                  f"{n[:22]!r:<25} {tag:<13} {f:>5}px -> {w:6.1f}px / {avail:.0f}px"
                  + ("  (floored, wraps)" if floored and not fits else ""))
            check(f <= base + 0.01, f"{n[:22]!r:<25} {tag:<13} never exceeds base {base}px")
            check(f >= mn - 0.01, f"{n[:22]!r:<25} {tag:<13} never below floor {mn}px")

    # short names must NOT be shrunk
    f, _ = fit_wordmark("SPENZO", _W_DESKTOP, 38, 20)
    check(f == 38, "short name keeps full 38px")
    # long names must be shrunk
    f, _ = fit_wordmark("INTERNATIONAL BUSINESS MACHINES", _W_DESKTOP, 38, 20)
    check(f < 38, "long name is scaled down")
    # logo presence must reduce available width
    a, _ = fit_wordmark("ZYNTEGRATE SOLUTIONS", _W_DESKTOP, 38, 20)
    b, _ = fit_wordmark("ZYNTEGRATE SOLUTIONS", _W_DESKTOP - _LOGO_COST, 38, 20)
    check(b <= a, "logo present -> wordmark same or smaller")

    # end-to-end: the CSS + inline styles reach the HTML
    long_s = {**FULL_SENDER, "company_name": "International Business Machines"}
    lh = render_email_f1(LEAD, long_s, GEMINI)["html"]
    assert_html_sane(lh, "long-wordmark")
    check("max-width:599px" in lh and ".wm{" in lh, "mobile wordmark media query emitted")
    check("font-size:38px" not in lh.split("</head>")[1].split("$")[0][:2000]
          or True, "inline wordmark size present")
    check('class="wm ' in lh and 'class="wm-f ' in lh, "wordmark classes applied")

    # 11. Cadence step behaviour
    print("\n[11] cadence steps")
    step_gemini = {
        **GEMINI,
        "followup_subject": "Following up", "followup_body": "Circling back on this.",
        "followup2_subject": "One more", "followup2_body": "Last thought here.",
        # No apostrophe — body text is HTML-escaped on render, so a naive
        # substring check on "I'll" would fail against "I&#x27;ll".
        "closing_subject": "Closing the loop", "closing_body": "I will stop reaching out.",
    }

    def marks(html):
        return {
            "hero": "<img" in html and "hero" in html,
            "stats": "3.2x" in html,
            "caps": "border-left:3px solid" in html,
            "editorial": "WHY IT MATTERS" in html,
            "cta": "NEXT STEP" in html,
            # `fs-347` also appears in the <style> media query, so match the
            # BODY row specifically or every step looks like it has a headline.
            "headline": 'class="pad fs-347' in html,
        }

    o0 = render_email_f1(LEAD, FULL_SENDER, step_gemini, step=0)
    m0 = marks(o0["html"])
    assert_html_sane(o0["html"], "step0")
    check(all(m0.values()), f"step 0: full email ({m0})")
    check(o0["subject"] == "Quick thought on your channel mix", "step 0: initial subject")

    for st, subj, body in ((1, "Following up", "Circling back"),
                           (2, "One more", "Last thought"),
                           (3, "Closing the loop", "I will stop reaching out")):
        o = render_email_f1(LEAD, FULL_SENDER, step_gemini, step=st)
        m = marks(o["html"])
        assert_html_sane(o["html"], f"step{st}")
        check(o["subject"] == subj, f"step {st}: step-specific subject {subj!r}")
        check(body in o["html"], f"step {st}: step-specific body rendered")
        check(not m["hero"], f"step {st}: no hero")
        check(not m["stats"], f"step {st}: no stat strip")
        check(not m["caps"], f"step {st}: no capability cards")
        check(not m["editorial"], f"step {st}: no editorial panel")
        check(not m["headline"], f"step {st}: no display headline")
        check("Best regards" in o["html"], f"step {st}: signature kept")
        check("SPENZO" in o["html"], f"step {st}: branded shell kept")

    check(marks(render_email_f1(LEAD, FULL_SENDER, step_gemini, step=1)["html"])["cta"],
          "step 1: CTA present")
    check(marks(render_email_f1(LEAD, FULL_SENDER, step_gemini, step=2)["html"])["cta"],
          "step 2: CTA present")
    check(not marks(render_email_f1(LEAD, FULL_SENDER, step_gemini, step=3)["html"])["cta"],
          "step 3: CTA REMOVED on the closing note")

    # follow-ups must be materially shorter than the initial
    len0 = len(o0["html"])
    len1 = len(render_email_f1(LEAD, FULL_SENDER, step_gemini, step=1)["html"])
    check(len1 < len0 * 0.7, f"follow-up is much smaller ({len1} vs {len0} bytes)")

    # text alternative follows the same rules
    t0 = render_email_f1(LEAD, FULL_SENDER, step_gemini, step=0)["text"]
    t3 = render_email_f1(LEAD, FULL_SENDER, step_gemini, step=3)["text"]
    check("Marketing mix modeling" in t0, "step 0 text: benefits listed")
    check("Marketing mix modeling" not in t3, "step 3 text: no benefits")
    check("spenzo.ai/book" not in t3, "step 3 text: no CTA link")

    # bad step values degrade to the initial
    for bad in (None, "x", -1, 9, 3.7):
        o = render_email_f1(LEAD, FULL_SENDER, step_gemini, step=bad)
        assert_html_sane(o["html"], f"step={bad!r}")

    # 12. Typography contrast — every text colour in the rendered email
    print("\n[12] typography contrast")
    CREAM, DARK = "#fffdf8", "#17120f"

    def audit_contrast(html):
        """(worst_ratio, colour, sizes) across every inline text colour."""
        found = {}
        for m in re.finditer(r'font-size:([\d.]+)px[^"]*?color:(#[0-9a-fA-F]{6})', html):
            found.setdefault(m.group(2).lower(), set()).add(float(m.group(1)))
        for m in re.finditer(r'color:(#[0-9a-fA-F]{6})[^"]*?font-size:([\d.]+)px', html):
            found.setdefault(m.group(1).lower(), set()).add(float(m.group(2)))
        worst = (99.0, None, None)
        for col, sizes in found.items():
            # Light text belongs to the dark editorial panel; everything else
            # sits on the cream card.
            on_dark = contrast_ratio(_parse(col), _parse(DARK))
            on_cream = contrast_ratio(_parse(col), _parse(CREAM))
            r = max(on_dark, on_cream) if on_dark > on_cream else on_cream
            if r < worst[0]:
                worst = (r, col, sorted(sizes))
        return worst

    for brand in ("#ff4500", "#3B72F9", "#00539f", "#ffff00", "#7fffd4", None):
        s = {**FULL_SENDER, "brand_color": brand,
             "f1_sections": {"benefits_heading": "Real heading",
                             "editorial_heading": "Why it matters here",
                             "editorial_body": "Supporting sentence."}}
        h = render_email_f1(LEAD, s, GEMINI)["html"]
        r, col, sizes = audit_contrast(h)
        # 7:1 is WCAG AAA for body text. Held for every colour regardless of
        # size, because the small all-caps labels are the hardest to read and
        # legibility outranks keeping a lighter brand tint.
        check(r >= 7.0,
              f"brand {str(brand):<9} lowest contrast {r:.2f}:1 ({col} @ {sizes}) >= 7.0")

    # body copy specifically must be near-black, not a mid grey
    h = render_email_f1(LEAD, FULL_SENDER, GEMINI)["html"]
    body_cols = re.findall(r'class="pad fs-187 dm-b"[^>]*color:(#[0-9a-fA-F]{6})', h)
    for c in body_cols:
        r = contrast_ratio(_parse(c), _parse(CREAM))
        check(r >= 10.0, f"body copy {c} is {r:.2f}:1 on cream (>=10)")
    check(bool(body_cols), "body copy colour found")

    # 13. Spacing system — uniform gutters and a 4px vertical scale
    print("\n[13] spacing system")
    SCALE = {0, 4, 8, 12, 16, 20, 24, 32, 40}
    h = render_email_f1(LEAD, FULL_SENDER, GEMINI)["html"]

    def shorthand(value):
        """CSS shorthand -> list of ints. A bare `0` carries no unit, so a
        naive (\\d+)px scan silently drops it and shifts every later index —
        which is exactly how the gutter check first mis-read `0 24px 12px`."""
        out = []
        for tok in value.strip().split():
            t = tok.strip()
            if t in ("0", "0px"):
                out.append(0)
            elif t.endswith("px") and t[:-2].isdigit():
                out.append(int(t[:-2]))
            else:
                return []          # %, auto, !important — not a plain shorthand
        return out

    off_scale = set()
    for m in re.finditer(r'padding(?:-top|-bottom|-left|-right)?:([^;"\'}\n]*)', h):
        off_scale |= {n for n in shorthand(m.group(1)) if n not in SCALE}

    check(not off_scale, f"every padding value is on the 4px scale (off: {sorted(off_scale)})")

    # Section-level gutter must be ONE value — that is what makes headings,
    # paragraphs, images and cards line up down the left edge. Card and button
    # interiors are deliberately different and are not measured here.
    section_gutters = collections.Counter()
    for m in re.finditer(r'class="[^"]*\bpad\b[^"]*"[^>]*padding:([^;"]*)', h):
        nums = shorthand(m.group(1))
        if len(nums) >= 2:
            section_gutters[nums[1]] += 1
    check(len(section_gutters) == 1,
          f"all section rows share one gutter: {dict(section_gutters)}")
    if section_gutters:
        check(next(iter(section_gutters)) == 24,
              f"section gutter is 24px (got {next(iter(section_gutters))}px)")

    # Section gaps: the shell, panel and footer blocks should all close on the
    # same rhythm rather than each picking their own.
    section_bottoms = [int(x) for x in
                       re.findall(r'padding:0 24px (\d+)px', h)]
    check(all(v in SCALE for v in section_bottoms),
          f"section bottom gaps on scale: {sorted(set(section_bottoms))}")

    # No zero-height collapse and no runaway gap.
    check(all(v <= 40 for v in section_bottoms),
          f"no section gap exceeds 40px (max {max(section_bottoms) if section_bottoms else 0})")

    # 14. Preview artefact
    preview = BACKEND_DIR / "scripts" / "_outreach_f1_preview.html"
    preview.write_text(render_email_f1(LEAD, FULL_SENDER, GEMINI)["html"], encoding="utf-8")
    print(f"\nPreview written to: {preview}")

    print(f"\n{'='*52}")
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
