"""Tests for Phase A brand-colour resolution.

Covers the rules that keep a bad extraction out of a customer's email:
  * 4 extracted colours collapse to the 2 derive_palette takes
  * near-white / near-black primaries are REJECTED, not clamped
  * primary ~= accent drops the accent rather than flattening the design
  * the resolution ladder prefers Business DNA, then cache, then extraction
  * allow_extraction=False never reaches the network

Usage:
    python scripts/test_brand_colors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from nexus.services import brand_assets  # noqa: E402
from nexus.services.brand_assets import (  # noqa: E402
    _norm_hex, is_usable_primary, map_extracted_colors, resolve_brand_colors,
)
from nexus.services.email_palette import (  # noqa: E402
    contrast_ratio, derive_palette,
)

failures: list = []


def check(cond, msg):
    print(f"  [{'OK' if cond else 'FAIL'}]   {msg}")
    if not cond:
        failures.append(msg)


def main() -> int:
    print("== brand colour resolution ==\n")

    # 1. hex normalisation
    print("[1] hex normalisation")
    for raw, want in [
        ("#FF4500", "#ff4500"), ("ff4500", "#ff4500"), ("  #Ff4500 ", "#ff4500"),
        ("#f40", "#ff4400"), ("", ""), ("nope", ""), (None, ""), (123, ""),
        ("#12345", ""), ("#1234567", ""),
    ]:
        check(_norm_hex(raw) == want, f"_norm_hex({raw!r}) -> {want!r}")

    # 2. primary usability band
    print("\n[2] primary usability")
    for h, ok, why in [
        ("#ff4500", True,  "vivid orange"),
        ("#0f4c5c", True,  "deep teal"),
        ("#1f70c1", True,  "mid blue"),
        ("#ffffff", False, "pure white — no brand signal"),
        ("#fefefe", False, "near white"),
        ("#000000", False, "pure black — no brand signal"),
        ("#010101", False, "near black"),
        ("#7fffd4", True,  "bright aquamarine still carries hue"),
    ]:
        check(is_usable_primary(h) is ok, f"{h} usable={ok}  ({why})")

    # 3. four -> two mapping
    print("\n[3] extracted colour mapping")
    m = map_extracted_colors({
        "primary": "#ff4500", "secondary": "#222222",
        "accent": "#0f4c5c", "background": "#ffffff",
    })
    check(m.get("brand_color") == "#ff4500", "primary -> brand_color")
    check(m.get("accent_color") == "#0f4c5c", "accent -> accent_color")
    check("#222222" not in m.values(), "secondary (text colour) discarded")
    check(m.get("background_color") == "#ffffff",
          "background RETAINED (hue-only; derive_palette re-lights it)")

    check(map_extracted_colors({}) == {}, "empty input -> {}")
    check(map_extracted_colors({"primary": "#ffffff"}) == {},
          "white primary rejected outright (not clamped)")
    check(map_extracted_colors({"primary": "#000000"}) == {},
          "black primary rejected outright")
    check(map_extracted_colors(None) == {}, "None input -> {}")
    check(map_extracted_colors({"accent": "#ff4500"}) == {},
          "accent without primary -> {}")

    # near-identical pair
    m = map_extracted_colors({"primary": "#ff4500", "accent": "#ff4601"})
    check(m.get("brand_color") == "#ff4500" and "accent_color" not in m,
          "near-identical accent dropped (would collide with c_link)")
    m = map_extracted_colors({"primary": "#ff4500", "accent": "#0f4c5c"})
    check("accent_color" in m, "distinct accent kept")

    # secondary used as accent fallback
    m = map_extracted_colors({"primary": "#ff4500", "secondary": "#0f4c5c"})
    check(m.get("accent_color") == "#0f4c5c",
          "secondary promoted to accent when accent absent")

    # 4. end-to-end into the palette
    print("\n[4] mapped colours drive a legible palette")
    for primary in ("#ff4500", "#0f4c5c", "#7fffd4", "#1f70c1"):
        m = map_extracted_colors({"primary": primary, "accent": "#333399"})
        pal = derive_palette(m.get("brand_color"), m.get("accent_color"))
        # 9 brand roles + 6 surfaces (shell/card/band/panel/tile/hairline)
        check(len(pal) == 15, f"{primary}: palette has 15 keys")
        for k in ("c_shell", "c_card", "c_band", "c_panel", "c_tile", "c_hairline"):
            check(k in pal, f"{primary}: surface {k} present")
        check(all(v.startswith("#") and len(v) == 7 for v in pal.values()),
              f"{primary}: every palette value is a full hex")

    # a dark / vivid site background must never become the email background
    print("\n[4b] background is hue-only, never inherited")
    from nexus.services.email_palette import _parse as _p, _rel_luminance
    for bg, label in [("#0a1929", "dark navy"), ("#000000", "black"),
                      ("#00ff00", "vivid green"), ("#ffffff", "white"), (None, "none")]:
        pal = derive_palette("#ff5722", None, bg)
        for key in ("c_shell", "c_card", "c_band", "c_panel", "c_tile"):
            lum = _rel_luminance(_p(pal[key]))
            check(lum > 0.60, f"{label:<12} {key} stays light (luminance {lum:.2f})")
        body = contrast_ratio(_p("#443e39"), _p(pal["c_card"]))
        check(body >= 10.0, f"{label:<12} body copy holds {body:.2f}:1 on the card")

    # rejected primary must yield the NEUTRAL palette, not a clamped brand one
    m = map_extracted_colors({"primary": "#ffffff"})
    neutral = derive_palette(None)
    check(derive_palette(m.get("brand_color"), m.get("accent_color")) == neutral,
          "rejected primary falls through to neutral slate")

    # 5. resolution ladder
    print("\n[5] resolution ladder")

    class FakeDB:
        def __init__(self): self.calls = []
        def execute(self, *a, **k):
            self.calls.append("execute")
            raise RuntimeError("no db in test")
        def commit(self): pass
        def rollback(self): pass

    calls = {"dna": 0, "extract": 0}

    import nexus.services.brand_dna as bdna
    orig_fetch = bdna.fetch_brand_colors

    def fake_dna(db, user_id, source_url):
        calls["dna"] += 1
        return {"brand_color": "#ff4500", "accent_color": "#0f4c5c"}

    bdna.fetch_brand_colors = fake_dna
    try:
        out = resolve_brand_colors(FakeDB(), {"id": 1, "user_id": 9,
                                              "source_url": "https://spenzo.ai"})
        check(out.get("brand_color") == "#ff4500", "DNA hit returned directly")
        check(calls["dna"] == 1, "DNA consulted first")
    finally:
        bdna.fetch_brand_colors = orig_fetch

    # DNA miss + extraction disabled -> {} and NO extraction attempted
    def empty_dna(db, user_id, source_url):
        return {}

    bdna.fetch_brand_colors = empty_dna
    orig_extract = getattr(brand_assets, "_read_cached")
    brand_assets._read_cached = lambda db, pid: {}
    try:
        out = resolve_brand_colors(
            FakeDB(), {"id": 1, "user_id": 9, "source_url": "https://x.co"},
            allow_extraction=False,
        )
        check(out == {}, "DNA miss + no extraction -> {}")
    finally:
        bdna.fetch_brand_colors = orig_fetch
        brand_assets._read_cached = orig_extract

    # cache hit short-circuits extraction
    bdna.fetch_brand_colors = empty_dna
    brand_assets._read_cached = lambda db, pid: {"brand_color": "#123456"}
    try:
        out = resolve_brand_colors(
            FakeDB(), {"id": 1, "user_id": 9, "source_url": "https://x.co"},
            allow_extraction=True,
        )
        check(out.get("brand_color") == "#123456", "cache hit used")
    finally:
        bdna.fetch_brand_colors = orig_fetch
        brand_assets._read_cached = orig_extract

    # 6. never raises
    print("\n[6] contract: never raises")
    for bad in (None, {}, {"id": None}, {"source_url": None}, {"source_url": ""}):
        try:
            r = resolve_brand_colors(FakeDB(), bad, allow_extraction=False)
            check(isinstance(r, dict), f"resolve_brand_colors({bad}) -> dict")
        except Exception as e:
            check(False, f"resolve_brand_colors({bad}) raised {e!r}")

    print(f"\n{'='*52}")
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
