"""Convert any images in screenshots-raw/ into Chrome-Web-Store-ready
1280x800 PNGs in screenshots/. Each image is scaled to fit (no distortion,
no cropping) and centered on a white 1280x800 canvas — portrait images get
side padding, wide images get top/bottom padding. Run:

  ../backend/venv/Scripts/python.exe make-screenshots.py
"""
import glob, os
from PIL import Image

W, H = 1280, 800
BG = (255, 255, 255)
raw = sorted(
    p for ext in ("png", "jpg", "jpeg", "webp")
    for p in glob.glob(os.path.join("screenshots-raw", f"*.{ext}"))
)
if not raw:
    print("No images found in screenshots-raw/ — drop your screenshots there first.")
    raise SystemExit

for i, p in enumerate(raw, 1):
    img = Image.open(p).convert("RGBA")
    scale = min(W / img.width, H / img.height)
    nw, nh = int(img.width * scale), int(img.height * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    canvas.paste(resized, ((W - nw) // 2, (H - nh) // 2), resized)
    out = os.path.join("screenshots", f"screenshot-{i}.png")
    canvas.save(out)
    print(f"{os.path.basename(p):<32} {img.width}x{img.height} -> {out} (1280x800)")
