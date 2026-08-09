import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Ensure we can import from apps/backend
HERE = Path(__file__).parent
BACKEND_DIR = HERE.parents[1]
sys.path.append(str(BACKEND_DIR))

from services.image_templates import (
    _smart_fit, _rounded_mask, _load_logo_image, INK, MUTED
)

# ---------------------------------------------------------------------------
# High-Fidelity Helpers (Wylto Style)
# ---------------------------------------------------------------------------

def _draw_shadow(d, shape, radius=8, color=(0, 0, 0, 20), passes=3):
    """Draws multiple offset layers to create a soft 'glow' shadow."""
    for i in range(1, passes + 1):
        offset = i * 2
        d.rounded_rectangle(
            [shape[0] + offset, shape[1] + offset, shape[2] + offset, shape[3] + offset],
            radius=radius, fill=color
        )

def _draw_gradient_pill(canvas, x, y, text, font, start_color, end_color):
    """Horizontal linear gradient pill with white text."""
    d = ImageDraw.Draw(canvas)
    tw = d.textbbox((0, 0), text, font=font)
    w, h = (tw[2] - tw[0]) + 52, 44
    
    # Create gradient mask
    mask = Image.new("L", (w, h), 0)
    d_mask = ImageDraw.Draw(mask)
    d_mask.rounded_rectangle([0, 0, w, h], radius=h//2, fill=255)
    
    # Generate gradient
    grad = Image.new("RGB", (w, h))
    for i in range(w):
        r = int(start_color[0] + (end_color[0] - start_color[0]) * (i / w))
        g = int(start_color[1] + (end_color[1] - start_color[1]) * (i / w))
        b = int(start_color[2] + (end_color[2] - start_color[2]) * (i / w))
        for j in range(h):
            grad.putpixel((i, j), (r, g, b))
            
    canvas.paste(grad, (x, y), mask)
    d.text((x + 26, y + (h - (tw[3] - tw[1])) // 2 - 3), text, font=font, fill=(255, 255, 255))
    return w, h

def _render_logo_lockup_premium(logo, name, tagline, max_h, color):
    """Wylto-style lockup: Bold Upper Name, tight spacing."""
    name = (name or "").strip().upper()
    tag = (tagline or "").strip()
    
    # Font setup (Segoe UI)
    font_path = "C:/Windows/Fonts/segoeuib.ttf"
    reg_path = "C:/Windows/Fonts/segoeui.ttf"
    try:
        f_name = ImageFont.truetype(font_path, int(max_h * 0.55))
        f_tag = ImageFont.truetype(reg_path, int(max_h * 0.28))
    except:
        f_name = ImageFont.load_default()
        f_tag = ImageFont.load_default()

    # Measure
    tmq = Image.new("RGBA", (1, 1))
    dq = ImageDraw.Draw(tmq)
    nb = dq.textbbox((0, 0), name, font=f_name)
    tb = dq.textbbox((0, 0), tag, font=f_tag) if tag else (0,0,0,0)
    
    mark_h = max_h
    lw, lh = logo.size
    mark = logo.resize((int(lw * (mark_h / lh)), mark_h), Image.LANCZOS)
    
    gap = 28
    v_gap = 4
    total_w = mark.size[0] + gap + max(nb[2]-nb[0], tb[2]-tb[0])
    total_h = max_h # simplified
    
    res = Image.new("RGBA", (total_w + 30, total_h + 10), (0, 0, 0, 0))
    res.paste(mark, (0, 0), mark)
    dr = ImageDraw.Draw(res)
    tx = mark.size[0] + gap
    dr.text((tx, 0), name, font=f_name, fill=color)
    if tag:
        dr.text((tx, int(max_h * 0.55) + v_gap), tag, font=f_tag, fill=MUTED)
    return res

# ---------------------------------------------------------------------------
# Template: Wylto Premium
# ---------------------------------------------------------------------------

def t12_wylto_premium(bg, copy, logo, primary_rgb, C):
    canvas = Image.new("RGB", (C, C), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    pad = 64
    
    # 1. Background Photo (Inset with extreme rounding)
    photo_h = int(C * 0.50)
    photo = _smart_fit(bg, C - 128, photo_h)
    mask = _rounded_mask(photo.size, 48) # Higher rounding
    px, py = 64, C - photo_h - 64
    
    # Layer: Soft Shadow for Photo
    _draw_shadow(d, [px, py, px + photo.size[0], py + photo_h], radius=48)
    canvas.paste(photo, (px, py), mask)

    # 2. Premium Logo Lockup
    lockup = _render_logo_lockup_premium(
        logo, copy.get("product_name"), copy.get("product_tagline"), 68, primary_rgb
    )
    canvas.paste(lockup, (pad, pad), lockup)

    # 3. Category Pill (e.g. LIVE WEBINAR)
    pill_font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 20)
    pill_label = "STRATEGIC INSIGHTS"
    pw, ph = _draw_gradient_pill(canvas, pad, pad + 100, pill_label, pill_font, (126, 87, 194), (103, 58, 183)) # Purple

    # 4. Heading (Geometric Sans)
    h_font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 62)
    headline = copy.get("headline", "")
    hi_words = copy.get("highlight_words", [])
    hi_set = {w.lower() for w in hi_words}
    
    # Simplified wrap
    lines, cur = [], ""
    for w in headline.split():
        probe = (cur + " " + w).strip()
        if d.textbbox((0, 0), probe, font=h_font)[2] <= (C - 128): cur = probe
        else: lines.append(cur); cur = w
    lines.append(cur)
    
    hy = pad + 100 + ph + 32
    for line in lines:
        lx = pad
        for word in line.split():
            clean = word.strip(".,;:!?").lower()
            color = primary_rgb if clean in hi_set else INK
            d.text((lx, hy), word, font=h_font, fill=color)
            lx += d.textbbox((0, 0), word + " ", font=h_font)[2]
        hy += 72

    # 5. CTA (Bottom Right with Glow)
    cta = copy.get("cta", "Register Now")
    cta_f = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 24)
    tw = d.textbbox((0, 0), cta, font=cta_f)
    bw, bh = (tw[2]-tw[0]) + 68, 56
    bx, by = C - pad - bw, C - pad - bh - 12
    
    # Button Shadow
    _draw_shadow(d, [bx, by, bx + bw, by + bh], radius=bh//2, color=(0,0,0,30))
    # Button Shape (Wylto Green style or Brand Orange)
    # Using a vibrant green for "Wylto" vibes if it matches the pill, otherwise brand color
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh//2, fill=(74, 206, 110)) # Wylto Green
    d.text((bx + 34, by + (bh - (tw[3]-tw[1]))//2 - 4), cta, font=cta_f, fill=(255, 255, 255))

    return canvas

def main():
    img_path = BACKEND_DIR / "scripts" / "visualist_harness" / "out" / "img_3.png"
    logo_path = BACKEND_DIR.parent / "product-page" / "public" / "favicon.png"
    bg = Image.open(img_path).convert("RGB")
    logo = Image.open(logo_path).convert("RGBA")
    
    copy = {
        "headline": "Actionable Insights. Accelerated Growth.",
        "product_name": "Pipelyt",
        "product_tagline": "AI Social Orchestration",
        "cta": "Register Now",
        "highlight_words": ["Actionable", "Accelerated"]
    }
    
    print("Rendering [WYLTO PREMIUM] variant...")
    res = t12_wylto_premium(bg, copy, logo, (255, 107, 53), 1024)
    out_path = HERE / "out" / "style_wylto_premium.png"
    res.save(out_path, "PNG")
    print(f"Success! Premium render saved to: {out_path}")

if __name__ == "__main__":
    main()
