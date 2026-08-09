"""
Pipelyt Image Utilities
PIL-based text compositing + Gemini image generation helpers.
Two-pass pipeline: Gemini produces background → PIL composites text/logo on top (zero typos).
"""
import os
import uuid
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types
from core.config import GEMINI_API_KEY, S3_BUCKET_NAME
from core.s3_utils import get_s3_client, get_s3_url

logger = logging.getLogger("pipelyt.image")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- Font paths (bundled Roboto for Lambda + local compatibility) ---
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts")
_FONT_BOLD_PATH = os.path.join(_FONT_DIR, "Roboto-Bold.ttf")
_FONT_REG_PATH = os.path.join(_FONT_DIR, "Roboto-Regular.ttf")

_SYSTEM_FONTS_BOLD = [
    "/c/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_SYSTEM_FONTS_REG = [
    "/c/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _get_font(style: str, size: int) -> ImageFont.ImageFont:
    """Load Roboto font with graceful system/default fallbacks."""
    if style == 'bold':
        candidates = [_FONT_BOLD_PATH] + _SYSTEM_FONTS_BOLD
    else:
        candidates = [_FONT_REG_PATH] + _SYSTEM_FONTS_REG
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color string to (R, G, B) tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    try:
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (255, 69, 0)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list:
    """Wrap text into lines that fit within max_width pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        try:
            w = draw.textlength(test, font=font)
        except AttributeError:
            w = font.getlength(test)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _draw_text_with_shadow(draw, pos, text, font, fill, shadow_color=(0, 0, 0), shadow_offset=3):
    """Draw text with a drop shadow for readability over any background."""
    x, y = pos
    # Shadow pass
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)
    draw.text((x - shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def _composite_text_on_image(
    image_bytes: bytes,
    heading: str,
    sub_heading: str,
    domain_name: str,
    logo_bytes: bytes = None,
    primary_color: str = "#FF4500",
    layout_type: str = "left-text",
) -> bytes:
    """
    Composites heading, subheading, logo, and CTA button onto a background image.
    - No heavy dark overlay — background image stays vibrant and fully visible.
    - A subtle frosted panel sits only behind the text block for readability.
    - Text shadows guarantee readability over any background color.
    - layout_type: 'left-text' (text left, image right) | 'center-text' (text centered bottom)
    """
    try:
        r_p, g_p, b_p = _hex_to_rgb(primary_color)
        # Slightly dimmed primary for panel tint
        panel_r = max(0, r_p - 30)
        panel_g = max(0, g_p - 30)
        panel_b = max(0, b_p - 30)

        with Image.open(BytesIO(image_bytes)).convert("RGBA") as bg:
            bg = bg.resize((1024, 1024), Image.LANCZOS)
            canvas = bg.copy()

        draw = ImageDraw.Draw(canvas)

        # --- Determine text column based on layout ---
        if layout_type == "center-text":
            text_x = 80
            text_max_w = 860
            logo_x = 40
        else:  # default: "left-text"
            text_x = 44
            text_max_w = 520
            logo_x = 40

        # --- Logo (top-left always) ---
        logo_bottom_y = 48
        if logo_bytes:
            try:
                with Image.open(BytesIO(logo_bytes)).convert("RGBA") as logo_img:
                    logo_img.thumbnail((180, 70), Image.LANCZOS)
                    # Paste with a subtle dark background pad for visibility over bright images
                    logo_pad = Image.new("RGBA", (logo_img.width + 16, logo_img.height + 16), (0, 0, 0, 120))
                    logo_pad.paste(logo_img, (8, 8), logo_img)
                    canvas.paste(logo_pad, (logo_x, 32), logo_pad)
                    logo_bottom_y = 32 + logo_pad.height + 12
            except Exception as e:
                logger.warning(f"Logo paste failed: {e}")

        heading_y = max(logo_bottom_y + 20, 155)

        # --- Heading font — adaptive size, no word limit ---
        heading_words = heading.upper().split()
        # Split into two roughly equal lines
        mid = max(1, len(heading_words) // 2)
        line1 = " ".join(heading_words[:mid])
        line2 = " ".join(heading_words[mid:]) if heading_words[mid:] else ""

        heading_font = _get_font('bold', 72)
        for fs in [72, 64, 56, 48, 40, 34]:
            heading_font = _get_font('bold', fs)
            try:
                w1 = draw.textlength(line1, font=heading_font)
                w2 = draw.textlength(line2, font=heading_font) if line2 else 0
            except AttributeError:
                w1 = heading_font.getlength(line1)
                w2 = heading_font.getlength(line2) if line2 else 0
            if max(w1, w2) <= text_max_w:
                break

        try:
            line_h = int(draw.textbbox((0, 0), "Ag", font=heading_font)[3]) + 10
        except Exception:
            line_h = 76

        # --- Subheading font ---
        sub_font = _get_font('regular', 34)
        sub_lines = _wrap_text(sub_heading, sub_font, text_max_w, draw)
        sub_line_h = 46

        # --- Measure total text block height ---
        num_heading_lines = 2 if line2 else 1
        total_text_h = (num_heading_lines * line_h) + 18 + (len(sub_lines[:4]) * sub_line_h) + 20

        # --- Frosted panel behind text only (no heavy overlay on rest of image) ---
        panel_x1 = text_x - 20
        panel_y1 = heading_y - 18
        panel_x2 = text_x + text_max_w + 20
        panel_y2 = panel_y1 + total_text_h + 24

        frosted = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        f_draw = ImageDraw.Draw(frosted)
        f_draw.rounded_rectangle(
            [panel_x1, panel_y1, panel_x2, panel_y2],
            radius=14,
            fill=(panel_r, panel_g, panel_b, 140),
        )
        canvas = Image.alpha_composite(canvas, frosted)
        draw = ImageDraw.Draw(canvas)

        # --- Draw heading ---
        shadow = (0, 0, 0)
        _draw_text_with_shadow(draw, (text_x, heading_y), line1, heading_font,
                               fill=(r_p, g_p, b_p), shadow_color=shadow)
        if line2:
            _draw_text_with_shadow(draw, (text_x, heading_y + line_h), line2, heading_font,
                                   fill=(255, 255, 255), shadow_color=shadow)
            sub_y = heading_y + line_h * 2 + 18
        else:
            sub_y = heading_y + line_h + 18

        # --- Draw subheading ---
        for i, sline in enumerate(sub_lines[:4]):
            _draw_text_with_shadow(draw, (text_x, sub_y + i * sub_line_h), sline, sub_font,
                                   fill=(230, 230, 230), shadow_color=shadow, shadow_offset=2)

        # --- CTA pill button ---
        if domain_name:
            cta_font = _get_font('bold', 26)
            cta_text = domain_name.lower().strip("/").split("/")[0]
            cta_y = 940
            cta_x = text_x
            try:
                txt_w = int(draw.textlength(cta_text, font=cta_font))
            except AttributeError:
                txt_w = int(cta_font.getlength(cta_text))
            pad_x, btn_h, radius = 28, 52, 26
            btn_w = txt_w + 2 * pad_x
            draw.rounded_rectangle(
                [cta_x, cta_y, cta_x + btn_w, cta_y + btn_h],
                radius=radius,
                fill=(r_p, g_p, b_p),
            )
            draw.text(
                (cta_x + pad_x, cta_y + (btn_h - 26) // 2),
                cta_text,
                fill=(255, 255, 255),
                font=cta_font,
            )

        final_rgb = canvas.convert("RGB")
        out = BytesIO()
        final_rgb.save(out, format="JPEG", quality=95)
        return out.getvalue()

    except Exception as e:
        logger.error(f"PIL compositing failed: {e}. Returning original image.")
        return image_bytes


def _pad_image_to_square(image_bytes):
    """Pads an image to a white square to normalize its aspect ratio."""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            max_dim = max(width, height)
            square_img = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
            offset = ((max_dim - width) // 2, (max_dim - height) // 2)
            square_img.paste(img, offset)
            out_buffer = BytesIO()
            square_img.save(out_buffer, format="PNG")
            return out_buffer.getvalue()
    except Exception as e:
        logger.error(f"Image padding failed: {e}")
        return image_bytes


def _strip_metadata(image_bytes):
    """Removes all EXIF and metadata from an image."""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)
            out_buffer = BytesIO()
            clean_img.save(out_buffer, format="JPEG", quality=95, optimize=True)
            return out_buffer.getvalue()
    except Exception as e:
        logger.error(f"Metadata stripping failed: {e}")
        return image_bytes


def _gen_single_variant(index, visual_concepts=None, logo_bytes=None, primary_color="#FF4500", raw_prompt=None, domain_name=""):
    """
    Generates one image variant using a two-pass approach:
      Pass 1 — Gemini generates the background visual (NO text in image).
      Pass 2 — PIL composites heading, subheading, logo, and CTA button on top.
    """
    try:
        if raw_prompt:
            prompt = raw_prompt
            heading = "Custom Concept"
            sub_heading = "Professional Visual"
            concept_name = "Custom Generation"
            concept_layout = "custom"
            concept_layout_type = "left-text"
        else:
            concept = visual_concepts[index]
            prompt = concept.get('generation_prompt', '')
            heading = concept.get('heading', 'TRANSFORM YOUR WORKFLOW')
            sub_heading = concept.get('sub_heading', 'Professional solutions for modern teams')
            concept_name = concept.get('name', f"Variant {index + 1}")
            concept_layout = concept.get('layout', 'default')
            concept_layout_type = concept.get('layout_type', 'left-text')

        if not prompt:
            logger.warning(f"Skipping Variant {index + 1}: prompt is missing.")
            return None

        if not client:
            raise Exception("AI_CLIENT_MISSING")

        logger.info(f"Triggering gemini-2.5-flash-image for variant {index + 1} (background-only)...")

        generate_content_config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        )

        image_bytes = None
        for chunk in client.models.generate_content_stream(
            model='gemini-2.5-flash-image',
            contents=[prompt],
            config=generate_content_config
        ):
            if chunk.parts:
                for part in chunk.parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break
            if image_bytes:
                break

        if not image_bytes:
            raise Exception("NO_IMAGES_RETURNED_BY_GEMINI")

        logger.info(f"Variant {index + 1} background generated ({len(image_bytes)} bytes). Running PIL compositing...")

        final_image_bytes = _composite_text_on_image(
            image_bytes=image_bytes,
            heading=heading,
            sub_heading=sub_heading,
            domain_name=domain_name,
            logo_bytes=logo_bytes,
            primary_color=primary_color,
            layout_type=concept_layout_type,
        )

        clean_bytes = _strip_metadata(final_image_bytes)
        s3 = get_s3_client()
        file_name = f"ai_gen/visual_{uuid.uuid4().hex}.jpg"
        s3.upload_fileobj(
            BytesIO(clean_bytes),
            S3_BUCKET_NAME,
            file_name,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )

        final_url = get_s3_url(file_name)
        logger.info(f"Variant {index + 1} composited and uploaded: {final_url}")

        return {
            "url": final_url,
            "bytes": final_image_bytes,
            "name": concept_name,
            "layout": concept_layout,
            "layout_type": concept_layout_type,
            "heading": heading,
            "sub_heading": sub_heading,
        }

    except Exception as e:
        logger.error(f"Image Gen variant {index} FAILED: {e}")
        return None


def generate_visual_variants(visual_concepts, logo_bytes=None, refined_brief="", primary_color="#FF4500", domain_name=""):
    """
    Generates image variants using the two-pass pipeline:
      1. Gemini produces background visuals (no text).
      2. PIL composites heading, subheading, logo, and CTA onto each background.
    """
    from services.agents import _visual_critic_agent

    if not isinstance(visual_concepts, list) or len(visual_concepts) == 0:
        logger.warning(f"Invalid visual_concepts ({type(visual_concepts)}). Using generic fallbacks.")
        visual_concepts = [
            {
                "layout": "Abstract Flow",
                "layout_type": "left-text",
                "name": "Energy Convergence",
                "heading": "BUILT FOR GROWTH",
                "sub_heading": "Powerful tools that move your business forward",
                "generation_prompt": (
                    "STRICT SQUARE 1:1, 1024x1024, 8k ultra-sharp, photorealistic render. "
                    "SCENE: Multiple glowing streams of warm amber and cool blue light flowing "
                    "from lower-right, converging into a single brilliant focal point at upper-right. "
                    "Abstract, cinematic, premium aesthetic. Left 50% = clean open dark gradient (empty). "
                    "Right 50% = converging light streams. Cinematic studio lighting, crisp glowing edges. "
                    "ABSOLUTE RULE: NO text, NO numbers, NO labels, NO UI elements, NO watermarks. "
                    "Pure abstract background visual only."
                ),
            },
            {
                "layout": "Geometric Precision",
                "layout_type": "right-visual",
                "name": "Precision Form",
                "heading": "WORK WITHOUT LIMITS",
                "sub_heading": "Precision and control at every step",
                "generation_prompt": (
                    "STRICT SQUARE 1:1, 1024x1024, 8k ultra-sharp, high-end 3D render. "
                    "SCENE: A perfect crystalline geometric form — translucent prism or segmented sphere — "
                    "floating center-right against a deep dark background with a subtle color gradient. "
                    "Warm amber and cool blue light refraction through translucent surfaces. "
                    "Clean, minimal, premium. Left 50% open dark gradient. "
                    "Cinematic studio lighting, crisp edges, NO blur. "
                    "ABSOLUTE RULE: NO text, NO numbers, NO labels, NO UI, NO watermarks. "
                    "Pure abstract background visual only."
                ),
            },
            {
                "layout": "Cinematic",
                "layout_type": "center-text",
                "name": "Aspirational Moment",
                "heading": "YOUR NEXT LEVEL",
                "sub_heading": "Take control and move forward with confidence",
                "generation_prompt": (
                    "STRICT SQUARE 1:1, 1024x1024, 8k ultra-sharp, photorealistic cinematic. "
                    "SCENE: A single figure seen from extreme back-angle or side profile, "
                    "standing in a vast minimalist architectural space, facing toward a large "
                    "abstract warm amber glowing light source filling the horizon. "
                    "The environment is dark, moody, and cinematic — NO screens, NO UI, NO text on any surface. "
                    "High contrast. Bottom third slightly darker gradient for text overlay. "
                    "ABSOLUTE RULE: NO text, NO numbers, NO labels, NO typography anywhere. "
                    "Pure background visual only."
                ),
            },
        ]

    logger.info(f"Generating {len(visual_concepts)} image variants (background + PIL compositing)...")

    final_logo = logo_bytes
    if final_logo:
        logger.info("Normalising brand logo to 1:1 square...")
        final_logo = _pad_image_to_square(final_logo)

    results = []
    for i in range(len(visual_concepts)):
        attempts = 0
        best_rating = -1
        best_variant_data = None

        while attempts < 3:
            logger.info(f"Variant {i + 1} — Attempt {attempts + 1}/3...")
            variant_data = _gen_single_variant(
                i,
                visual_concepts,
                logo_bytes=final_logo,
                primary_color=primary_color,
                domain_name=domain_name,
            )

            if not variant_data:
                break

            logger.info(f"Running Visual Audit for Variant {i + 1}...")
            audit = _visual_critic_agent(
                variant_data['bytes'],
                visual_concepts[i].get('generation_prompt', ''),
                refined_brief,
                primary_color,
                heading=variant_data.get('heading', ''),
                sub_heading=variant_data.get('sub_heading', ''),
            )

            rating = audit.get('rating_out_of_10', 8)
            reason = audit.get('reason', 'N/A')
            advice = audit.get('improvement_advice', 'Improve visual relevance.')

            logger.info(f"Audit — Variant {i + 1} Attempt {attempts + 1}: {rating}/10")

            if rating > best_rating:
                best_rating = rating
                best_variant_data = variant_data
                best_variant_data['rating_out_of_10'] = rating
                best_variant_data['audit_reason'] = reason
                best_variant_data['attempts_count'] = attempts + 1

            if rating >= 6:
                logger.info(f"Variant {i + 1} PASSED ({rating}/10).")
                break

            logger.warning(f"Variant {i + 1} scored {rating}/10. Retrying with critic directive.")
            visual_concepts[i]['generation_prompt'] = (
                f"IMPROVEMENT DIRECTIVE: {advice}\n\n"
                f"ORIGINAL PROMPT: {visual_concepts[i]['generation_prompt']}"
            )
            attempts += 1

        if best_variant_data:
            best_variant_data.pop('bytes', None)
            results.append(best_variant_data)

    return results
