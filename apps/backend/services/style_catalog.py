"""Image style catalog — single source of truth.

Fifteen curated visual styles + AUTO sentinel.

Two behaviours drive off this file:

  • AUTO: the pipeline uses the ORIGINAL Art Director system prompt
    verbatim (TEMPLATE A + TEMPLATE B with "figure out a visual style"
    language). Zero prompt change from before this feature existed.

  • Any explicit style: the ORIGINAL system prompt is REPLACED by a
    style-first system prompt built dynamically from the style's
    `visual_dna` block. TEMPLATE A/B don't exist in this prompt, so
    there's no "figure out a style" language to fight and no
    "real-person required" rule to override.

The frontend fetches this catalog via GET /config/image-styles.
"""
from __future__ import annotations

from typing import Optional


AUTO_STYLE = "auto"


# Every explicit-style entry has a `visual_dna` dict with 6 fields the
# dynamic system-prompt builder reads:
#
#   composition        — how to lay out the scene
#   palette            — colour rules (brand colour stays accent-only)
#   typography         — text treatment
#   mood               — lighting, atmosphere, energy
#   elements_to_include — what MUST appear
#   elements_to_avoid  — visual patterns that would break the style
#
# Every field is a plain sentence — no template variables. The builder
# assembles them into a coherent system prompt at request time.

IMAGE_STYLES: dict[str, dict] = {
    # ── ✨ Auto — default, no prompt changes ────────────────────────
    AUTO_STYLE: {
        "label": "Auto",
        "group": "auto",
        "emoji": "✨",
        "when_to_use": "Let AI pick the style from your brand DNA and industry",
        "prompt_hint": "",   # empty → no injection anywhere
        "visual_dna": None,  # None → use the default Art Director system prompt
    },

    # ── 🛍️ Physical Product ────────────────────────────────────────
    "studio_product_shot": {
        "label": "Studio Product Shot",
        "group": "physical_product",
        "emoji": "📸",
        "when_to_use": "New product launches, catalog hero shots, marketplace listings",
        "prompt_hint": (
            "clean studio product photography on white or subtle gradient background, "
            "soft even lighting, sharp product focus, e-commerce catalog quality"
        ),
        "visual_dna": {
            "composition": "Product centred in frame at eye-level or 3/4 view, generous negative space around it, no props, no environmental context, no lifestyle setting.",
            "palette": "Predominantly white, off-white, or a very subtle single-hue gradient background. Product's own colours are the only saturation source.",
            "typography": "Minimalist bold sans-serif for headline, small clean caption text. Never overlapping the product.",
            "mood": "Neutral, premium, catalog-clean. Soft even lighting with subtle contact shadow.",
            "elements_to_include": "Only the product itself, brand logo top-left, small CTA button, one optional feature-callout label.",
            "elements_to_avoid": "Backgrounds with texture, props, human figures, environmental scenes, dramatic lighting, coloured seamless paper.",
        },
    },
    "lifestyle_on_body": {
        "label": "Lifestyle / On-Body",
        "group": "physical_product",
        "emoji": "🌟",
        "when_to_use": "Fashion, jewelry, watches, cosmetics, wearables — model using product",
        "prompt_hint": (
            "aspirational lifestyle photography, real model authentically using or "
            "wearing the product, warm daylight, editorial fashion quality"
        ),
        "visual_dna": {
            "composition": "One real human model naturally using / wearing the product in a plausible real-world setting. Product must be clearly visible on-body; not next to the model.",
            "palette": "Warm natural daylight tones. Muted earthy background palette. Brand colour appears only in a small graphic element or CTA.",
            "typography": "Editorial sans-serif with generous letter-spacing. Small, tasteful.",
            "mood": "Aspirational, authentic, editorial fashion / lifestyle magazine.",
            "elements_to_include": "The model wearing/using the product, natural environment (café, street, home, studio), brand logo top-left, subtle CTA.",
            "elements_to_avoid": "Studio backdrops, product-only shots, cartoon or illustration styling, harsh flash, over-saturated colours, decorative typography.",
        },
    },
    "flat_lay": {
        "label": "Flat Lay",
        "group": "physical_product",
        "emoji": "🍃",
        "when_to_use": "Fashion accessories, beauty, food, gift boxes — Instagram-worthy overhead",
        "prompt_hint": (
            "overhead flat-lay composition on a curated surface, aesthetic prop "
            "arrangement, symmetrical layout, natural top-down lighting"
        ),
        "visual_dna": {
            "composition": "Strict top-down / overhead camera. Product plus 3-6 aesthetic props arranged around it in a rhythmic, near-symmetrical layout on a curated surface (marble, wood, textured paper, linen).",
            "palette": "Warm neutral surface tones (cream, beige, marble grey). Prop colours coordinate; brand colour appears only in the CTA or a single prop.",
            "typography": "Delicate sans-serif or subtle serif for headline. Small, magazine-styled.",
            "mood": "Slow, considered, editorial. Natural window light from one side.",
            "elements_to_include": "Overhead product, curated relevant props (herbs, fabric, flowers, ceramics, journal, coffee), brand logo top-left, small CTA.",
            "elements_to_avoid": "Human figures, angled or eye-level cameras, 3D renders, dramatic side lighting, digital screens/dashboards, cluttered backgrounds.",
        },
    },
    "macro_close_up": {
        "label": "Macro Close-up",
        "group": "physical_product",
        "emoji": "🔬",
        "when_to_use": "Jewelry, watches, cosmetics — extreme detail work",
        "prompt_hint": (
            "extreme macro close-up photography, sharp focus on product texture, "
            "luxurious depth of field, soft rim lighting to highlight craftsmanship"
        ),
        "visual_dna": {
            "composition": "Extreme close-up of the product filling most of the frame. Focus on the texture, material, and craftsmanship detail. Subtle background falloff.",
            "palette": "Dark or moody neutral background so the product materials pop. Rim lighting in cool or warm tone depending on product finish.",
            "typography": "Small, elegant serif or fine sans-serif for a single headline. Never over the product's hero area.",
            "mood": "Luxurious, precious, artisanal. Soft rim lighting highlights craftsmanship.",
            "elements_to_include": "Extreme detail view of the product's most beautiful surface, single brand logo, one small caption.",
            "elements_to_avoid": "Wide shots, multiple props, human figures, flat lighting, cluttered scenes, bright saturated backgrounds.",
        },
    },
    "3d_product_render": {
        "label": "3D Product Render",
        "group": "physical_product",
        "emoji": "💎",
        "when_to_use": "Tech gadgets, hardware, sneakers, futuristic products",
        "prompt_hint": (
            "studio-lit 3D product render, dramatic cinematic lighting, glossy "
            "premium finish, floating perspective with soft shadow"
        ),
        "visual_dna": {
            "composition": "3D-rendered product floating or on a minimalist platform, hero angle, dramatic perspective. Feels like a Pixar/Apple product shot.",
            "palette": "Rich saturated brand-aligned background gradient. Product materials rendered with cinematic light bouncing (glossy plastic, brushed metal, glass).",
            "typography": "Clean geometric sans-serif with subtle 3D depth. Never cartoonish.",
            "mood": "Cinematic, premium, futuristic. Dramatic three-point studio lighting with visible highlights.",
            "elements_to_include": "3D-rendered hero product, soft contact shadow, atmospheric particles or subtle glow, brand logo top-left, CTA.",
            "elements_to_avoid": "Real-world photography, flat 2D illustration, cartoon characters, hand-drawn textures, cluttered environments, human figures.",
        },
    },
    "cinematic": {
        "label": "Cinematic",
        "group": "physical_product",
        "emoji": "🎬",
        "when_to_use": "Luxury products, brand storytelling, high-end lifestyle",
        "prompt_hint": (
            "cinematic film-grade frame, moody atmospheric lighting, film grain "
            "texture, 2.35:1 editorial composition, luxury brand storytelling energy"
        ),
        "visual_dna": {
            "composition": "Film-still framing with strong asymmetry or negative space. Product or subject placed off-centre. Deep foreground / midground / background layers.",
            "palette": "Moody, desaturated palette. Deep shadows, warm highlights. Teal/orange or amber/black cinematic grade.",
            "typography": "Editorial serif or elegant sans-serif with generous kerning. Small.",
            "mood": "Cinematic, atmospheric, story-driven. Moody dramatic lighting with directional falloff.",
            "elements_to_include": "Hero subject, layered depth, atmospheric haze or light bloom, brand logo top-left, subtle CTA.",
            "elements_to_avoid": "Bright even lighting, catalog-clean backgrounds, cartoon or illustration styling, saturated primary colours, 3D floating renders.",
        },
    },

    # ── 💼 Service / SaaS / Content ────────────────────────────────
    "photorealistic": {
        "label": "Photorealistic",
        "group": "creative",
        "emoji": "📷",
        "when_to_use": "Real-world use cases, testimonials, team shots, event coverage",
        "prompt_hint": (
            "hyper-realistic photograph, natural lighting, sharp focus, 8K DSLR "
            "quality, authentic real-world scene"
        ),
        "visual_dna": {
            "composition": "Documentary-style photograph. Real people or real environments in a plausible everyday setting. Natural angles.",
            "palette": "Realistic natural colour reproduction. Brand colour appears only in wardrobe/props or CTA.",
            "typography": "Clean modern sans-serif overlay. Never intrusive.",
            "mood": "Authentic, documentary, real-world. Natural window light or realistic interior lighting.",
            "elements_to_include": "Real people or real place, natural props, brand logo top-left, small CTA.",
            "elements_to_avoid": "3D renders, illustrations, cartoon styling, over-saturated colours, magazine-perfect lighting, decorative typography.",
        },
    },
    "infographic": {
        "label": "Infographic",
        "group": "creative",
        "emoji": "📊",
        "when_to_use": "ROI callouts, feature comparisons, stat-heavy posts, before/after",
        "prompt_hint": (
            "clean 2D flat infographic composition with prominent stat callouts, bold "
            "numeric emphasis, iconography, structured grid layout, data-driven "
            "visual hierarchy"
        ),
        "visual_dna": {
            "composition": "Flat 2D layout organised on a clean grid. Big stat numbers as hero elements. Small iconography around them. Structured hierarchy: headline → hero stat → supporting bullets. NEVER 3D dashboards or product mockups.",
            "palette": "White or very light neutral background. 2-3 flat colours max plus brand colour as accent on the key stat. High contrast for readability.",
            "typography": "Bold sans-serif for stat numbers (large), medium weight for labels, small readable body. All flat, no 3D depth on type.",
            "mood": "Analytical, clear, information-dense but breathable. Even flat lighting — this is a designed graphic, not a photograph.",
            "elements_to_include": "Big stat numbers, flat icons, thin dividers, simple bar/pie/line chart shapes, small callout labels, brand logo top-left, CTA button.",
            "elements_to_avoid": "3D rendered elements, glossy dashboards, product screenshots inside device frames, photographic backgrounds, human figures, glowing effects, cinematic depth of field, decorative 3D characters or mascots.",
        },
    },
    "illustration": {
        "label": "Illustration",
        "group": "creative",
        "emoji": "✏️",
        "when_to_use": "Editorial LinkedIn posts, thought leadership, tech explainers",
        "prompt_hint": (
            "flat 2D vector illustration, editorial magazine style, clean line work, "
            "bold color palette, professional storytelling"
        ),
        "visual_dna": {
            "composition": "Flat 2D vector illustration. Single hero scene or metaphorical illustration. Editorial magazine style. Clean linework, deliberate composition.",
            "palette": "3-5 bold flat colours. Brand colour features prominently as an accent. High contrast, no gradients.",
            "typography": "Editorial sans-serif for headline. Complements the illustration, doesn't compete.",
            "mood": "Thoughtful, editorial, storytelling. Even flat lighting.",
            "elements_to_include": "Flat vector characters or metaphorical objects, clean line details, geometric shapes, brand logo top-left, CTA.",
            "elements_to_avoid": "Photographic elements, 3D renders, glossy effects, realistic textures, decorative 3D mascots.",
        },
    },
    "isometric": {
        "label": "Isometric",
        "group": "creative",
        "emoji": "🧊",
        "when_to_use": "SaaS integrations, workflow visualizations, tech process explainers",
        "prompt_hint": (
            "isometric 3D perspective, tech-forward composition, clean geometric "
            "shapes, floating platforms, integration/workflow-friendly layout"
        ),
        "visual_dna": {
            "composition": "Isometric perspective (30-degree axonometric). Multiple floating platforms, boxes, or icons connected by dotted lines or beams. Clean geometric shapes only.",
            "palette": "Flat modern colours with subtle drop shadows. Brand colour accents connections and key nodes.",
            "typography": "Clean geometric sans-serif. Small labels next to icons or nodes.",
            "mood": "Tech-forward, structured, process-explaining. Even flat lighting.",
            "elements_to_include": "Isometric platforms/nodes/icons, connection lines, small tech icons (cloud, database, gear, chart), brand logo top-left, CTA.",
            "elements_to_avoid": "Photorealistic 3D, dramatic lighting, human figures at scale, environmental scenes, cartoon characters, decorative flourishes.",
        },
    },
    "ui_mockup": {
        "label": "UI Mockup / Device Frame",
        "group": "creative",
        "emoji": "📱",
        "when_to_use": "SaaS feature launches, app releases, product demos",
        "prompt_hint": (
            "app or software UI mockup shown inside a realistic device frame, "
            "soft screen glow, subtle drop shadow, minimal product-focused background"
        ),
        "visual_dna": {
            "composition": "Realistic laptop or phone device frame with an app UI mockup displayed on the screen. Device angled slightly. Minimal background lets device be the hero.",
            "palette": "Neutral or subtle-gradient background. Brand colour appears inside the UI mockup (buttons, active states, chart accents).",
            "typography": "Clean sans-serif system UI for the on-screen mockup. Small caption or headline outside the device.",
            "mood": "Product-focused, feature-launch, launch-post energy. Soft screen glow and subtle drop shadow.",
            "elements_to_include": "Realistic device frame (MacBook, iPhone), UI mockup on screen with dashboard/charts/typography, brand logo top-left, CTA.",
            "elements_to_avoid": "Human figures, dramatic environmental backgrounds, cartoon or watercolor styling, 3D non-device elements floating around.",
        },
    },

    # ── 🎭 Creative / Stylistic ────────────────────────────────────
    "minimalist_flat": {
        "label": "Minimalist Flat",
        "group": "creative",
        "emoji": "◽",
        "when_to_use": "Design agencies, high-end SaaS, luxury minimalism",
        "prompt_hint": (
            "minimalist flat design, generous negative space, single accent color "
            "on neutral background, Scandinavian aesthetic, restrained composition"
        ),
        "visual_dna": {
            "composition": "Extreme minimalism. One or two flat elements on a large neutral background. Generous negative space is the hero.",
            "palette": "Off-white / cream / bone background. One single accent colour (brand colour). Nothing else.",
            "typography": "Bold minimal sans-serif. Very few words. Confident.",
            "mood": "Confident, restrained, high-end. Even flat lighting.",
            "elements_to_include": "One flat shape or icon, headline, brand logo top-left, thin CTA.",
            "elements_to_avoid": "Photography, 3D renders, gradients, textures, multiple colours, decorative flourishes.",
        },
    },
    "cartoon": {
        "label": "Cartoon",
        "group": "creative",
        "emoji": "🎨",
        "when_to_use": "D2C consumer, gaming, kids' products, playful campaigns",
        "prompt_hint": (
            "playful cartoon illustration, bold black outlines, expressive characters, "
            "saturated colors, comic-strip energy"
        ),
        "visual_dna": {
            "composition": "Playful cartoon scene. Expressive characters or objects in an animated pose. Comic-strip energy.",
            "palette": "Saturated bold colours. Brand colour features prominently. High contrast.",
            "typography": "Playful hand-drawn or bold cartoon sans-serif. Sometimes inside a speech bubble.",
            "mood": "Playful, expressive, informal, fun.",
            "elements_to_include": "Cartoon characters, bold black outlines, expressive facial expressions, speech bubbles, brand logo top-left, playful CTA.",
            "elements_to_avoid": "Photorealistic elements, corporate flat design, 3D Pixar-style renders (this is 2D cartoon), moody lighting.",
        },
    },
    "watercolor": {
        "label": "Watercolor",
        "group": "creative",
        "emoji": "💧",
        "when_to_use": "Wellness, food, artisan/boutique brands, weddings",
        "prompt_hint": (
            "hand-painted watercolor style, soft color washes, textured paper feel, "
            "organic brush strokes, artisanal warmth"
        ),
        "visual_dna": {
            "composition": "Hand-painted watercolour scene. Soft edges, natural composition. Feels like an artist's illustration on textured paper.",
            "palette": "Muted watercolour tones with visible pigment blooms. Brand colour appears as one subtle wash.",
            "typography": "Elegant serif or handwritten script. Soft, understated.",
            "mood": "Artisanal, warm, considered. Soft natural daylight feeling.",
            "elements_to_include": "Watercolour brush strokes, soft washes, visible paper texture, hand-drawn elements, brand logo top-left, small CTA.",
            "elements_to_avoid": "Sharp digital edges, 3D renders, glossy surfaces, saturated flat colours, corporate typography, photography.",
        },
    },
    "cyberpunk_neon": {
        "label": "Cyberpunk / Neon",
        "group": "creative",
        "emoji": "🌆",
        "when_to_use": "AI products, gaming, Web3, futuristic tech",
        "prompt_hint": (
            "cyberpunk neon aesthetic, glowing accent lights, dark atmospheric "
            "background, holographic UI overlays, futuristic tech energy"
        ),
        "visual_dna": {
            "composition": "Dark atmospheric scene with holographic UI overlays, floating panels, neon light strips outlining hero elements. Futuristic tech energy dominates.",
            "palette": "Deep navy / purple / black background. Cyan, magenta, hot pink neon accents. Brand colour amplified with a neon glow effect.",
            "typography": "Bold sans-serif with neon glow effect. Optional monospace/digital-style secondary text.",
            "mood": "Futuristic, energetic, tech-forward. Slight cyberpunk grit.",
            "elements_to_include": "Neon light strips, holographic UI panels, glowing icons, subtle circuit-line patterns, chromatic aberration on edges, brand logo top-left, glowing CTA.",
            "elements_to_avoid": "Natural daylight, pastel colours, hand-drawn textures, watercolor washes, 3D Pixar-style characters, warm cozy environments.",
        },
    },
    "overseas_agency": {
        "label": "Overseas Education Flyer",
        "group": "travel_immigration",
        "emoji": "🌍",
        "when_to_use": "Study-abroad / immigration consultancies — country landing pages, university line-ups, intake deadlines, visa services. IMPORTANT: universities and contact info are pulled ONLY from the campaign brief / business overview / business DNA — nothing is invented.",
        "prompt_hint": (
            "vertical study-abroad consultancy flyer, dominant navy-blue + red + "
            "white palette, a real smiling student holding the destination "
            "country's flag with an iconic landmark of that country in the "
            "background, big bold display country name, 'Your Dream University "
            "in [COUNTRY]' handwritten script tagline above it, EASY ADMISSION "
            "COLLEGES red banner sub-header, horizontal row of 5 circular icons "
            "for value props, TOP & EASY ADMISSION UNIVERSITIES section with a "
            "clean 4-card grid of universities each showing logo + location + "
            "3-4 bullets — universities are listed ONLY if explicitly named in "
            "the campaign brief, business overview, or business DNA (otherwise "
            "the section is dropped, NOT filled with invented university "
            "names). Bottom banner with phone numbers and CALL US TODAY button "
            "— phone numbers, emails, and addresses are pulled EXACTLY from "
            "the campaign brief / business overview / business DNA / auto-"
            "extracted CONTACT INFO block. Do NOT invent, do NOT hallucinate. "
            "Brand logo top-left, small graduation-cap badge top-right"
        ),
        "visual_dna": {
            "composition": (
                "GROUNDING RULE — read BEFORE designing: this style renders "
                "ONLY what the campaign brief, business overview, business "
                "DNA document, and auto-extracted CONTACT INFO block "
                "explicitly provide. UNIVERSITY NAMES, PHONE NUMBERS, "
                "EMAILS, ADDRESSES, WEBSITE URLS, INTAKE DATES, TUITION "
                "FEE FIGURES, and any specific factual claim must come "
                "from those sources verbatim. If a piece of information "
                "is NOT provided, drop the section or replace it with a "
                "generic value-prop badge — NEVER invent placeholder "
                "'University of X', '+1-800-555-0100', or 'info@example.com' "
                "content. Hallucinated university names or fake phone "
                "numbers are the #1 forbidden mistake in this style.\n\n"
                "Vertical portrait flyer with 4 clear stacked sections "
                "separated by clean whitespace: (1) TOP HERO — brand logo "
                "TOP-LEFT + small graduation-cap 'QUALITY EDUCATION / "
                "BRIGHT FUTURE' badge TOP-RIGHT. A calligraphic script "
                "tagline 'Your Dream University in' sits above a MASSIVE "
                "display country name — the country is taken from the "
                "brief ('AUSTRALIA' / 'CANADA' / 'UK' / 'IRELAND' / "
                "'GERMANY' / 'USA' etc. — only what the brief says). A "
                "red pill-banner underneath reads 'EASY ADMISSION "
                "COLLEGES' followed by a smaller sub-banner 'STUDY IN "
                "[COUNTRY] - BUILD YOUR FUTURE'. To the right of the "
                "headline, a real smiling student holds the destination "
                "country's flag with an iconic landmark of that country "
                "in the background (Sydney Harbour Bridge for Australia, "
                "CN Tower for Canada, Big Ben for UK, etc — the landmark "
                "MUST match the country named in the brief). A small "
                "aeroplane with a dotted contrail arcs across the sky. "
                "(2) VALUE-PROP STRIP — one horizontal row of 5 circular "
                "icons (navy or red circle) each with a short label: "
                "EASY ADMISSION, AFFORDABLE TUITION FEES, HIGH VISA "
                "SUCCESS RATE, PART TIME WORK, POST STUDY WORK RIGHTS/"
                "PERMIT. Each icon has 2-3 words of supporting caption "
                "underneath. These 5 are STANDARD value-props for this "
                "consultancy category and are safe defaults. "
                "(3) UNIVERSITY GRID — MANDATORY whenever the campaign "
                "brief, business overview, or business DNA documents list "
                "ANY university names for the destination country. This "
                "is the whole point of an overseas-agency flyer — a "
                "flyer without visible university names is a failure. "
                "Extract UP TO 8 universities from the source (prefer "
                "the most prestigious / most recognisable first — e.g. "
                "Russell Group first for the UK, U15 first for Canada, "
                "Ivy League first for the USA — then fill the rest). "
                "Layout: choose the tightest grid that fits the count — "
                "8 in 2x4, 6 in 2x3, 4 in 2x2, 2 in 1x2. "
                "MINIMUM REQUIRED per card (BOTH must appear): "
                "  (a) The UNIVERSITY LOGO rendered at the top of the "
                "      card. Render each university's actual logo based "
                "      on the university NAME given — real universities "
                "      have widely-recognisable visual identities "
                "      (brand colours, shield / crest / seal shape, "
                "      wordmark style). Do your best from the name. "
                "      If a real logo reference image is separately "
                "      attached, use it verbatim in preference. "
                "  (b) The UNIVERSITY NAME rendered large and fully "
                "      legible below the logo, verbatim as spelled in "
                "      the source. "
                "OPTIONAL per card (include ONLY when the source "
                "provides them; leave blank otherwise): a small red "
                "'Located in [City], [Region]' line under the name, and "
                "2-4 program bullets under that. Never invent city, "
                "region, ranking, tuition, or program details that are "
                "not in the source — those fields are dropped, not "
                "guessed. NEVER replace real university names with "
                "grey/blank placeholder rectangles or 'illegible lines' "
                "to satisfy text-density concerns — the density rules "
                "for this style permit lists like this. NEVER substitute "
                "'300+ university partners worldwide' as a paragraph in "
                "place of the actual named-card grid. The paragraph "
                "summary is fine as a SUBHEADING above the grid; the "
                "grid itself must show real names. Only if the source "
                "provides ZERO university names for the destination "
                "country (empty list, not merely 'names without cities') "
                "may this section be dropped and replaced with an "
                "expanded value-prop strip. "
                "(4) BOTTOM CONTACT STRIP — a red or navy horizontal "
                "banner with the brand's own logo/wordmark on the left "
                "(the brand's real name, taken from Business DNA), a red "
                "'CALL US TODAY!' or 'CALL US NOW' pill in the middle "
                "with a phone icon, and phone numbers below it PULLED "
                "VERBATIM from the auto-extracted CONTACT INFO block "
                "OR from the campaign brief / business overview / "
                "business DNA. Additional contact rows (email address, "
                "website URL, physical address) also pulled VERBATIM "
                "from those sources — only render what is actually "
                "provided. If NO phone number is present in any source, "
                "OMIT the phone row entirely — do NOT print a fake or "
                "placeholder number. Right-side vertical stack of "
                "check-mark support items (Expert Counselors / Visa "
                "Assistance / Education Loan Support / End to End "
                "Support) — these are standard category value-props and "
                "are safe defaults. Underneath the strip, a thin red "
                "bar with the tagline 'YOUR DREAM. OUR COMMITMENT. YOUR "
                "FUTURE!' — a category-standard tagline, always safe."
            ),
            "palette": "Strict tri-colour scheme — DEEP NAVY BLUE (~#0A2A66) for headers, backgrounds of feature strips, and typography; VIVID RED (~#D0021B) for accent banners, the country name letters, CTA pills, and highlight bars; CLEAN WHITE for the card and grid background. NO other colours in the layout chrome — the only additional colour comes from the destination country's flag being held by the student and from natural sky tones in the landmark photograph.",
            "typography": "MASSIVE extra-bold condensed sans-serif for the country name in either RED or NAVY (like a printed passport stamp). A flowing hand-lettered SCRIPT font in navy for the 'Your Dream University in' line above the country name. Clean bold sans-serif ALL CAPS for the red banners ('EASY ADMISSION COLLEGES', 'TOP & EASY ADMISSION UNIVERSITIES'). Structured sans-serif for university names in the grid (SMALL CAPS or Title Case). Clean readable body sans-serif for the bullet points, value-prop labels, and contact strip. Oversized bold numerals for the phone numbers.",
            "mood": "Professional, trustworthy, aspirational — the promise of a better future through overseas education. This is a printed IMMIGRATION-CONSULTANCY flyer or newspaper insert, NOT a tech poster and NOT a cyberpunk graphic. Bright natural daylight on the student and landmark photo. Confident, credible, printed-brochure feel.",
            "elements_to_include": (
                "ONE real smiling student (fresh-faced, professional "
                "attire) holding the DESTINATION COUNTRY's flag; the "
                "destination country's most recognisable landmark "
                "clearly visible in the background sky (matching the "
                "country named in the brief — Sydney Harbour Bridge, CN "
                "Tower, Big Ben and Westminster, Statue of Liberty, "
                "Trinity College Dublin, etc); a small aeroplane with a "
                "dotted contrail arc showing 'journey to that country'; "
                "a graduation-cap badge chip top-right; 5 circular value-"
                "prop icons in a horizontal strip (standard category "
                "props — safe to render). "
                "CONDITIONAL: a clean UNIVERSITY GRID (2x2 up to 2x4) "
                "WHEN AND ONLY WHEN the campaign brief / business "
                "overview / business DNA explicitly lists specific "
                "university names — each card uses the SOURCE-PROVIDED "
                "name, source-provided city / region, and source-"
                "provided bullet points. When no universities are "
                "listed, DROP the grid section (replace with expanded "
                "value-props or a testimonial block if provided). "
                "CONDITIONAL: a bright red 'CALL US TODAY' / 'CALL US "
                "NOW' pill CTA. CONDITIONAL: phone-icon + phone number "
                "lines pulled VERBATIM from the auto-extracted CONTACT "
                "INFO block or the campaign brief / business overview / "
                "business DNA. CONDITIONAL: email row and website row "
                "and physical address row — each rendered ONLY if the "
                "value is provided in one of those source documents. "
                "A bottom tagline banner 'YOUR DREAM. OUR COMMITMENT. "
                "YOUR FUTURE!' (category-standard, always safe). Brand "
                "logo top-left (using the actual brand's provided logo)."
            ),
            "elements_to_avoid": (
                "HALLUCINATED UNIVERSITY NAMES — this is the #1 "
                "forbidden mistake. Never invent 'University of X', "
                "'ABC College of Engineering', 'International Institute "
                "of Y', or any specific university name that is not "
                "spelled out in the campaign brief / business overview / "
                "business DNA. If no universities are listed, drop the "
                "entire grid section. HALLUCINATED CONTACT INFO — never "
                "invent phone numbers, email addresses, physical "
                "addresses, or website URLs. Only render values pulled "
                "verbatim from the source documents / auto-extracted "
                "CONTACT INFO block. INVENTED INTAKE DATES / TUITION "
                "FIGURES / SCHOLARSHIP AMOUNTS — never fabricate "
                "specific numbers or dates; only render what's in the "
                "source. Landmarks that don't match the destination "
                "country (no Eiffel Tower on a UK poster). Dark "
                "cyberpunk backgrounds; neon glow effects; holographic "
                "tech overlays; floating code or dashboards; cartoon / "
                "illustrated / watercolour styling; minimalist single-"
                "hero layouts (this flyer MUST be information-dense); "
                "pastel colour palettes; single-university posters when "
                "a country grid was requested; more than one student in "
                "the hero (one smiling face only); casual party-style "
                "photos."
            ),
        },
    },
    # ── 🚨 Deadline Urgency Flyer (final-call intake / deadline) ────
    "deadline_urgency_flyer": {
        "label": "Deadline Urgency Flyer",
        "group": "travel_immigration",
        "emoji": "🚨",
        "when_to_use": (
            "Study-abroad / immigration consultancies announcing "
            "APPLICATION DEADLINES, closing intake windows, final-call "
            "urgency, last-chance campaigns. Dramatic typographic "
            "poster: a white flyer being posted into a bold red "
            "mailbox / letter slot / envelope scene, big countdown-"
            "style headline, red 'deadlines closing soon' banner, "
            "4-icon value strip, navy tagline bar, contact footer. "
            "Country and deadline text pulled VERBATIM from the "
            "campaign brief / business DNA — no invented dates, "
            "no fake scholarship figures. Best for briefs that use "
            "words like 'final call', 'deadline', 'closing soon', "
            "'last chance', 'apply now', 'intake closing'."
        ),
        "prompt_hint": (
            "vertical urgent-deadline flyer for a study-abroad / "
            "immigration consultancy — dominant RED background with a "
            "clean WHITE flyer posted into the scene by a hand from "
            "the right edge, brand logo top-left on the flyer, MASSIVE "
            "extra-bold condensed sans-serif headline reading 'FINAL "
            "CALL FOR [COUNTRY] ADMISSIONS' with the country in a "
            "gigantic navy display size and the word 'ADMISSIONS' on a "
            "red pill banner, followed by a smaller red pill with "
            "calendar icon reading 'APPLICATION DEADLINES ARE CLOSING "
            "SOON!', a 4-icon value strip (Top [COUNTRY] Universities, "
            "Fast-Track Application Support, Scholarships Available*, "
            "End-to-End Visa Guidance), a navy full-width strip with "
            "the tagline 'APPLY TODAY. SECURE YOUR FUTURE TOMORROW.', "
            "and a bottom red contact strip with address (if provided) "
            "and phone number pulled VERBATIM from the CONTACT INFO "
            "block / campaign brief / business DNA. Do not invent "
            "phone numbers, addresses, or deadlines"
        ),
        "visual_dna": {
            "composition": (
                "GROUNDING RULE — read BEFORE designing: country name, "
                "deadline dates, phone numbers, address, and any "
                "scholarship figure MUST come from the campaign brief, "
                "business overview, business DNA document, or auto-"
                "extracted CONTACT INFO block. If any specific figure "
                "or date is NOT provided, DROP that piece — never "
                "invent 'February 15th' / '$5000 scholarship' / a "
                "placeholder phone number.\n\n"
                "Vertical portrait poster (9:16 / 4:5) in TWO distinct "
                "planes: (1) a dominant SOLID RED BACKGROUND (deep "
                "brand-red or vivid #D0021B) filling the whole canvas "
                "and suggestive of a large red mailbox / letter slot / "
                "envelope — subtle shadowed grooves at the top hint at "
                "a mail slot, faint envelope-edge geometry near the "
                "top corner. On the RIGHT edge, one photographic hand "
                "(realistic, sharply focused) enters the frame holding "
                "the flyer from its lower-right corner, as if the "
                "person just posted it. Bright natural light, no blur. "
                "(2) In the FOREGROUND, ONE clean WHITE flyer occupies "
                "the central 80-90 percent of the canvas, slightly "
                "angled with a soft realistic drop shadow, containing "
                "all the flyer content described below.\n\n"
                "FLYER CONTENT STACK (top to bottom, WHITE background):\n"
                "  (A) TOP HEADER STRIP — brand logo TOP-LEFT (using "
                "  the attached logo verbatim), and a small right-side "
                "  tag (optional) like '4 UK UNIVERSITIES' or the "
                "  brand's short strapline in tiny navy sans-serif "
                "  ONLY when the source provides it.\n"
                "  (B) HEADLINE BLOCK — three-line dramatic typography, "
                "  center-aligned, no serifs:\n"
                "      Line 1: 'FINAL CALL' in bold navy sans-serif, "
                "      medium-large size.\n"
                "      Line 2: 'FOR' in a smaller red script or italic "
                "      accent centered between two thin red rule "
                "      lines.\n"
                "      Line 3: The [COUNTRY] name in HUGE extra-bold "
                "      condensed navy — takes up 1/3 of the flyer "
                "      width (e.g. 'UK', 'CANADA', 'USA', 'IRELAND', "
                "      'AUSTRALIA', 'GERMANY').\n"
                "      Line 4: 'ADMISSIONS' rendered white on a bold "
                "      red pill/banner spanning most of the flyer "
                "      width, slightly tilted for energy.\n"
                "  (C) DEADLINE URGENCY BANNER — small navy heading "
                "  'APPLICATION DEADLINES' followed by a red pill with "
                "  white bold text 'ARE CLOSING SOON!' and a small "
                "  white calendar icon on the left. If the source "
                "  gives a SPECIFIC DEADLINE DATE, append it below in "
                "  smaller navy sans-serif (e.g. 'FEB 15, 2026 — LAST "
                "  DAY'). Otherwise omit the date line entirely.\n"
                "  (D) 4-ICON VALUE STRIP — a single horizontal row of "
                "  4 circular navy icons with simple white line "
                "  illustrations, each with a short 2-4 word label in "
                "  navy sans-serif underneath. Default set (safe when "
                "  the brief is silent on specifics): "
                "  'TOP [COUNTRY] UNIVERSITIES' (building icon), "
                "  'FAST-TRACK APPLICATION SUPPORT' (document icon), "
                "  'SCHOLARSHIPS AVAILABLE*' (grad-cap or wallet "
                "  icon), 'END-TO-END VISA GUIDANCE' (passport icon). "
                "  The asterisk after 'SCHOLARSHIPS AVAILABLE' hints "
                "  at conditions apply — safe default disclaimer.\n"
                "  (E) NAVY CTA STRIP — a full-width navy horizontal "
                "  strip beneath the icons with white bold small-caps "
                "  text 'APPLY TODAY. SECURE YOUR FUTURE TOMORROW.' "
                "  centered.\n"
                "  (F) CONTACT FOOTER STRIP — a full-width RED bottom "
                "  strip with two blocks:\n"
                "      • LEFT: small map-pin icon + address VERBATIM "
                "        from the source (single-line, wrap if "
                "        needed). If NO address is available, OMIT "
                "        the pin+address column entirely.\n"
                "      • RIGHT: red pill (or white pill on red) with "
                "        phone icon and phone number VERBATIM from "
                "        the CONTACT INFO block / campaign brief / "
                "        business DNA. If MULTIPLE phone numbers are "
                "        provided, render them separated by ' | '. "
                "        If NO phone number is available, render "
                "        only the email / website line instead — do "
                "        NOT invent a phone.\n\n"
                "NO UNIVERSITY GRID in this style — the point is "
                "urgency + CTA, not a partner catalogue. The single "
                "value-strip icon 'TOP [COUNTRY] UNIVERSITIES' "
                "summarises the offer without listing names.\n\n"
                "NO smiling-student-with-flag hero photo (that's the "
                "overseas_agency style). This flyer is the "
                "typography-first alternative in the same group."
            ),
            "palette": (
                "Bold, high-contrast tri-colour: dominant RED "
                "background (deep brand-red #D0021B or slightly "
                "warmer scarlet), deep navy blue (#0A2A66) for all "
                "primary typography and icons, crisp WHITE for the "
                "flyer background and inverse text on red pills. "
                "Brand colour <BRAND_COLOR> appears only as a thin "
                "accent (a rule under a heading, an icon outline, a "
                "tiny divider) — never dominating the flyer body. "
                "Avoid pastel tints, gradient washes, or muted tones — "
                "this style needs LOUD urgency-red contrast."
            ),
            "typography": (
                "Two families max. Primary: extra-bold condensed "
                "sans-serif for the giant country name and 'FINAL "
                "CALL' — imagine an Impact / Bebas Neue / Anton "
                "vibe. Secondary: clean modern sans-serif (Inter / "
                "Poppins / Manrope feel) for value-strip labels, "
                "deadline banner, and contact footer text. Optional "
                "third: a small hand-lettered red italic for the "
                "connector word 'FOR' between 'FINAL CALL' and the "
                "country. Every line of text must be legible at a "
                "distance — no fine print except the tiny "
                "'*conditions apply' hint next to 'SCHOLARSHIPS "
                "AVAILABLE*'."
            ),
            "mood": (
                "Urgent, decisive, professional. Bright natural light "
                "on the hand and the flyer (no dark shadows). Feels "
                "like a real photograph of someone posting a "
                "last-day-to-apply notice into a bold red slot — "
                "grounded, tangible, unmistakable call to action."
            ),
            "elements_to_include": (
                "SOLID RED BACKGROUND filling the canvas with subtle "
                "mail-slot / envelope-edge geometric hints. One real "
                "photographic HAND entering from the right edge "
                "holding the flyer at its lower-right corner. ONE "
                "clean WHITE flyer occupying the central 80-90 "
                "percent of the canvas with realistic drop shadow. "
                "Brand logo top-left on the flyer. Massive typographic "
                "'FINAL CALL FOR [COUNTRY] ADMISSIONS' headline "
                "stack. Red pill deadline banner with calendar icon. "
                "Row of 4 circular navy icons with short white/navy "
                "labels. Navy CTA strip with 'APPLY TODAY. SECURE "
                "YOUR FUTURE TOMORROW.' text. Bottom red contact "
                "strip with address (if provided) and phone (verbatim)."
            ),
            "elements_to_avoid": (
                "Smiling-student-with-flag hero photograph (that's "
                "the overseas_agency style — use that when the brief "
                "is a hero announcement, not a deadline). Multi-"
                "university logo grid (this poster is CTA-first, not "
                "catalogue-first). Landmarks / skyline photos in the "
                "flyer body (a small country-shape silhouette in the "
                "background is fine, a full landmark photo is not). "
                "Multi-column body paragraphs (dense body text kills "
                "the urgency). Cyberpunk / neon effects, holographic "
                "overlays, glassmorphism, cartoon or watercolour "
                "styling. Pastel or muted palettes. Invented deadline "
                "dates, phone numbers, addresses, or scholarship "
                "amounts. More than one hand or arm in the frame. "
                "Photorealistic reproductions of real mailbox brands "
                "(USPS / Royal Mail insignia etc. — keep the "
                "mailbox / slot generic)."
            ),
        },
    },
    # ── 🛕 Religious / Devotional (Hindu Temple event flyers) ──────
    "temple_event_flyer": {
        "label": "Temple Event Flyer (with Schedule)",
        "group": "religious",
        "emoji": "🛕",
        "when_to_use": (
            "Hindu / South-Asian temple events with a detailed "
            "schedule — Guru Purnima, Hanuman Jayanthi, Sankatahara "
            "Chaturthi, Diwali puja, Navaratri, Ganesh Chaturthi, "
            "any multi-time-slot festival. Full portrait flyer with "
            "marigold-and-mango-leaf frame, temple header, centered "
            "deity photograph, descriptive paragraph, time-by-time "
            "schedule table, and contact footer. All content pulled "
            "VERBATIM from the campaign brief — no hallucinated "
            "times, addresses, phone numbers, or event names."
        ),
        "prompt_hint": (
            "vertical Hindu temple event flyer — warm saffron / gold "
            "gradient background, decorative frame with green mango-"
            "leaf toran across the top, orange marigold flower "
            "strings, hanging brass diyas on both sides, palm-tree "
            "silhouettes on left and right edges; top-center temple "
            "header block (temple logo in oval frame + bold blue "
            "temple name + address + italic devotional tagline); "
            "श्रद्धा / सबूरी or brief-provided Devanagari accents "
            "on either side of the heading; bold red serif event "
            "heading; centered deity photograph from the reference "
            "image; short descriptive paragraph explaining the "
            "festival significance; schedule table with date "
            "header + rows of `time | event`; footer with phone / "
            "email / website / social — ALL taken verbatim from "
            "the campaign brief and Business DNA"
        ),
        "visual_dna": {
            "composition": (
                "GROUNDING RULE — read BEFORE designing: EVERY "
                "time, date, event name, temple name, address, "
                "phone number, email, website URL, Facebook / "
                "Instagram handle, and descriptive text on this "
                "flyer must come VERBATIM from the campaign brief "
                "or the Business DNA. NEVER invent event schedules. "
                "NEVER guess phone numbers. NEVER hallucinate a "
                "temple address. If a piece of info is missing, "
                "OMIT that row rather than fabricate. Hallucinated "
                "schedules or contact info are the #1 forbidden "
                "mistake in this style.\n\n"
                "VERTICAL PORTRAIT TEMPLE FLYER. Reference: Sri "
                "Shirdi Sai Baba Temple of DFW — 'Guru Purnima', "
                "'Hanuman Jayanthi', 'Sankatahara Chaturthi' "
                "flyers.\n\n"
                "STACKED SECTIONS (top → bottom):\n\n"
                "(1) DECORATIVE FRAME (surrounds the entire "
                "canvas):\n"
                "  • Top edge: a horizontal MANGO-LEAF TORAN — a "
                "string of glossy green mango leaves hanging in "
                "gentle arcs across the full width, with tiny "
                "orange marigold flower clusters between the leaf "
                "groups.\n"
                "  • Below the toran: a second string of ORANGE "
                "MARIGOLD FLOWERS (dense round marigold balls "
                "strung on thread) hanging in shallow loops.\n"
                "  • Left AND right edges: brass DIYA (oil lamp) "
                "hanging decorations at 2–3 vertical positions, "
                "each with a small flame; behind them, a subtle "
                "silhouette of a PALM TREE or coconut tree in "
                "warm-brown / dark-green.\n"
                "  • Corners softly decorated with small marigold "
                "clusters or subtle floral motifs.\n\n"
                "(2) TEMPLE HEADER BLOCK (top-center of the flyer, "
                "below the toran):\n"
                "  • Small oval-framed TEMPLE LOGO on the far "
                "left (the provided logo file — use it exactly, "
                "untouched). This is usually the primary deity's "
                "portrait (Sai Baba, Krishna, Ganesha, etc.) in "
                "an oval / rounded frame.\n"
                "  • To the right of the logo: bold sans-serif "
                "TEMPLE NAME in blue (~#0A5CA8), one line "
                "(e.g. 'Sri Shirdi Sai Baba Temple of DFW'). "
                "This text comes from the Business DNA.\n"
                "  • Below the name: small ADDRESS LINE in dark "
                "grey / near-black (e.g. '2699 W. Plano Pkwy, "
                "Plano, Texas 75075'). From Business DNA / "
                "brief.\n"
                "  • Below the address: an italic devotional "
                "TAGLINE in dark red / maroon (e.g. 'Satguru "
                "Sainath Maharaj Ki Jai'). From Business DNA "
                "or the brief. If none provided, omit this line "
                "— never invent.\n\n"
                "(3) EVENT HEADING SECTION:\n"
                "  • Devanagari script accents on either side of "
                "the heading — usually श्रद्धा (Shraddha) on the "
                "left and सबूरी (Saburi) on the right, in maroon / "
                "red serif Devanagari font. These are Sai Baba's "
                "core teachings ('Faith' and 'Patience'). For non-"
                "Sai-Baba temples, use the brief's provided "
                "Devanagari accents or omit.\n"
                "  • Bold red / crimson SERIF EVENT HEADING "
                "(e.g. 'Guru Purnima Celebrations', 'Sri Hanuman "
                "Jayanthi Celebrations', 'Sankatahara Chaturthi "
                "Puja'). Centered, weight 700–800, ~40–56pt.\n\n"
                "(4) CENTERED DEITY PHOTOGRAPH:\n"
                "  • The relevant DEITY IMAGE for this festival "
                "(from the provided reference image if supplied — "
                "use it EXACTLY, do not restyle). Sized modestly "
                "(~30–40% of canvas width), centered horizontally, "
                "with a subtle white or gold border.\n"
                "  • If no reference image provided, render a "
                "tasteful traditional deity illustration that "
                "matches the festival (Hanuman for Hanuman "
                "Jayanthi, Ganesha for Sankatahara Chaturthi, "
                "Sai Baba for Guru Purnima, Lakshmi for Diwali, "
                "Durga for Navaratri, etc.).\n\n"
                "(5) DESCRIPTIVE PARAGRAPH:\n"
                "  • A short paragraph (2–5 lines, ~40–80 words) "
                "explaining the festival's significance. TAKE "
                "THIS VERBATIM from the campaign brief. If the "
                "brief doesn't provide one, omit this section — "
                "do NOT invent religious explanations.\n"
                "  • Font: clean sans or light serif in dark grey / "
                "near-black, centered, comfortable line-height.\n\n"
                "(6) SCHEDULE TABLE:\n"
                "  • DATE HEADER: bold red / crimson serif, format "
                "like 'Tuesday, 29 July 2026 (Main Day)'. Pulled "
                "VERBATIM from the brief.\n"
                "  • SCHEDULE ROWS: each row is `time (left, "
                "aligned) | event description (right, aligned)`. "
                "Examples: '7:15 am — Kakad Aarti (Daily event)', "
                "'12:00 pm — Madhyana Aarti (Daily event)', "
                "'7:00 pm — Shri Sathyanarayana Vratha Katha & "
                "Puja'.\n"
                "  • SPECIAL / HIGHLIGHT EVENTS: bold red text "
                "for both time and description (e.g. main puja "
                "at a special time).\n"
                "  • ALL times and event descriptions come "
                "VERBATIM from the brief. NEVER invent a schedule. "
                "NEVER collapse or summarise rows — if the brief "
                "provides 11 time slots (like a Main Day with "
                "Kakad Aarti / Ganesha Puja / Guru Puja / Homam / "
                "Baba Maha Abhishekam / Madhyana Aarti / "
                "Stavanamanjari / Satcharitra Parayanam / Hanuman "
                "Chalisa / Dhoop Aarti / Baba Palki Seva / Special "
                "Archana / Shej Aarti), render ALL 11 rows in a "
                "dense schedule table. Do NOT compress into "
                "'morning rituals, Maha Abhishekam, Palki Seva, "
                "and special bhajans' — that is a forbidden "
                "collapse. Render each time slot on its own row.\n"
                "  • If multiple day headers exist (multi-day "
                "festivals), stack them with clear visual "
                "separation.\n"
                "  • Small footnote text below the schedule "
                "(e.g. '*Refer to Sathyanarayana Puja flyer for "
                "more details') ONLY if the brief includes such "
                "footnotes.\n\n"
                "(7) CONTACT FOOTER (bottom of the flyer):\n"
                "  • Two-column footer strip in the flyer's "
                "bottom margin.\n"
                "  • LEFT column: 'Phone: [number]' and 'Email: "
                "[email]' — pulled VERBATIM from the brief / "
                "Business DNA / auto-extracted CONTACT INFO block. "
                "If either is missing, omit that specific line — "
                "never invent.\n"
                "  • RIGHT column: 'Website: [URL]' and "
                "'Facebook: [URL or handle]' or 'Instagram: "
                "[handle]' — same rule.\n"
                "  • Font: small clean sans, dark red / maroon "
                "for labels, black for values.\n"
            ),
            "palette": (
                "Warm devotional palette:\n"
                "  • Background: soft warm SAFFRON / GOLD gradient "
                "(#FFD98C at top → #FFC060 at middle → #FFA640 at "
                "bottom, or similar warm yellow-orange range). "
                "Never a flat block — always a soft gradient.\n"
                "  • Event heading text: deep RED / CRIMSON "
                "(~#D32F2F, #C41E1E).\n"
                "  • Temple name: strong BLUE (~#0A5CA8).\n"
                "  • Address + body text: near-black / dark grey "
                "(#1A1A1A / #333).\n"
                "  • Tagline: dark maroon / burgundy (~#8B0000).\n"
                "  • Toran leaves: fresh GREEN (~#3AA34F, "
                "#2E8B3F).\n"
                "  • Marigold flowers: vibrant ORANGE "
                "(~#FF7F00, #E85D0C).\n"
                "  • Diyas: brass / gold with warm flame yellow-"
                "orange glow.\n"
                "  • Palm tree silhouettes: dark warm green / "
                "olive.\n"
                "  • Brand accent color <BRAND_COLOR> may appear "
                "subtly in the schedule highlight rows or the "
                "tagline colour, but the overall palette stays "
                "traditionally saffron-gold-red-green regardless "
                "of brand DNA."
            ),
            "typography": (
                "Bold SERIF (Playfair Display, Merriweather, "
                "Georgia) for the event heading — weight 700–800, "
                "red / crimson. Strong sans-serif (Inter Bold, "
                "Montserrat Bold) for the temple name in blue. "
                "Italic script or refined italic serif for the "
                "devotional tagline. Clean readable sans-serif "
                "(Inter Regular, Open Sans) for the schedule "
                "table, body paragraph, and contact footer. "
                "Devanagari text (श्रद्धा / सबूरी) in a "
                "traditional Devanagari serif (Noto Serif "
                "Devanagari, Sanskrit Text) in maroon."
            ),
            "mood": (
                "Devotional, celebratory, community-oriented, "
                "culturally authentic. Feels like a printed "
                "temple event flyer you'd receive at a community "
                "gathering — warm, welcoming, respectful of "
                "tradition. NOT flashy, NOT modern-corporate, "
                "NOT minimalist."
            ),
            "elements_to_include": (
                "Warm saffron-gold gradient background. "
                "Decorative frame: green mango-leaf toran + "
                "orange marigold string across the top; hanging "
                "brass diyas on left and right; palm-tree "
                "silhouettes on left and right edges. Top-center "
                "temple header: oval-framed temple logo + bold "
                "blue temple name + address + italic devotional "
                "tagline. Devanagari accents (श्रद्धा / सबूरी or "
                "brief-provided) on either side of the heading. "
                "Bold red serif EVENT HEADING. Centered DEITY "
                "PHOTOGRAPH from the reference image. Short "
                "DESCRIPTIVE PARAGRAPH from the brief. SCHEDULE "
                "TABLE with date header + time / event rows "
                "verbatim from the brief. CONTACT FOOTER with "
                "phone / email / website / social taken verbatim "
                "from the brief / Business DNA."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of "
                "these:\n"
                "  • HALLUCINATED SCHEDULE TIMES OR EVENT NAMES — "
                "every time and event description must come "
                "verbatim from the campaign brief. If the brief "
                "has 6 events, render 6. Never invent 'Aarti at "
                "5:30 pm' if the brief doesn't list it.\n"
                "  • HALLUCINATED PHONE NUMBERS, EMAILS, "
                "WEBSITES, ADDRESSES — never fabricate contact "
                "info. Only render values pulled verbatim from "
                "the campaign brief / Business DNA / auto-"
                "extracted CONTACT INFO block. Omit missing "
                "lines rather than invent.\n"
                "  • HALLUCINATED TEMPLE NAMES OR TAGLINES — the "
                "temple name and tagline come from the Business "
                "DNA. Never invent 'Sri X Temple'.\n"
                "  • INVENTED FESTIVAL DESCRIPTIONS — the "
                "descriptive paragraph must come from the brief. "
                "Do NOT invent religious explanations.\n"
                "  • The WRONG DEITY for the festival — Hanuman "
                "for Hanuman Jayanthi, Ganesha for Chaturthi, "
                "etc. Match the deity to the festival named in "
                "the brief.\n"
                "  • Corporate / minimalist / cyberpunk / neon / "
                "modern SaaS aesthetics — this is a traditional "
                "temple flyer, not a tech poster.\n"
                "  • CTA BUTTONS OF ANY KIND — 'Plan Your Visit', "
                "'Explore festival details', 'Register Now', 'Book "
                "Seats', 'Learn More', 'Read More' or any pill / "
                "rounded rectangle button that looks clickable. "
                "This is a traditional temple flyer, NOT a landing "
                "page. The contact footer strip (phone / email / "
                "website / Facebook) is the only 'action zone' — "
                "no separate button. This is the #1 forbidden "
                "mistake for this style.\n"
                "  • COLLAPSING / SUMMARISING the schedule — if the "
                "brief provides 11 time slots, render 11 rows. "
                "NEVER compress into 'morning rituals, [X], [Y], "
                "and special bhajans'. Each time-slot row stays on "
                "its own line with its time on the left.\n"
                "  • Sparse minimalist layout — this style is "
                "information-dense by design.\n"
                "  • English-only when the brief provides "
                "Devanagari accents.\n"
            ),
        },
    },

    "deity_devotional_poster": {
        "label": "Deity Devotional Poster",
        "group": "religious",
        "emoji": "🕉️",
        "when_to_use": (
            "Simple devotional posts — daily blessings, festival "
            "greetings, deity announcements, thought-of-the-day "
            "religious posts. Traditional Indian devotional poster "
            "aesthetic featuring the deity as the hero with ornate "
            "ceremonial framing, rich colours, and a short "
            "blessing / greeting message. Best for posts that need "
            "the visual weight of a devotional image WITHOUT a "
            "detailed schedule."
        ),
        "prompt_hint": (
            "traditional Indian devotional deity poster — rich "
            "ornate ceremonial framing with temple-arch or gopuram "
            "silhouette, hero deity image centered (from provided "
            "reference), warm gold + red + royal blue devotional "
            "palette, decorative gold filigree borders, small "
            "brass diya + lotus + marigold motifs, single short "
            "devotional headline in Sanskrit / English / Devanagari "
            "(taken from the brief), temple logo top-corner, "
            "greeting or blessing line pulled from the brief; no "
            "CTA, no schedule, no dense body copy"
        ),
        "visual_dna": {
            "composition": (
                "GROUNDING RULE — read BEFORE designing: EVERY "
                "deity name, blessing text, festival greeting, "
                "Sanskrit / Devanagari phrase, and any contact "
                "info on this poster must come VERBATIM from the "
                "campaign brief or the Business DNA. NEVER invent "
                "Sanskrit slokas, blessings, or greetings. Only "
                "render what the brief provides.\n\n"
                "TRADITIONAL INDIAN DEVOTIONAL DEITY POSTER. "
                "Reference: 'Kuber Lakshmi' devotional print + "
                "traditional Hindu deity posters that hang in "
                "homes and temples.\n\n"
                "OVERALL FRAMING:\n"
                "  • Ornate CEREMONIAL FRAME around the entire "
                "canvas — gold filigree border with decorative "
                "corner motifs (lotus, kalasha, small deities, "
                "peacocks). Rich detailed border like a "
                "traditional South-Indian temple painting.\n"
                "  • Background inside the frame: rich devotional "
                "gradient — deep royal blue / burgundy at the "
                "corners fading to warm gold in the center where "
                "the deity sits. Alternatively, a soft cloud-and-"
                "sky background behind the deity.\n"
                "  • Optional: a temple-arch or GOPURAM silhouette "
                "behind the deity as an architectural frame.\n\n"
                "CENTRAL DEITY:\n"
                "  • The HERO DEITY image from the provided "
                "reference — use it EXACTLY, do not restyle "
                "(traditional deity iconography is sacred; treat "
                "the reference as canonical).\n"
                "  • Deity is centered, sized generously (60–75% "
                "of canvas width / height).\n"
                "  • Optional: divine halo / aura behind the "
                "deity's head in soft gold rays.\n"
                "  • Optional secondary / companion deities as "
                "smaller figures beside the main deity IF the "
                "brief specifies (e.g. Lakshmi beside Kuber, "
                "Radha beside Krishna, Sita beside Rama).\n\n"
                "DECORATIVE ELEMENTS AROUND THE DEITY:\n"
                "  • Small brass DIYAS (oil lamps) at the base "
                "with visible flame.\n"
                "  • LOTUS flowers (pink or white) in the "
                "foreground / base.\n"
                "  • Optional MARIGOLD garland strung above the "
                "deity.\n"
                "  • Traditional offerings (small pots of grain, "
                "coins, sweets) at the base only if the brief "
                "references them (e.g. Kuber posters often show "
                "gold coin pots).\n\n"
                "TEXT ELEMENTS (all verbatim from the brief):\n"
                "  • Temple logo TOP-LEFT (untouched, small "
                "~50px) OR temple name in a small ribbon banner "
                "at the top.\n"
                "  • Deity NAME on a decorative ribbon banner at "
                "the top OR bottom (e.g. 'KUBER LAKSHMI', "
                "'GANESHA', 'HANUMAN'). Bold serif or "
                "traditional Devanagari-influenced English "
                "typography, gold / red / white text on "
                "contrasting banner.\n"
                "  • Optional short DEVOTIONAL GREETING or "
                "BLESSING (1 line, Sanskrit / Devanagari / "
                "English) below the deity name — pulled "
                "VERBATIM from the brief. Examples: 'Om Gam "
                "Ganapataye Namah', 'Jai Sri Ram', 'Happy "
                "Diwali'. If no greeting provided, omit.\n"
                "  • Optional occasion line ('Guru Purnima "
                "Blessings', 'Diwali Greetings') if the brief "
                "provides one.\n"
                "  • NO schedule, NO time table, NO descriptive "
                "paragraph, NO CTA button.\n\n"
                "OPTIONAL CONTACT LINE (bottom margin):\n"
                "  • ONE small line at the very bottom with the "
                "temple name + website + phone, in small clean "
                "typography, taken VERBATIM from the brief / "
                "Business DNA. Omit if any specific value is "
                "missing.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Portrait 4:5 / 9:16 → primary format "
                "(matches traditional devotional posters).\n"
                "  • Square 1:1 → tighter deity crop, same "
                "framing.\n"
                "  • Landscape 16:9 → deity center, decorative "
                "companions on left and right for balance.\n"
            ),
            "palette": (
                "Traditional devotional palette:\n"
                "  • Frame border: rich GOLD (~#D4AF37, "
                "#B8860B) with decorative red / burgundy "
                "inlays.\n"
                "  • Background: deep ROYAL BLUE (~#1A3A6F) or "
                "MAROON (~#7B1F1F) at the corners, fading to "
                "warm GOLD (~#F0C36D) at the center.\n"
                "  • Halo / aura: soft warm gold with rays.\n"
                "  • Deity ribbon banner background: deep red "
                "(~#8B0000) or gold with white / gold text.\n"
                "  • Devotional greeting text: white or gold "
                "on the banner, or deep red on the "
                "background.\n"
                "  • Lotus + marigold accents: pink and orange "
                "in soft focus.\n"
                "  • Diyas: warm brass gold with flame yellow-"
                "orange.\n"
                "  • Brand accent <BRAND_COLOR> may subtly "
                "appear in the ribbon banner or contact line, "
                "but the overall palette stays traditional "
                "devotional regardless of brand DNA."
            ),
            "typography": (
                "Bold traditional SERIF or Devanagari-influenced "
                "English display font for the deity name / "
                "banner (Cinzel, Trajan, Playfair Display Bold). "
                "Devanagari serif (Noto Serif Devanagari, "
                "Sanskrit Text) for any Sanskrit / Hindi "
                "greeting. Clean small sans-serif for the "
                "optional bottom contact line."
            ),
            "mood": (
                "Devotional, reverent, festive, ornate, "
                "traditional. Feels like a devotional print "
                "poster hanging in a Hindu home or temple — "
                "rich, colourful, ceremonial. NOT minimalist, "
                "NOT modern-corporate, NOT sparse."
            ),
            "elements_to_include": (
                "Ornate gold ceremonial FRAME with decorative "
                "corner motifs. Rich devotional background "
                "(deep royal blue or maroon fading to warm "
                "gold). Central HERO DEITY from the reference "
                "image (untouched). Optional divine halo. "
                "Optional temple-arch / gopuram silhouette. "
                "Decorative diyas + lotus + marigold accents. "
                "Deity NAME on a decorative ribbon banner. "
                "Optional short devotional GREETING / BLESSING "
                "from the brief (Sanskrit / Devanagari / "
                "English). Optional occasion line. Small "
                "temple logo top-corner. Optional small "
                "contact line at the very bottom (temple + "
                "website + phone) from the brief."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of "
                "these:\n"
                "  • HALLUCINATED SANSKRIT SLOKAS, MANTRAS, OR "
                "BLESSINGS — never invent religious text. Only "
                "render what the brief provides verbatim.\n"
                "  • HALLUCINATED DEITY NAMES OR FESTIVAL "
                "GREETINGS — pull from the brief only.\n"
                "  • HALLUCINATED CONTACT INFO (phone, email, "
                "website) — verbatim from the brief only.\n"
                "  • The WRONG DEITY for the occasion — match "
                "the deity to what the brief specifies.\n"
                "  • Schedule tables, event times, day-by-day "
                "programs — those go in the temple_event_flyer "
                "style, not here. This style is a devotional "
                "poster, NOT an event flyer.\n"
                "  • Descriptive paragraphs, dense body copy, "
                "long explainer text.\n"
                "  • CTA BUTTONS of any kind.\n"
                "  • Modern minimalist / corporate / cyberpunk "
                "aesthetics — this is a traditional devotional "
                "poster.\n"
                "  • Cartoon / anime / stylized deity "
                "reinterpretations — respect the reference "
                "iconography.\n"
                "  • Frames that clip or crop the deity — the "
                "deity is always fully visible, respectfully "
                "framed.\n"
            ),
        },
    },

    "adaptive_context": {
        "label": "Adaptive (Context-Driven)",
        "group": "travel_immigration",
        "emoji": "🧭",
        "when_to_use": "Let the Art Director READ the brief, business DNA and reference documents, then choose composition, palette, typography and mood that best fit the campaign — no locked template",
        "prompt_hint": (
            "context-driven design decision — do NOT default to a preset "
            "template. Read the campaign brief, business DNA, category, brand "
            "colour, and any reference documents, then reason about which "
            "composition, colour palette, typography style and mood best serve "
            "the campaign's audience and intent, and design accordingly"
        ),
        "visual_dna": {
            "composition": "REASON FIRST, THEN DESIGN. Before writing the image_prompt, silently ask: What is this campaign about (announcement, offer, thought leadership, event, product launch, service pitch, hiring, testimonial)? Who is the audience (B2B decision-maker, D2C consumer, student, patient, developer)? What does the business DNA / category imply (physical product → product-hero; service/SaaS → clean editorial or UI mockup; promotional offer → flyer with prominent pricing and CTA; event → poster with date and location prominent; thought leadership → magazine-style hero with negative space; hiring → team photograph with role callouts; healthcare → calm clinical layout with soft imagery). Choose the composition that best delivers the message — do NOT default to a generic marketing hero.",
            "palette": "DERIVE the palette from context, do not invent randomly. Start from the brand's primary colour <BRAND_COLOR> as the accent. Choose supporting colours from the industry norms: finance / legal / consultancy → navy + grey + white with a red or gold accent; wellness / spa / organic → soft greens, creams, muted terracotta; deeptech / AI / SaaS → deep purple or midnight blue + cyan; education / edtech → navy + red + white, OR purple + yellow, OR white + brand accent; food / hospitality → warm oranges, browns, cream; luxury / jewellery / fashion → black + gold + off-white; healthcare → calm blue + white + soft green. Justify the palette choice in one line to yourself before emitting the prompt.",
            "typography": "MATCH TO AUDIENCE. B2B / enterprise / consultancy → clean modern sans-serif with confident hierarchy and data-forward layout. Consumer / lifestyle / D2C → bold display sans with optional handwritten brush accent for one keyword. Luxury / fashion / premium → elegant modern serif with generous kerning. Technical / developer / SaaS → geometric sans plus optional monospace secondary. Editorial / thought leadership → magazine-style contrast between a very large headline and small tasteful caption. Choose ONE typographic system and commit to it throughout the image.",
            "mood": "READ THE BRAND VOICE from the campaign brief and business DNA. Match the mood accordingly: promotional / urgent (bold, high-contrast, unmissable), authoritative / editorial (calm, spacious, magazine-like), aspirational / lifestyle (warm daylight, editorial photography), technical / factual (clean grid, data-forward, restrained), luxurious / premium (moody lighting, cinematic depth, rich materials). Do not default to generic 'tech glow' or 'gaming poster' unless the brief specifically calls for it.",
            "elements_to_include": "Only the elements the campaign actually needs — no decorative filler. If the brief mentions a hero stat / price / date, render it in a prominent callout. If the brief lists features, render them as icon-and-label cards. If the brief mentions contact info (phones / emails / URLs), render it in a bottom contact strip verbatim. If the brief mentions a real person or team, render a real photograph. If the brief is purely conceptual, use a strong metaphorical image or clean typographic composition. Brand logo top-left is always included. CTA is included when the brief has a clear call-to-action.",
            "elements_to_avoid": "Do NOT default to cyberpunk neon, do NOT default to holographic tech, do NOT invent decorative 3D mascots, do NOT force a specific template that does not fit the campaign. Do NOT copy the visual DNA of any other preset style — reason your own visual decisions from context. Avoid generic stock-photo compositions unless the brief specifically calls for editorial photography.",
        },
    },
    # ── 🛍️ Product-Based (category-specific) ──────────────────────
    "beauty_cosmetics": {
        "label": "Beauty & Cosmetics",
        "group": "physical_product",
        "emoji": "💄",
        "when_to_use": "Skincare, makeup, fragrance, personal-care launches — luxury unboxing hero",
        "prompt_hint": (
            "luxe cosmetic hero on glass or marble surface with soft ring-light "
            "glow, single product bottle or tube as centrepiece, minimal styling "
            "props (petal, cream swatch, glass shard), pastel or blush palette"
        ),
        "visual_dna": {
            "composition": "Single hero product (bottle, jar, tube, palette) placed centre or slightly off-centre on a reflective glass or matte marble surface. One or two minimal styling props (a fresh petal, a soft cream swatch, a single ingredient callout). Camera at 45° for depth. Ample negative space around the hero.",
            "palette": "Blush, cream, champagne, soft pastel, or muted powder tones. Brand colour appears as one small accent on the label or CTA — never on the background. High-contrast product-on-surface.",
            "typography": "Elegant modern serif OR clean light sans-serif with generous kerning. Heading is short and refined. NEVER shouty sans.",
            "mood": "Luxurious, editorial, calm, aspirational. Soft ring-light glow. Subtle shadow beneath product. Feels like a premium magazine advertorial.",
            "elements_to_include": "One hero product, subtle styling prop, ONE heading (product name or benefit), optional one-line subheading, up to 3 icon-label chips (ingredient / benefit / claim), brand logo top-left, small refined CTA.",
            "elements_to_avoid": "Cluttered flat lays with 10+ props, neon colours, cyberpunk, 3D cartoon mascots, dark moody photography, dense body copy paragraphs, before/after collages.",
        },
    },
    "fashion_lookbook": {
        "label": "Fashion Look-Book",
        "group": "physical_product",
        "emoji": "👗",
        "when_to_use": "Apparel, jewellery, accessories, footwear — seasonal drops, capsule collections, editorial fashion posts",
        "prompt_hint": (
            "editorial fashion look-book portrait, one model wearing the product "
            "with confident poise, clean studio backdrop or moody urban location, "
            "magazine-cover typography, controlled colour palette"
        ),
        "visual_dna": {
            "composition": "One model wearing / holding the product in a confident poise. Studio backdrop (paper roll, coloured sweep) OR a controlled urban location (concrete wall, glass architecture). Portrait framing. The product is the hero — clearly visible.",
            "palette": "Two or three controlled tones only. Muted neutrals or a single bold statement colour that complements the product. Brand colour as accent on the CTA or a single graphic element.",
            "typography": "Magazine-cover style — one big display sans or elegant serif for the heading, optional small sans-serif caption underneath. Vogue / GQ feel.",
            "mood": "Editorial, aspirational, confident. Natural window light OR soft studio strobes. Clean shadows, minimal retouching feel.",
            "elements_to_include": "One model wearing the product, ONE bold heading (collection name or seasonal tagline), optional one-line subheading, up to 3 small chips (fabric / drop date / price), brand logo top-left, small refined CTA.",
            "elements_to_avoid": "Group photos with multiple models, busy graphic overlays, dense feature lists, cartoon or watercolour, product-on-white catalog shots (use studio_product_shot for that), cyberpunk or neon.",
        },
    },
    "luxury_jewelry_editorial": {
        "label": "Luxury Jewelry Editorial",
        "group": "physical_product",
        "emoji": "💎",
        "when_to_use": (
            "Fine jewelry, watches, luxury accessories, high-end "
            "fashion brands — cinematic editorial campaign visuals "
            "with the product worn by a real model OR staged on a "
            "natural surface (leaf, marble, silk), with a poetic "
            "elegant serif headline overlay. Minimal text, extreme "
            "restraint, luxury magazine feel."
        ),
        "prompt_hint": (
            "editorial luxury jewelry campaign photograph — either "
            "(a) a real model wearing the product in a soft neutral "
            "cream / beige setting with cinematic lighting, or (b) "
            "the product staged on a real natural surface (leaf, "
            "marble, silk, moss) in a moody dark scene; elegant "
            "SERIF headline overlaid in white, short and poetic; no "
            "CTA, no bullets, no feature chips, no dense text"
        ),
        "visual_dna": {
            "composition": (
                "LUXURY JEWELRY EDITORIAL. Two sub-templates share "
                "the same restraint — the Art Director picks based "
                "on the brief:\n\n"
                "═══════════════════════════════════════════\n"
                "TEMPLATE PICKER — READ THIS FIRST BEFORE CHOOSING\n"
                "═══════════════════════════════════════════\n"
                "  • IF the request includes REFERENCE IMAGE(S) that "
                "show a MODEL / PERSON (even one) → ALWAYS use "
                "TEMPLATE A (Model Editorial). The reference "
                "model IS the campaign hero — use their face, "
                "wardrobe, and pose faithfully. NEVER fall back to "
                "Template B when a person reference is present.\n"
                "  • IF the pipeline's user prompt says "
                "variant_type='follower_growth' OR "
                "'high_interaction' → USE TEMPLATE A. In this "
                "style, follower_growth = model editorial (NOT "
                "product-on-nature). Ignore any default-prompt "
                "convention that maps 'follower_growth' to a "
                "different template shape — for THIS style, "
                "follower_growth ALWAYS means model editorial.\n"
                "  • IF the campaign brief mentions 'wearing', "
                "'worn', 'model', 'on-body', 'campaign photo', "
                "'lookbook', 'launch', 'new collection', 'she', "
                "'her', 'his', 'him' → USE TEMPLATE A.\n"
                "  • Use TEMPLATE B (Product-on-Nature) ONLY when "
                "ALL of these are true: (1) no person reference "
                "image is provided, (2) the brief has NO mention "
                "of a model / wearer / on-body context, AND (3) "
                "the brief is EXPLICITLY about craft / material / "
                "origin / heritage (e.g. 'inspired by the forest', "
                "'crafted from raw stone', 'handmade in [region]').\n"
                "  • When in doubt → USE TEMPLATE A. Model "
                "editorial is the DEFAULT for this style.\n\n"
                "═══════════════════════════════════════════\n"
                "TEMPLATE A — MODEL EDITORIAL (DEFAULT — Ref: gold-"
                "layered-necklace portrait, 'Stuck in a love "
                "triangle?' bracelet stack)\n"
                "═══════════════════════════════════════════\n"
                "Use as the DEFAULT — see picker above.\n\n"
                "  • FULL-CANVAS real editorial PHOTOGRAPH of a real "
                "model wearing the jewelry as the hero. Frame the "
                "shot to feature the product prominently — a close "
                "portrait crop for necklaces / earrings, a "
                "close-crop of the wrist / hand for bracelets / "
                "rings.\n"
                "  • IF a REFERENCE MODEL IMAGE is provided in the "
                "request, use that exact model — same face, same "
                "hair, same skin tone, same expression, same pose "
                "family. Do NOT swap them for a stock model. Adapt "
                "wardrobe and lighting only.\n"
                "  • IF a REFERENCE PRODUCT IMAGE is provided, "
                "render the actual jewelry from that reference on "
                "the model — same shape, same colour metal, same "
                "gem colour, same design details. Do NOT invent new "
                "pieces.\n"
                "  • Model wears understated clothing (black "
                "strapless top, black v-neck, plain neutral top) so "
                "the jewelry stands out. No competing patterns, no "
                "logos on clothing.\n"
                "  • Background: soft neutral cream / beige / ivory "
                "with a hint of natural window light. Slightly "
                "out-of-focus wall, drape, or interior detail. "
                "Editorial magazine feel, not studio-clinical.\n"
                "  • Lighting: soft natural daylight or warm ring-"
                "light, subtle shadows, high skin quality, jewelry "
                "catches highlights.\n"
                "  • ELEGANT SERIF HEADLINE overlaid on the "
                "photograph in white — usually center-positioned or "
                "positioned in a negative-space area (top-half or "
                "beside the model's neck/wrist). Poetic and playful "
                "('Stuck in a love triangle?', 'She is her own "
                "muse', 'For the everyday extraordinary'). 3–7 "
                "words, may wrap 2 lines. Weight 400–500 (light "
                "elegant serif), never bold display.\n\n"
                "═══════════════════════════════════════════\n"
                "TEMPLATE B — PRODUCT-ON-NATURE HERO (Ref: pearl "
                "earrings on a leaf, 'Collected from the forest / "
                "Translated into precious craft')\n"
                "═══════════════════════════════════════════\n"
                "Use ONLY when the picker rules above allow it "
                "(no person reference AND explicitly craft/origin "
                "brief).\n\n"
                "  • IF a REFERENCE PRODUCT IMAGE is provided, "
                "render the actual jewelry from that reference on "
                "the natural surface — same shape, same colour "
                "metal, same gem colour, same design details.\n\n"
                "  • FULL-CANVAS real product photograph. The "
                "jewelry is staged on a REAL natural surface that "
                "ties to the brief's inspiration: a fresh green "
                "leaf, dark moss, weathered wood, marble slab, silk "
                "fabric, wet stone, dried petals, coral. The "
                "surface is CHOSEN to reflect the campaign's origin "
                "story.\n"
                "  • Product placement: 1–3 pieces of the jewelry "
                "arranged on the surface with cinematic staging. "
                "Small sparkle / catch-light on the diamonds or "
                "polished metal.\n"
                "  • Background is MOODY — deep dark green, "
                "charcoal, black, or deep midnight blue. The natural "
                "surface hero-lit against a dark background.\n"
                "  • Lighting: cinematic key light on the product, "
                "deep shadows around, subtle rim light on the "
                "jewelry edges. Feels like a Van Cleef & Arpels or "
                "Cartier campaign still.\n"
                "  • ELEGANT SERIF HEADLINE overlaid in white, "
                "positioned in the negative space (typically top-"
                "center or top-third). Poetic 2-line format is "
                "common ('Collected from the forest / Translated "
                "into precious craft'). 6–14 words total across 2 "
                "lines. Light-weight serif (400–500), never bold "
                "display.\n\n"
                "═══════════════════════════════════════════\n"
                "SHARED RULES (both templates):\n"
                "═══════════════════════════════════════════\n"
                "  • NO CTA BUTTON. NO chip highlights. NO icon-"
                "labeled features. NO price tags. NO 'shop now' "
                "labels. NO body paragraphs.\n"
                "  • NO brand logo overlay (this style is about the "
                "campaign message, not identity branding — the "
                "brand comes through the product itself and the "
                "post caption). If a logo IS required, place it "
                "tiny at the bottom-center (~24px) in white / "
                "brand-neutral.\n"
                "  • Optional: 3-dot carousel indicator at bottom-"
                "center (small white dots) if this image is part of "
                "a carousel set.\n"
                "  • Optional: tiny navigation arrow icon on the "
                "right edge if part of a carousel.\n"
                "  • Every visual choice is driven by the campaign "
                "brief — the model wardrobe, the natural surface, "
                "the headline copy, the mood. No generic filler.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Portrait 4:5 / 9:16 → primary format (matches "
                "the reference set). Model close-crop or product-"
                "on-nature hero fills the full canvas.\n"
                "  • Square 1:1 → same composition, tighter crop.\n"
                "  • Landscape 16:9 / 1.91:1 → Template B works "
                "well (product-on-nature spans horizontally); "
                "Template A can flip to a wide model portrait with "
                "text on one side.\n"
            ),
            "palette": (
                "LUXURY EDITORIAL — the palette derives from the "
                "chosen template, not the brand colour:\n"
                "  • Template A background: soft cream / beige / "
                "ivory (~#F5EEE3, #EDE4D8). Warm natural skin tones "
                "in the model. Metallic tones from the jewelry "
                "itself (gold, silver, rose gold, pearl white).\n"
                "  • Template B background: deep moody tones — "
                "forest green (~#1A2E1F), charcoal (~#1C1C1C), "
                "midnight blue (~#0E1A2E), or wet stone grey. "
                "Natural surface (leaf green, marble white, silk "
                "champagne) as the hero platform. Jewelry catches "
                "gold / silver / gemstone colour highlights.\n"
                "  • Overlaid headline text = pure white (#FFFFFF) "
                "in both templates, elegant serif at 400–500 weight.\n"
                "  • Brand accent colour <BRAND_COLOR> only appears "
                "if the jewelry itself carries it (a gemstone that "
                "matches the brand palette) — never as a background "
                "wash, never as a text colour.\n"
                "  • Carousel dots (if used) = white at low opacity."
            ),
            "typography": (
                "Elegant modern SERIF for the overlaid headline — "
                "Didot, Playfair Display, Cormorant Garamond, Lora, "
                "or similar refined serif. Weight 400–500 (never "
                "bold). Generous letter-spacing. Never sans-serif "
                "for this style — the serif carries the luxury "
                "signal. Optional italic for one emphasised word."
            ),
            "mood": (
                "Editorial, aspirational, cinematic, luxurious. "
                "Feels like a Vogue / Harper's Bazaar spread, a Van "
                "Cleef campaign still, or a Cartier holiday poster. "
                "Restrained and confident. The photograph and the "
                "single poetic headline carry the entire message — "
                "everything else is silent."
            ),
            "elements_to_include": (
                "ONE full-canvas real editorial PHOTOGRAPH — "
                "Template A: real model wearing the jewelry in a "
                "soft neutral setting with cinematic natural light; "
                "Template B: jewelry hero-staged on a real natural "
                "surface (leaf, marble, silk, moss) against a moody "
                "dark background. ONE elegant SERIF HEADLINE "
                "overlaid in white, poetic, 3–14 words. Optional "
                "3-dot carousel indicator + small navigation arrow "
                "if part of a carousel set."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS of any kind ('Shop Now', 'Buy', "
                "'Learn More', 'Discover', 'Explore') — this is "
                "campaign / editorial content, not conversion.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS "
                "('Best Seller', 'New Arrival', price chips, "
                "'Handmade' badges, etc.). NEVER emit chips even if "
                "a text-density rule elsewhere suggests them.\n"
                "  • ICON-LABELED FEATURES, BULLET LISTS, ICON "
                "ROWS, feature grids — the poetic headline is the "
                "ONLY text.\n"
                "  • BODY PARAGRAPHS, product descriptions, "
                "material specs, price tags.\n"
                "  • LARGE BRAND LOGO OVERLAY — logo is omitted, or "
                "tiny bottom-center (~24px) at most.\n"
                "  • Sans-serif overlaid headline — this style uses "
                "elegant serif ONLY.\n"
                "  • Bold heavy display weights — the serif is "
                "always light (400–500).\n"
                "  • Multiple models in the frame — one model only.\n"
                "  • Cluttered flat lay with 10+ props — max 3 "
                "product pieces + 1 natural surface for Template B.\n"
                "  • Bright saturated backgrounds, cyberpunk neon, "
                "cartoon or watercolour styling — this is real "
                "editorial photography only.\n"
                "  • Studio-white catalog backgrounds (use "
                "studio_product_shot for that instead).\n"
                "  • Defaulting to TEMPLATE B (product-on-nature) "
                "when a MODEL REFERENCE IMAGE has been provided in "
                "the request — this is the #1 forbidden mistake. "
                "When a person reference is present, ALWAYS render "
                "them on the canvas using TEMPLATE A. Template B "
                "is a fallback for craft-only briefs with no "
                "model reference.\n"
            ),
        },
    },
    "food_beverage_editorial": {
        "label": "Food & Beverage Editorial",
        "group": "physical_product",
        "emoji": "🍽️",
        "when_to_use": "Restaurants, packaged food/drink brands, cafés, meal-kits, hospitality — appetite-driven hero",
        "prompt_hint": (
            "appetite-driven overhead or 45° hero shot of the food or beverage, "
            "styled ingredients around the hero, warm natural daylight, wooden "
            "or linen surface, editorial food-magazine composition"
        ),
        "visual_dna": {
            "composition": "Overhead flat lay OR 45° hero of the food / drink as the centrepiece. Styled ingredients (herbs, spices, garnish, spilled seeds, linen napkin) around the hero but never crowding it. Wooden board, marble, or linen surface. Composition has clear depth and rhythm.",
            "palette": "Warm natural tones — cream, wood brown, terracotta, soft green herbs. Brand colour as one accent on packaging label or CTA. Never neon or pastel.",
            "typography": "Warm modern serif or a friendly rounded sans. Heading references the dish or seasonal message. Small handwritten script accent is optional (once per image).",
            "mood": "Appetising, warm, homely-but-refined. Warm natural window light with soft directional shadows. Feels like a Bon Appétit spread.",
            "elements_to_include": "One hero food or drink item, 2-4 styling ingredients around it, ONE heading (dish name / offer), optional one-line subheading, up to 3 chips (price / calorie / origin / dietary tag), brand logo top-left, small warm CTA.",
            "elements_to_avoid": "Cartoon food characters, cyberpunk neon plates, 3D rendered CGI food, cluttered ingredient lists as paragraphs, before/after weight-loss framing, harsh flash lighting.",
        },
    },
    "consumer_gadget_spotlight": {
        "label": "Consumer Gadget Spotlight",
        "group": "physical_product",
        "emoji": "🎧",
        "when_to_use": "Electronics, appliances, wearables, audio gear — feature-forward tech hero (Apple-launch feel)",
        "prompt_hint": (
            "premium tech product spotlight, single gadget floating or elevated "
            "on a dark or gradient stage with a subtle rim-light, 2-3 icon-labeled "
            "spec callouts, Apple-keynote hero aesthetic"
        ),
        "visual_dna": {
            "composition": "One gadget as the hero, floating or elevated on a subtle pedestal / gradient stage. Camera at 3/4 view. Optional exploded parts or component callouts around the hero. Up to 3 spec callouts labelled with a thin line pointing to the feature (battery, sensor, chip).",
            "palette": "Dark charcoal / near-black gradient background OR very clean off-white. Brand colour appears as a subtle rim-light or on the CTA. Product retains its true colour with accurate reflections.",
            "typography": "Clean geometric sans-serif — big product name, small technical spec labels beneath. Confident, minimal, Apple / Sonos aesthetic.",
            "mood": "Premium, engineered, keynote-launch energy. Directional rim-light, precise product shadow, sharp focus on every material detail. NOT cinematic-moody, NOT lifestyle.",
            "elements_to_include": "One gadget hero (real product photography style), 2-3 thin-line spec callouts, ONE bold product/heading name, optional one-line subheading, up to 3 chips (price / launch date / key spec), brand logo top-left, refined CTA.",
            "elements_to_avoid": "Human hands holding the product (use lifestyle_on_body for that), cluttered lifestyle scenes, cartoon or illustration, warm homely lighting, food props, dense body copy.",
        },
    },
    "software_product_hero": {
        "label": "Software / App Product Hero",
        "group": "physical_product",
        "emoji": "🖥️",
        "when_to_use": "Consumer or B2B software products — mobile apps, desktop tools, dev platforms (VALUE-focused hero, not just a screenshot)",
        "prompt_hint": (
            "software product hero visual — one clean device (phone or laptop) "
            "showing a partial UI, surrounded by 2-3 abstract visual metaphors "
            "for what the software DOES (not just a screenshot in a device frame)"
        ),
        "visual_dna": {
            "composition": "One clean device (phone or laptop) at a slight angle showing a PARTIAL UI (not the whole app crammed in). Around the device, 2-3 abstract graphic metaphors for what the software actually DOES — a checkmark bubble, a clean chart card, a chat pill, a data-flow line. These metaphors float in a clean composed way, they don't clutter. This is different from `ui_mockup` (which is purely a device frame): here the metaphors CARRY the value story, the device shows just enough UI to feel real.",
            "palette": "Clean off-white or subtle gradient (light lavender, mint, or peach). Brand colour features inside the UI accent state and on ONE floating metaphor card. Never cyberpunk-dark.",
            "typography": "Modern geometric sans-serif. One bold headline about the SOFTWARE OUTCOME (not the feature list). Small tags on each metaphor card. NEVER paragraphs.",
            "mood": "Fresh, product-launch, benefit-forward. Even soft studio lighting. Clean drop shadows. Feels like a Product Hunt hero card.",
            "elements_to_include": "One device with partial UI, 2-3 abstract benefit metaphors (chart / check / chat / data-flow), ONE bold outcome-focused heading, optional one-line subheading, up to 3 chips (platform / free-trial / integrations), brand logo top-left, bold CTA.",
            "elements_to_avoid": "Full app screenshot with every screen crammed in, dense feature lists as paragraphs, 3D dashboard renders piled up, human portraits (use software_product_hero for the product, not the founder), cyberpunk neon.",
        },
    },

    # ── ✂️ Minimal-Text (Auto's aesthetic + text discipline) ───────
    # The ONE difference from Auto is TEXT DENSITY. Every other visual
    # decision (composition, palette, typography, mood, whether to include
    # a real person, what kind of scene, product staging, editorial vs
    # illustrative, etc.) is exactly the same freedom Agent 1 gets under
    # Auto. The Art Director designs a rich image and, when deciding what
    # text to render on it, distils the brief to heading + optional
    # subheading + optional 1-3 short highlight chips + CTA. Long-form
    # copy stays in the caption.
    "minimal_text": {
        "label": "Minimal Text (Auto-Cull)",
        "group": "consulting_services",
        "emoji": "✂️",
        "when_to_use": "Editorial split layout matching the Spenzo AI reference set — logo top-left, heading + 1–2 sentence subheading + 2–4 premium-icon key points on one side, and a brief-driven supporting visual on the other (real workspace photo for follower_growth, contextual floating dashboard for viral_reach). Contact info auto-included when the brief provides it. CTA bottom-right.",
        "prompt_hint": (
            "design a clean editorial split — logo top-left, then heading, "
            "1–2 sentence subheading, 2–4 premium-icon key points (or pill "
            "chips), optional contact info row, brand-color CTA pill "
            "bottom-right; opposite half holds a supporting visual driven "
            "by the campaign brief (real workspace photo of someone using "
            "the product for follower_growth, or a stylized floating "
            "dashboard / data-viz with labels from the actual brief for "
            "viral_reach). NEVER generic device mockups or generic network "
            "graphs. NEVER render the full post copy verbatim."
        ),
        "visual_dna": {
            "composition": (
                "REFERENCE STYLE — the target aesthetic is the Spenzo AI "
                "reference set: a clean editorial split with a strong "
                "text zone on one side and a rich, CONTEXTUAL supporting "
                "visual on the other. The supporting visual MUST reflect "
                "the actual business (from Business DNA) and the actual "
                "campaign topic (from the brief). Never generic device "
                "mockups. Never abstract network graphs unrelated to the "
                "product.\n\n"
                "VARIANT BRANCHING — check the variant_type in the user "
                "message and pick the corresponding supporting-visual "
                "language. Text hierarchy and layout stay identical.\n"
                "  • variant_type == 'follower_growth' → REAL WORKSPACE "
                "PHOTOGRAPHY (Spenzo AI reference image 1 style). Show "
                "a real-looking professional in a real environment "
                "actually using / demonstrating the product. Examples: "
                "a marketing director at a dual-monitor desk with the "
                "product's real dashboard on-screen; a founder in a "
                "modern office reviewing the product on a tablet; a "
                "team lead at a whiteboard with the product visible in "
                "the background. Natural lighting, professional setting, "
                "everything relevant to the brief. Person must look "
                "natural — no AI face artifacts, no wrong-fingered "
                "hands. The person + real product context is the "
                "emotional hook.\n"
                "  • variant_type == 'viral_reach' / 'high_interaction' / "
                "'festival_variant' → CONTEXTUAL FLOATING DASHBOARD OR "
                "DATA-VIZ ART (Spenzo AI reference image 2 style). Show "
                "a stylized, high-fidelity dashboard or data-visualization "
                "graphic as a pure design element — NO device frame, NO "
                "laptop bezel, NO iPhone chrome. The panel must contain "
                "the ACTUAL metrics / concepts / labels from the campaign "
                "brief (not generic 'chart 1, chart 2' filler). "
                "Reference image 2 shows a 'Feel-Good Dashboard' section "
                "(vanity metrics: Impressions, Clicks, CTR, CPC) above "
                "an 'Incrementality View' section (Incremental Lift, "
                "Contribution by Channel, Control vs Exposed) — every "
                "label ties to the brief's message. Do the same: read "
                "the brief and label the dashboard panels with words "
                "from THIS campaign.\n"
                "  Both variants must feel BRIEF-DRIVEN, not template-"
                "driven. Whatever the brief is about is what the "
                "supporting visual shows.\n\n"
                "PREFERRED LAYOUT PATTERN — clean editorial split:\n"
                "  TOP-LEFT: brand logo, untouched, at comfortable "
                "margin. This position is MANDATORY and NEVER moves.\n"
                "  TEXT ZONE (left half on 1:1 and 16:9; top half on "
                "portrait): heading → subheading → 2–4 icon-labeled key "
                "points → optional contact info → CTA anchor.\n"
                "  VISUAL ZONE (right half on 1:1 and 16:9; bottom half "
                "on portrait): the variant-specific supporting visual "
                "described above. Fills the full opposite half.\n"
                "  BOTTOM-RIGHT: CTA button (brand-color pill with white "
                "text). This position is MANDATORY and NEVER moves. The "
                "CTA anchors to the bottom-right of the ENTIRE CANVAS — "
                "NOT the bottom-right of the text column. Even when the "
                "text zone occupies only the left half, the CTA still "
                "sits at the absolute bottom-right corner of the image "
                "(over / at the edge of the visual zone if needed). "
                "Never place the CTA at the bottom of the text column, "
                "never at the bottom-left, never center-bottom. On "
                "portrait aspect ratios the CTA still anchors to the "
                "absolute bottom-right corner of the canvas.\n"
                "  Text zone stays on one side, visual zone on the "
                "opposite side. Do NOT interleave. Do NOT float text on "
                "top of the visual.\n\n"
                "TEXT HIERARCHY — CONCRETE RULES:\n\n"
                "The image should include ALL of these, in order:\n\n"
                "  1. BRAND LOGO — top-left, use the provided logo file "
                "exactly as given. Do not recolor, restyle, or crop.\n\n"
                "  2. HEADING (large, bold display) — the strongest "
                "single message. 3–7 words, may wrap to 2 lines. "
                "Optional accent underline on a key word (see Spenzo AI "
                "reference image 2: 'Vanity Metrics' is underlined).\n\n"
                "  3. SUBHEADING (ONE short paragraph, 1–2 SENTENCES, "
                "aspect-ratio-aware). Length ceiling by ratio:\n"
                "     • Square 1:1  → 1–2 sentences (~12–25 words).\n"
                "     • Portrait 4:5 / 9:16 → 1–2 sentences (~12–25 "
                "words).\n"
                "     • Landscape 16:9 / 1.91:1 → 1 sentence "
                "(~8–15 words) — the visual dominates, text stays tight.\n"
                "   HARD LIMITS: MAX 2 sentences. ONE paragraph, NEVER "
                "TWO stacked blocks of prose. Write as a natural short "
                "line, NOT bullets, NOT a colon-then-list. Reference "
                "example (Spenzo AI image 1, square): 'Measure the real "
                "incremental impact of every marketing dollar.'\n\n"
                "  4. 2–4 KEY POINTS with PREMIUM ICONS. Choose either "
                "style based on brief density:\n"
                "     STYLE A (Spenzo AI reference image 1, richer): "
                "each key point = a stroked-line PREMIUM ICON in a "
                "rounded container + a bold 2–4 word LABEL. Icons must "
                "be premium editorial quality (crisp line art, tinted "
                "with the brand accent color, sitting in a subtle "
                "rounded chip). Examples from reference: [target icon] "
                "Blind spend / [chart icon] Real lift / [pie icon] "
                "Smarter allocation.\n"
                "     STYLE B (Spenzo AI reference image 2, tighter): "
                "each key point = a rounded PILL CHIP with a short "
                "label (2–5 words) — no icon needed inside the chip. "
                "Examples: 'Vanity metric spikes' | 'Incremental "
                "revenue lift' | 'Outcome-first attribution'.\n"
                "   HOW TO CHOOSE: if the brief has depth per feature "
                "(a real benefit story), use STYLE A with icons. If the "
                "brief has crisp taglines / one-liners, use STYLE B "
                "chips. Both styles cap at 4 items.\n"
                "   NEVER: full paragraph per point, two sentences per "
                "point, colon-then-paragraph pattern.\n\n"
                "  5. CONTACT INFORMATION (OPTIONAL, brief-driven). If "
                "the campaign brief provides a website URL, phone "
                "number, email, or physical address, INCLUDE the "
                "relevant items in a small, tasteful row above or beside "
                "the CTA — but only when the aspect ratio has room. "
                "Priority order when space is tight: website > phone > "
                "email > address. Landscape and square usually fit 1–2 "
                "items; portrait can fit 2–3.\n\n"
                "  6. CTA BUTTON — 2–4 words, brand-color pill, white "
                "text, ALWAYS anchored to the ABSOLUTE bottom-right "
                "CORNER OF THE ENTIRE CANVAS (NOT the bottom-right of "
                "the text column). If the text zone is on the left, the "
                "CTA still sits at the far right — it may overlap or "
                "sit at the edge of the supporting visual. Never place "
                "it at the bottom-left, never at the bottom of the text "
                "column, never center-bottom. Examples: 'Learn more', "
                "'Book a demo', 'Talk to us', 'See how →'.\n\n"
                "PRINCIPLES:\n"
                "  1. The image must tell the WHOLE story on its own — "
                "a scroller who doesn't read the caption should still "
                "get the message.\n"
                "  2. NEVER render the full post copy verbatim. Distil "
                "long descriptions into 3–7 word labels or 8–15 word "
                "one-liners.\n"
                "  3. The supporting visual is DRIVEN BY THE BRIEF, not "
                "a template. Read the brief, understand what the product "
                "actually does, then design the visual to reinforce that "
                "specific message.\n"
                "  4. ONE paragraph of supporting copy on the image "
                "(the subheading). NEVER two stacked paragraphs.\n"
                "  5. Logo top-left is MANDATORY. CTA bottom-right is "
                "MANDATORY. Do not move them.\n"
                "  6. NEVER add a fake device mockup (stylized empty "
                "laptop / iPhone frame with placeholder UI). If a "
                "device appears, it appears BECAUSE the follower_growth "
                "variant needs a real workspace scene and the person is "
                "actually using the device — the device is part of a "
                "real photograph, not a rendered mockup.\n"
                "  7. Every visual element (icons, labels, chips, "
                "dashboard tiles, workspace props) must be tied to the "
                "brief's actual subject matter. No generic filler."
            ),
            "palette": (
                "Same freedom as Auto — pick colours that fit the brand, "
                "category, mood, and any occasion the brief mentions. Brand "
                "colour <BRAND_COLOR> appears as accent (headline highlight, "
                "CTA button, small motif), never as background wash or "
                "dominant fill."
            ),
            "typography": (
                "Same freedom as Auto — modern sans, elegant serif, editorial "
                "display, whatever fits the brand and the tone of the piece. "
                "One typographic system, committed throughout. Confident "
                "heading, supportive subheading, small clean chip labels."
            ),
            "mood": (
                "Same freedom as Auto — corporate, editorial, warm lifestyle, "
                "premium tech, celebratory, calm-authoritative — whatever the "
                "brand voice and campaign intent call for. Not restricted to "
                "'calm and minimal.'"
            ),
            "elements_to_include": (
                "Brand logo TOP-LEFT (mandatory, untouched). Strong "
                "HEADING (3–7 words, may wrap to 2 lines; optional "
                "accent underline on a key word). ONE SUBHEADING "
                "paragraph, 1–2 sentences, aspect-ratio-aware (~12–25 "
                "words on square/portrait, ~8–15 on landscape). 2–4 KEY "
                "POINTS with either premium-icon + label (Spenzo AI "
                "reference image 1 style) OR pill chips with short "
                "labels (Spenzo AI reference image 2 style) — pick the "
                "style that fits the brief's depth. Icons must be "
                "premium editorial line-art, tinted with brand accent, "
                "sitting in subtle rounded chip containers. CONTACT "
                "INFO from the brief (website / phone / email / "
                "address) placed tastefully near the CTA when the "
                "aspect ratio has room. CTA button anchored to the "
                "ABSOLUTE BOTTOM-RIGHT CORNER OF THE ENTIRE CANVAS "
                "(mandatory, brand-color pill, white text, 2–4 words; "
                "NOT the bottom of the text column — the far-right edge "
                "of the whole image, even if it overlaps the supporting "
                "visual). "
                "ONE rich SUPPORTING VISUAL on the opposite half — "
                "variant-driven: follower_growth = real workspace "
                "photograph of a professional actually using the "
                "product (natural lighting, real environment); "
                "viral_reach = stylized floating dashboard / data-viz "
                "graphic with labels drawn from the actual campaign "
                "brief (no device frame, no bezel — pure design "
                "element). Every visual element must tie back to what "
                "the product actually does and what THIS specific "
                "campaign is about."
            ),
            "elements_to_avoid": (
                "GENERIC DEVICE MOCKUPS — no stylized fake laptop / "
                "iPhone / iPad frames with placeholder UI as the hero. "
                "If a device appears, it appears only because the "
                "follower_growth variant needs a real workspace "
                "photograph and the person is genuinely using the "
                "device (device is part of a real photo, not a "
                "rendered chrome frame). GENERIC ABSTRACT NETWORK "
                "GRAPHS unrelated to the brief. STOCK-PHOTO SCENES "
                "that don't reflect the actual product. TWO OR MORE "
                "PARAGRAPHS of supporting copy on the image — one "
                "subheading paragraph only. Subheadings longer than 2 "
                "sentences. The entire post copy rendered verbatim. "
                "Key points written as multi-line paragraphs or "
                "colon-then-body-paragraph patterns. More than 4 key "
                "points (2–4 is the ceiling — Spenzo AI references use "
                "exactly 3). Multi-line contact blocks (keep contact "
                "info to a compact 1–2 line row). Logo anywhere except "
                "top-left. CTA anywhere except the absolute bottom-right "
                "corner of the entire canvas — placing the CTA at the "
                "bottom-right of the text column (instead of the far "
                "right edge of the whole image) is a common mistake and "
                "is FORBIDDEN. Text "
                "overlaid on top of the supporting visual (keep them "
                "on opposite halves). Placeholder or lorem-style "
                "labels on dashboards / chips — every label must come "
                "from the brief. Also avoid the OPPOSITE failure: "
                "don't strip so much that only heading + chips remain "
                "— the image must stand alone."
            ),
        },
    },

    # ── 💼 SaaS / Service (added) ──────────────────────────────────
    "b2b_executive_poster": {
        "label": "B2B Executive Poster",
        "group": "creative",
        "emoji": "👔",
        "when_to_use": "Enterprise B2B thought-leadership, whitepaper launch, exec quote, consulting-firm hero — clean editorial feel",
        "prompt_hint": (
            "clean editorial B2B poster with generous whitespace, one bold "
            "headline, optional one-line executive quote or subheading, up to "
            "three icon-labeled proof points, no crowded scene"
        ),
        "visual_dna": {
            "composition": "Editorial magazine-style layout with clear whitespace zones. Left column: bold headline + optional one-line exec quote or subheading. Right column: either ONE clean chart or ONE professional executive portrait (bordered rounded rectangle, never bleeding across the layout). Below: a horizontal strip of 3 icon-and-label proof-point cards. This is NOT a busy infographic — restraint is the point.",
            "palette": "Navy + white + one accent (brand colour) OR charcoal + cream + one accent. Never more than 3 colours plus the logo. High readability priority.",
            "typography": "Editorial serif OR strong modern sans for the headline (choose one system and commit). Small clean sans for proof points. Confident hierarchy. Ample line-height. Feels like Harvard Business Review or Bloomberg.",
            "mood": "Authoritative, credible, calm, boardroom-ready. Even flat design lighting OR soft studio portrait lighting if a face is included. NOT flashy, NOT cyberpunk, NOT playful.",
            "elements_to_include": "ONE bold heading, optional one-line subheading OR one-sentence exec quote with attribution, up to 3 icon-labeled proof-point cards, optional one chart OR one exec portrait, brand logo top-left, minimal CTA.",
            "elements_to_avoid": "Cartoon characters, cyberpunk, watercolour, cluttered dashboards, multiple portraits, dense body paragraphs, glowing effects, 3D mascots, product mockups in device frames (use ui_mockup for that).",
        },
    },

    # ── 🎨 Free Style (raw gpt-image-2, zero prompt engineering)
    # Marker style — when picked, the pipeline routes to
    # services/free_style_pipeline.py. NO Prompt Writer agent, NO
    # structured prompt template, NO rules. The prompt sent to
    # gpt-image-2 is literally:
    #     generate a image {raw_campaign_brief} and {post_content}
    # The brand logo is attached via images.edit so it appears in the
    # render. Everything else — layout, imagery, typography, CTA,
    # colours — is left entirely to the image model's own judgement.
    "free_style_post": {
        "label": "Free Style",
        "group": "creative",
        "emoji": "🎨",
        "when_to_use": (
            "Zero prompt engineering. The image model receives only the "
            "raw campaign brief + post text + brand logo and decides "
            "everything else. Use when you want to see what gpt-image-2 "
            "produces without any Fortune-500 direction, section templates, "
            "or hard rules. Great for creative exploration and A/B tests "
            "against the User Intent style."
        ),
        "prompt_hint": "",   # Router style — the free-style pipeline builds the prompt.
        "visual_dna": None,  # Marker — do NOT run the standard style-first prompt builder.
    },

    # ── 💬 User Intent Post (single-call ChatGPT-style agent)
    # Marker style — when picked, the pipeline routes to
    # services/user_intent_pipeline.py instead of the standard flow.
    # A single GPT-5.1 agent reads the post copy + Business DNA + brief,
    # picks its OWN style based on what the content is about, and hands
    # off a free-form image_prompt to gpt-image-2 high for rendering.
    # No locked layouts, no locked CTA vocab, no 7-day memory — matches
    # how ChatGPT's built-in image tool behaves.
    "user_intent_post": {
        "label": "User Intent",
        "group": "creative",
        "emoji": "💬",
        "when_to_use": (
            "ChatGPT-style: let a single agent read the post copy and "
            "figure out the right image on its own. No locked layouts, "
            "no locked CTAs, no memory guardrails. Best when you want "
            "maximum flexibility and trust the model to match style to "
            "post intent. Hard rules kept: logo top-left, CTA bottom "
            "corner, brand colours from DNA, image + text integrated, "
            "high quality."
        ),
        "prompt_hint": "",   # Router style — the User Intent agent builds the actual prompt.
        "visual_dna": None,  # Marker — do NOT run the standard style-first prompt builder.
    },

    # ── 🖌️ Designer-Grade Post (Image Director + Nano Banana pipeline)
    # This style is a PIPELINE MARKER. When picked, the standard
    # Art Director → gpt-image-2 flow is bypassed in favour of a
    # dedicated two-agent flow implemented in
    # services/image_director_pipeline.py: an Image Director agent
    # (GPT-5.1) reads the FULL business DNA + campaign brief, picks
    # the best DNA-native colour combination for text legibility,
    # locks the CTA to a controlled vocabulary, and hands off to
    # Gemini 2.5 Flash Image Preview (Nano Banana) for high-quality
    # rendering with clean text.
    "designer_grade_post": {
        "label": "Designer-Grade Post",
        "group": "creative",
        "emoji": "🖌️",
        "when_to_use": (
            "Software / SaaS / IT-consulting brands that want a top-designer "
            "feel — sharp typography, contrast-aware colour picks from the "
            "Business DNA, mandatory clear heading, real call-to-action "
            "language (Book a Demo, Explore, See it in Action, etc.), and "
            "no AI-slop artefacts. Best for product launches, feature "
            "highlights, service announcements, and campaign hero posts "
            "where the image needs to look intentionally designed, not "
            "generated. Uses a dedicated two-agent pipeline (Image "
            "Director + Nano Banana image agent) — bypasses the standard "
            "Art Director + gpt-image-2 flow."
        ),
        "prompt_hint": "",   # This style is a router; the Image Director builds the actual prompt.
        "visual_dna": None,  # Marker — do NOT run the standard style-first prompt builder.
    },

    # ── ⚡ Edit Style (Responses API image_generation tool)
    # This style is a PIPELINE MARKER. When picked, the standard Art
    # Director + gpt-image-2 flow (and the Image Director / User Intent
    # marker flows) are bypassed in favour of a single call to OpenAI's
    # Responses API `image_generation` tool, implemented in
    # services/edit_style_pipeline.py. Business DNA + campaign brief + the
    # already-written post text are handed straight to the tool-enabled
    # model, which plans AND renders in one call — no separate
    # prompt-writing agent. Always uses action="generate" (never "edit",
    # never multi-turn refinement — one shot per variant) and always
    # produces exactly 2 variants for the user to pick from in the UI,
    # instead of the usual 3/4.
    "edit_style": {
        "label": "Edit Style",
        "group": "creative",
        "emoji": "⚡",
        "when_to_use": (
            "Fastest, most flexible option — a single call hands your "
            "Business DNA, campaign brief, and post text straight to "
            "OpenAI's native image-generation tool and lets it plan the "
            "whole image itself. Generates 2 variants to pick from. Always "
            "a fresh one-shot generation — never an edit of a prior image."
        ),
        "prompt_hint": "",   # Router style — edit_style_pipeline builds the actual call.
        "visual_dna": None,  # Marker — do NOT run the standard style-first prompt builder.
    },

    # ── 🎨 Social Media Designer (fresh Art Director approach)
    # Marker style — when picked, the pipeline routes to
    # services/social_media_designer_pipeline.py.
    # Deliberately different shape from the other marker pipelines: no
    # forced logo position, no forced CTA position, no rigid 3-slot text
    # schema, no character caps. The Art Director (GPT-5.1) returns ONE
    # free-form design brief (300-500 words) plus the aspect ratio it
    # chose — everything else is a design decision the Art Director makes
    # inside that brief. gpt-image-2 at quality=high renders. 2 variants.
    "social_media_designer": {
        "label": "Social Media Designer",
        "group": "creative",
        "emoji": "🎨",
        "when_to_use": (
            "A senior designer's approach — an Art Director agent reads "
            "your post, DNA, and brief and writes one free-form design "
            "brief with full freedom on layout, aspect ratio, colors, and "
            "logo placement. No template. No 3-slot text form. Text uses "
            "bright saturated colors for legibility. Generates 2 variants "
            "to pick from. Best when you want the model to think like a "
            "designer, not fill out a template."
        ),
        "prompt_hint": "",   # Router style — social_media_designer_pipeline builds the prompt.
        "visual_dna": None,  # Marker — do NOT run the standard style-first prompt builder.
    },

    # ── 🧩 Software Product (MuleSoft / Salesforce reference set) ──
    # Two entries: (1) a FIXED "software product marketing" aesthetic
    # distilled from all 5 references (deep blue product-marketing hero
    # with agent orchestration / architecture diagrams as the language),
    # and (2) an ADAPTIVE variant where the Art Director reads the brief
    # + Business DNA and picks the best of five sub-templates.
    "software_product_marketing": {
        "label": "Software Product Marketing",
        "group": "software_product",
        "emoji": "🧩",
        "when_to_use": (
            "Software / AI / integration platforms doing product-marketing "
            "posts — agent architecture, orchestration diagrams, platform "
            "capability shots. Distilled from the MuleSoft / Salesforce "
            "reference set: deep blue product-marketing palette, rounded "
            "white cards for product entities, small platform badges, and "
            "clean connector lines. Best for architecture / capability / "
            "'how it works' style posts."
        ),
        "prompt_hint": (
            "deep-blue product-marketing hero in the MuleSoft / Salesforce "
            "reference language: rounded white cards for product entities "
            "(agents, systems, tools), small pill badges for platform / "
            "capability labels, thin connector lines showing flow, bold "
            "clean typography, subtle sparkle / accent shapes, brand logo "
            "top-left, CTA bottom-right"
        ),
        "visual_dna": {
            "composition": (
                "Deep-blue product-marketing composition in the "
                "MuleSoft / Salesforce reference language. The hero "
                "visual is a clean AGENT / SYSTEM ORCHESTRATION DIAGRAM "
                "or ARCHITECTURE FLOW built from rounded white cards "
                "connected by thin curved / straight lines with subtle "
                "arrow-ends.\n\n"
                "LAYOUT:\n"
                "  TOP-LEFT: brand logo (untouched), plus optional small "
                "brand wordmark to its right.\n"
                "  TEXT ZONE (left third on 1:1 / 16:9, or top area on "
                "portrait): heading (large, bold, may wrap 2 lines, "
                "optional accent underline on a key word) → 1–2 sentence "
                "subheading → 2–4 icon-labeled key points OR pill chips.\n"
                "  HERO VISUAL ZONE (right two-thirds on 1:1 / 16:9, or "
                "bottom on portrait): the orchestration diagram — a set "
                "of rounded white cards containing product entities "
                "(agents, systems, tools, modules from the brief), each "
                "card showing: small icon or robot avatar + entity name "
                "in bold + a small colored pill badge (label like 'Agent' "
                "/ 'MCP' / 'API' / 'Tool') + optional smaller platform "
                "label below (like 'Anypoint' / 'Bedrock' / 'Azure' — "
                "when the brief mentions specific platforms). Cards "
                "connect via thin light-blue lines with subtle arrow "
                "endings, arranged either as a hierarchical tree "
                "(hub → spokes → sub-spokes) or a left-to-right "
                "architecture flow.\n"
                "  ABSOLUTE BOTTOM-RIGHT: CTA button (brand-color pill, "
                "white text, 2–4 words). MANDATORY position — corner of "
                "the entire canvas, not corner of the text column.\n\n"
                "BACKGROUND: deep navy blue (#0A1E4C to #12295F range) "
                "with a subtle vertical gradient (slightly lighter at "
                "the center). Optional faint dot-grid or particle-field "
                "texture at very low opacity. NO cyberpunk neon, NO "
                "photograph overlay.\n\n"
                "OPTIONAL DECORATIVE ACCENTS: 1–3 small teal / mint "
                "SPARKLE or four-point star shapes floating in "
                "whitespace (matching the MuleSoft reference posters). "
                "Keep them subtle — decoration, not the hero.\n\n"
                "CARD DESIGN: each card is a rounded rectangle (12–16px "
                "radius), white or very light off-white fill, subtle "
                "drop shadow (blue-tinted). Inside: 24×24 icon or "
                "avatar circle on the left, entity name in bold dark "
                "text, small pill badge (2 letters or short label) in a "
                "brand accent color on the right side of the header, "
                "and an optional small subtitle line with a shield / "
                "cloud icon + platform name below.\n\n"
                "TYPOGRAPHY: modern geometric sans-serif (Inter, SF Pro "
                "Display, or similar). Heading = 60–96pt bold. "
                "Subheading = 18–24pt regular. Card names = 16–18pt "
                "semibold. Pill labels = 10–12pt semibold, uppercase or "
                "small caps.\n\n"
                "CONNECTOR LINES: thin (1.5–2px), light blue "
                "(#7BB3FF or a slightly desaturated brand accent), "
                "curved OR straight with 90-degree bends, ending in "
                "small arrow tips at each destination. The lines should "
                "read as data / control flow, not decoration.\n"
            ),
            "palette": (
                "Deep navy blue background (#0A1E4C to #12295F). White "
                "and off-white cards. Light blue (#7BB3FF) connector "
                "lines. Brand accent color <BRAND_COLOR> used for pill "
                "badges, key underline on the heading, and the CTA "
                "button. Optional purple / pink pill badges (#B266FF) "
                "for special tags like 'MCP' or 'Agent'. Optional "
                "teal / mint (#5EEAD4) for decorative sparkles. Never "
                "more than 4 colors plus the logo."
            ),
            "typography": (
                "Modern geometric sans-serif. Confident bold heading, "
                "clean semibold card titles, small-caps pill labels. "
                "Ample line-height. Feels like a modern SaaS marketing "
                "site (Vercel / Linear / Anthropic) — not a "
                "traditional enterprise deck."
            ),
            "mood": (
                "Technical, confident, product-forward. Clean tech "
                "marketing energy. NOT flashy, NOT cyberpunk. Deep-blue "
                "authoritative palette with playful sparkle accents."
            ),
            "elements_to_include": (
                "Brand logo top-left (mandatory, untouched). Heading "
                "(3–7 words, may wrap 2 lines, optional accent "
                "underline on key word). 1–2 sentence subheading. 2–4 "
                "icon-labeled key points OR pill chips. HERO "
                "ORCHESTRATION DIAGRAM: rounded white cards for entities "
                "from the brief (agents / systems / tools / modules), "
                "each card with icon + name + small pill badge + "
                "optional platform label; connected via thin light-blue "
                "curved / straight lines with arrow endings. Deep navy "
                "background. Optional 1–3 teal sparkle shapes. CTA "
                "button anchored to ABSOLUTE bottom-right corner of the "
                "entire canvas (brand-color pill, white text, 2–4 "
                "words). Contact info (website / phone / email from the "
                "brief) placed tastefully near the CTA when the aspect "
                "ratio has room."
            ),
            "elements_to_avoid": (
                "Photographs of people or real workspaces. Device "
                "mockups (laptop / phone / iPad frames). Generic "
                "network graphs unrelated to the brief. Cyberpunk neon "
                "glows. 3D rendered characters or mascots. Cartoon or "
                "watercolor styling. Cluttered scenes with 8+ cards — "
                "cap at 6 cards for readability. Rendering the full "
                "post copy verbatim. Multiple paragraphs of body text. "
                "Logo anywhere except top-left. CTA anywhere except the "
                "absolute bottom-right corner of the entire canvas. "
                "Placeholder / lorem card labels — every entity on a "
                "card must come from the brief's actual product / "
                "system list."
            ),
        },
    },

    "software_product_adaptive": {
        "label": "Software Product (Adaptive)",
        "group": "software_product",
        "emoji": "🎨",
        "when_to_use": (
            "Software / AI / integration platforms — the Art Director "
            "reads the campaign brief and Business DNA, then chooses "
            "the best of 5 sub-templates from the MuleSoft / Salesforce "
            "reference set: agent orchestration diagram, before-vs-after "
            "comparison, product-in-context screenshot, architecture "
            "flow diagram, or bold announcement poster. Use when you "
            "want the visual TYPE to auto-fit the brief's shape."
        ),
        "prompt_hint": (
            "read the brief + business DNA and pick ONE of five "
            "sub-templates from the MuleSoft / Salesforce reference "
            "set: (A) agent orchestration diagram, (B) before-vs-after "
            "split, (C) product-in-context screenshot, (D) architecture "
            "flow, (E) bold typography announcement poster; brand logo "
            "top-left, CTA bottom-right"
        ),
        "visual_dna": {
            "composition": (
                "ADAPTIVE MULTI-TEMPLATE STYLE — the Art Director reads "
                "the campaign brief and Business DNA, then picks ONE of "
                "five sub-templates (below) that best fits the message. "
                "Do NOT mix templates in the same image; commit to one. "
                "All five templates share the same MANDATORY anchors: "
                "brand logo TOP-LEFT (untouched); CTA button at the "
                "ABSOLUTE bottom-right corner of the entire canvas.\n\n"
                "TEMPLATE PICKER — decide by reading the brief:\n\n"
                "  (A) AGENT ORCHESTRATION DIAGRAM — pick when the brief "
                "is about how AI agents / systems / tools connect and "
                "collaborate, when it names specific platforms "
                "(Anypoint, Bedrock, Azure AI Foundry, Agentforce, "
                "Salesforce, HubSpot, etc.), or when the message is "
                "'here's the architecture'. Reference: MuleSoft "
                "'Employee Onboarding Broker'. Style: deep navy "
                "background, rounded white cards for each agent / "
                "system, each card showing icon + entity name + small "
                "pill badge ('Agent' / 'MCP' / 'API') + optional "
                "platform label below; cards connected by thin "
                "light-blue arrows in a hierarchical tree or hub-spoke "
                "layout.\n\n"
                "  (B) BEFORE-VS-AFTER COMPARISON — pick when the brief "
                "contrasts an old way with a new way, a manual process "
                "with an automated one, or a broken pattern with a "
                "solved one. Reference: Salesforce 'How guided "
                "determinism works'. Style: light sky-blue background "
                "with white space, bold heading spanning the top (with "
                "accent underline on the key word), split-panel layout "
                "(left = OLD / simple, right = NEW / rich), colored "
                "boxes (pink / blue / teal / purple) for entities, thin "
                "arrows for flow, 2–4 small yellow / teal star sparkle "
                "accents in the whitespace.\n\n"
                "  (C) PRODUCT-IN-CONTEXT SCREENSHOT — pick when the "
                "brief is about a specific product feature, a UI, a "
                "'here's what it looks like in your workflow' moment, "
                "or an integration inside a familiar surface (Slack, "
                "Teams, Notion, Chrome, IDE). Reference: MuleSoft Agent "
                "in Slack. Style: render a clean, realistic screenshot "
                "of the product surface — sidebar / header / message "
                "list / input row / action buttons — with the actual "
                "product's messaging visible in the panel. Keep the "
                "chrome faithful to the host app; the product's own "
                "features are the hero. Overlay the brand heading + "
                "subheading in a text zone above or beside the "
                "screenshot.\n\n"
                "  (D) ARCHITECTURE FLOW DIAGRAM — pick when the brief "
                "is about system integration, infrastructure, "
                "lifecycle, pipeline stages, or 'how it fits into your "
                "stack'. Reference: MuleSoft 'Infrastructure as Code'. "
                "Style: soft teal / mint background, white cards in a "
                "clean 3-column architecture flow (left column = "
                "traditional lifecycle, center = the product, right = "
                "modern lifecycle OR outcome), thin arrows connecting "
                "columns, no decorative elements — restraint is the "
                "point.\n\n"
                "  (E) BOLD TYPOGRAPHY ANNOUNCEMENT POSTER — pick when "
                "the brief is a partnership announcement, a launch, a "
                "milestone, an award, or a news beat. Reference: "
                "MuleSoft 'Salesforce Partners with Databricks'. "
                "Style: deep navy or gradient background, the headline "
                "DOMINATES the entire canvas (60–120pt bold, 3–5 "
                "lines), white + brand-accent-color text with one key "
                "word in the accent color, 2–5 teal / mint sparkle "
                "decorations in the corners and gaps, small brand logo "
                "at the bottom-center or top-left. NO product visual, "
                "NO diagram — the message IS the hero.\n\n"
                "SHARED RULES (all five templates):\n"
                "  • Brand logo TOP-LEFT (untouched).\n"
                "  • Heading in bold display type, may wrap 2 lines, "
                "optional accent underline on one key word.\n"
                "  • Subheading 1–2 sentences (~15–30 words on square "
                "/ portrait, ~10–20 on landscape). ONE paragraph only.\n"
                "  • CTA button anchored to the ABSOLUTE bottom-right "
                "corner of the entire canvas (brand-color pill, white "
                "text, 2–4 words). Templates C, D can accept a small "
                "CTA on the bottom-center if the composition strictly "
                "requires it — but bottom-right is the default.\n"
                "  • Every visual element (card names, screenshot "
                "labels, comparison boxes, poster headline) must be "
                "driven by the actual campaign brief and Business DNA. "
                "No lorem / placeholder / generic filler.\n"
                "  • Contact info (website / phone / email from the "
                "brief) placed tastefully near the CTA when the aspect "
                "ratio has room.\n"
            ),
            "palette": (
                "Template A: deep navy (#0A1E4C) + white cards + "
                "light-blue connectors + brand accent pills.\n"
                "Template B: light sky-blue (#EAF3FF) + white space + "
                "multi-color entity boxes (pink, blue, teal, purple) + "
                "yellow / teal sparkle accents + brand accent underline.\n"
                "Template C: neutral gray host-app chrome (Slack blue / "
                "Teams purple / Notion white) + brand accent inside the "
                "product panel.\n"
                "Template D: soft teal / mint (#D4F2E7) background + "
                "white cards + navy text + one brand accent.\n"
                "Template E: deep navy or navy-to-purple gradient + "
                "white heading + brand accent on ONE key word + teal "
                "sparkles.\n"
                "In all templates the brand color <BRAND_COLOR> is the "
                "single dominant accent. Never more than 4 colors plus "
                "the logo per image."
            ),
            "typography": (
                "Modern geometric sans-serif (Inter, SF Pro Display, "
                "similar). Bold display heading. Semibold card / "
                "section titles. Small-caps or lowercase pill labels. "
                "Confident hierarchy, ample line-height. Modern SaaS "
                "marketing feel."
            ),
            "mood": (
                "Confident, technical, product-forward. Template A + D "
                "= architectural / structured. Template B = "
                "conversational / didactic. Template C = friendly, "
                "in-context. Template E = celebratory, big-news. "
                "Never cyberpunk, never cartoon, never dark-mode "
                "gaming."
            ),
            "elements_to_include": (
                "Brand logo top-left (mandatory, untouched). Heading "
                "(bold display, optional accent underline). 1–2 "
                "sentence subheading. Template-specific hero (see "
                "composition). CTA button anchored to ABSOLUTE "
                "bottom-right corner of the entire canvas (brand-color "
                "pill, white text, 2–4 words). Contact info from the "
                "brief placed near the CTA when the aspect ratio has "
                "room. Every visual element driven by the brief."
            ),
            "elements_to_avoid": (
                "Mixing multiple templates in one image (commit to "
                "one). Cyberpunk neon. Cartoon / watercolor styling. "
                "3D rendered characters or mascots. Photographs of "
                "people (except Template C's realistic-UI screenshot). "
                "Device mockups (fake laptop / phone frames) unless "
                "Template C. Placeholder / lorem labels — every entity "
                "on the diagram must come from the brief's actual "
                "product / system list. Logo anywhere except top-left. "
                "CTA anywhere except the absolute bottom-right corner "
                "of the entire canvas (placing it at the bottom of the "
                "text column is a common mistake and is FORBIDDEN). "
                "Rendering the full post copy verbatim. Multiple "
                "paragraphs of body text on the image."
            ),
        },
    },

    "software_product_framework": {
        "label": "Framework / Methodology Infographic",
        "group": "software_product",
        "emoji": "📐",
        "when_to_use": (
            "Software / SaaS / analytics platforms explaining a "
            "methodology, framework, comparison, or educational "
            "concept — long-form infographic with hand-drawn / "
            "sketch-style icons, muted editorial palette, multi-"
            "section structure. Best for whitepaper posts, thought-"
            "leadership explainers, 'how it works' teaching content. "
            "Layout auto-adapts by aspect ratio: portrait / square = "
            "dark stacked-sections; landscape = light two-column "
            "framework diagram. Reference: NotebookLM-style research "
            "infographics ('The Death of MTA', 'Unified Marketing "
            "Measurement Blueprint')."
        ),
        "prompt_hint": (
            "framework infographic — bold serif headline at top, "
            "hand-drawn sketch-style icons inside coloured circles / "
            "ovals, muted editorial palette (teal + coral + mustard + "
            "purple + cream, with the brand colour as one accent among "
            "several); layout depends on aspect ratio: PORTRAIT / "
            "SQUARE → dark navy background with vertical stacked "
            "sections (headline → numbered reasons row → comparison "
            "table → pillars / architecture diagram → optional warning "
            "callout); LANDSCAPE → cream / off-white background with "
            "two-column side-by-side framework (left column stacks "
            "tiered concept cards, right column shows a triangle / "
            "loop / cycle diagram). Educational tone, no CTA, no "
            "promotional chips"
        ),
        "visual_dna": {
            "composition": (
                "FRAMEWORK / METHODOLOGY INFOGRAPHIC. This style "
                "teaches a concept through a visual framework — like "
                "a page from a McKinsey deck, a Gartner report, or a "
                "NotebookLM explainer. Layout is ASPECT-RATIO-ADAPTIVE:\n\n"
                "═══════════════════════════════════════════\n"
                "TEMPLATE A — PORTRAIT / SQUARE (Ref: 'The Death of "
                "MTA and the Rise of Unified Measurement')\n"
                "═══════════════════════════════════════════\n"
                "Use when aspect ratio is portrait (4:5, 9:16, 3:4) "
                "or square (1:1).\n\n"
                "BACKGROUND: deep navy blue (#0A2A4E to #12356B "
                "range) with a subtle dot-grid or fine texture at "
                "very low opacity.\n\n"
                "VERTICAL STACKED SECTIONS (top to bottom):\n"
                "  1. BIG HEADLINE at top — bold sans-serif or "
                "editorial serif in WHITE, 2–3 lines, centered or "
                "left-aligned. Optional smaller italic subtitle "
                "underneath.\n"
                "  2. NUMBERED-REASONS ROW — a horizontal strip of "
                "3–6 mini-cards, each with a hand-drawn / sketch "
                "icon inside a coloured circle or rounded rectangle, "
                "a bold short label (2–4 words), and a 1–2 sentence "
                "description underneath in light-grey text. The "
                "coloured circles use varied palette tones (teal, "
                "coral, mustard, purple, cream) — the brand colour "
                "<BRAND_COLOR> is ONE among several.\n"
                "  3. COMPARISON TABLE — a 2-column table with a "
                "coloured header bar (e.g. 'Old Approach' vs 'New "
                "Approach'), row labels down the left, and "
                "checkmark/x-style verdicts down each column. Use "
                "green ✓ and red ✗ for verdicts. Rows separated by "
                "faint dividers.\n"
                "  4. ARCHITECTURE / PILLARS DIAGRAM — 3 marble-"
                "column-style pillars (or 3 stacked cards) forming "
                "the foundation of a concept, each labelled with a "
                "short capability name and 1-line description. Above "
                "the pillars, a summary bar with the overall "
                "framework name. This is the visual climax of the "
                "infographic.\n"
                "  5. Optional WARNING or CALLOUT box at the bottom "
                "— a coloured rounded rectangle with a warning icon "
                "and 1–2 sentence 'cost of inaction' or 'why it "
                "matters' message.\n\n"
                "═══════════════════════════════════════════\n"
                "TEMPLATE B — LANDSCAPE (Ref: 'The Unified Marketing "
                "Measurement Blueprint — Beyond the Single Number "
                "Mirage')\n"
                "═══════════════════════════════════════════\n"
                "Use when aspect ratio is landscape (16:9, 1.91:1, "
                "21:9).\n\n"
                "BACKGROUND: cream / off-white (#F5F0E8 to #FAF6EE) "
                "with subtle warm tint. Never a pure white — always "
                "editorial cream.\n\n"
                "LAYOUT:\n"
                "  • BIG CENTERED HEADLINE at top — bold editorial "
                "SERIF in near-black, 2 lines (heading + smaller "
                "italic subtitle). Feels like a magazine article "
                "opener.\n"
                "  • TWO SIDE-BY-SIDE COLUMNS below the headline:\n"
                "     LEFT COLUMN — 'The Hierarchy of X' — a "
                "vertical stack of 3 tiered concept cards (Strategic "
                "/ Tactical / Operational, or similar tier vocab from "
                "the brief). Each card is an OVAL / CAPSULE shape "
                "with a hand-drawn icon inside a coloured oval on the "
                "left, a bold label ('Primary Instrument: [name]') "
                "in the middle, and a small right-side pill labeled "
                "'The Decision-Fit Question' with the concept name "
                "('Budget Mix' / 'Channel Scale' / 'Bids/Creative'). "
                "Each card uses a different palette colour (teal / "
                "coral / purple).\n"
                "     RIGHT COLUMN — 'The Orchestration Loop / The "
                "Triangle' — a triangular diagram with 3 nodes "
                "(Experiments / MMM / Attribution or similar), each "
                "node inside a coloured circle with a hand-drawn "
                "icon, connected by thin curved coloured arrows "
                "forming a closed loop or triangle. Each node has a "
                "short 2–3 sentence role description positioned "
                "around the triangle (top-left, top-right, bottom).\n"
                "  • Each column has its own subheading in bold "
                "serif above it, and small italic descriptive text "
                "underneath the subheading.\n\n"
                "═══════════════════════════════════════════\n"
                "SHARED RULES (both templates):\n"
                "═══════════════════════════════════════════\n"
                "  • ICONS are hand-drawn / sketch style — never "
                "flat corporate vector icons. They should feel like "
                "quick editorial illustrations (magnifying glass, "
                "stopwatch, flask, bar chart, target, network node, "
                "chain link, TV screen, dollar sign, etc.).\n"
                "  • Icons sit inside COLOURED CONTAINERS (circles, "
                "ovals, rounded squares) that vary by palette tone.\n"
                "  • NO promotional CTA button, NO pill highlight "
                "chips as separate elements, NO 'Book a Demo' style "
                "conversion pushes. This is educational content.\n"
                "  • NO prominent brand logo overlay — the logo can "
                "sit small in a corner (top-left ~40px OR "
                "bottom-right as a watermark), or be omitted if the "
                "brand name is baked into the framework title.\n"
                "  • Content labels (framework tier names, pillar "
                "names, comparison-row titles, callout copy) MUST "
                "come from the actual campaign brief and Business "
                "DNA — never lorem-ipsum, never invented framework "
                "vocab. The Art Director reads the brief and derives "
                "the sections from it.\n"
                "  • Optional bottom-right watermark: brand name in "
                "small caps or a small brand logo (like NotebookLM's "
                "'@NotebookLM' bottom-right).\n"
            ),
            "palette": (
                "MUTED EDITORIAL PALETTE — the framework style uses "
                "a soft multi-tone palette across the icons and "
                "cards, NOT a single dominant brand colour:\n"
                "  • Portrait template (Template A) background: deep "
                "navy (#0A2A4E–#12356B). Text on background = white "
                "and off-white / light grey.\n"
                "  • Landscape template (Template B) background: "
                "cream / warm off-white (#F5F0E8–#FAF6EE). Text on "
                "background = near-black.\n"
                "  • ICON CONTAINER TONES (both templates) — pick "
                "from a curated set: teal (#5EA8A0), coral / peach "
                "(#E8946B), mustard yellow (#D8A83F), muted purple "
                "(#8E6FB0), sage green (#7AA579), cream (#EFE4CE), "
                "dusty rose (#C88D8D). Use 4–6 of these in one "
                "composition so the icons pop against the "
                "background.\n"
                "  • BRAND COLOUR <BRAND_COLOR> appears as ONE among "
                "the several icon-container tones — it is NOT the "
                "dominant colour. If <BRAND_COLOR> clashes with the "
                "muted editorial palette (e.g. bright neon), use a "
                "desaturated / tinted version for one icon container "
                "so it still ties to the brand without breaking the "
                "aesthetic.\n"
                "  • Comparison table verdict colours: green (#4A8F4A) "
                "for ✓, muted red (#C25454) for ✗.\n"
            ),
            "typography": (
                "Bold editorial SANS-SERIF or SERIF for the headline "
                "(both templates). Weight 700–800. Portrait template "
                "usually uses bold sans (Inter Bold, Graphik Bold, "
                "SF Pro Display); landscape template usually uses "
                "bold serif (Lyon, TT Norms Serif, Georgia). Card "
                "labels + captions in a clean readable sans "
                "(Inter Regular, Graphik Regular). Small italic "
                "subtitles for the framework subtitle line."
            ),
            "mood": (
                "Educational, authoritative, teacherly. Feels like a "
                "page from a research report, a McKinsey slide, or a "
                "NotebookLM explainer. Info-dense but organized — "
                "the reader learns something. NOT promotional, NOT "
                "salesy, NOT flashy. Confident and pedagogical."
            ),
            "elements_to_include": (
                "Bold headline at top (with optional italic subtitle). "
                "Hand-drawn / sketch-style icons inside coloured "
                "containers (circles, ovals, rounded squares). "
                "Multi-section structure: portrait template = "
                "numbered-reasons row + comparison table + pillars "
                "diagram + optional warning callout; landscape "
                "template = left tiered-concept cards + right "
                "triangle/loop diagram. Muted editorial palette "
                "(teal + coral + mustard + purple + cream + one "
                "brand-colour accent). Optional small brand watermark "
                "(bottom-right or top-left). All content labels "
                "derived from the actual campaign brief."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book "
                "a Demo', 'Get Started', 'Explore', 'Read More' "
                "buttons or pills). This is educational content, not "
                "conversion.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS as "
                "separate elements. Card labels inside the framework "
                "are OK; standalone chips are not.\n"
                "  • LOREM-IPSUM / PLACEHOLDER / INVENTED framework "
                "vocab — every tier name, pillar name, comparison "
                "row, and callout must come from the actual campaign "
                "brief.\n"
                "  • Bright neon / cyberpunk palette — this style is "
                "editorial and muted.\n"
                "  • Flat corporate vector icons — icons must feel "
                "hand-drawn / editorial-sketch, not stock.\n"
                "  • Single dominant brand-colour palette — the "
                "brand colour is ONE among several editorial tones, "
                "not the whole composition.\n"
                "  • Large brand logo overlay — the logo is small or "
                "omitted (framework title carries brand identity).\n"
                "  • Photorealistic photography — this style is "
                "illustration + typography, not photo.\n"
            ),
        },
    },

    "software_product_feature_collage": {
        "label": "Feature Showcase Collage",
        "group": "software_product",
        "emoji": "✨",
        "when_to_use": (
            "Software / SaaS product feature showcase — a single "
            "product's multiple capabilities rendered as floating UI "
            "cards on a soft pastel gradient background, with a "
            "prominent central action pill. Best for 'here's "
            "everything this product does' posts, capability round-"
            "ups, product-page hero sections. Reference: Google "
            "Business Profile / Marketing Asset product marketing "
            "visuals."
        ),
        "prompt_hint": (
            "soft pastel gradient background (blue-purple, "
            "brand-tinted); multiple floating white UI cards showing "
            "different product features / use cases from the brief; "
            "one central prominent rounded pill showing the primary "
            "product action ('Connecting to [Brand]', 'Analyze my "
            "[thing]', etc.); rounded pill inputs and small chip "
            "buttons scattered; subtle shadows, glassy translucent "
            "feel; small user avatar or star rating element for "
            "authenticity; brand-adaptive palette; no CTA button, "
            "no bullets"
        ),
        "visual_dna": {
            "composition": (
                "FLOATING UI FEATURE COLLAGE. Reference: Google "
                "Business Profile / Marketing Asset marketing hero — "
                "soft pastel gradient background with multiple "
                "floating white UI cards representing different "
                "capabilities of a single product, and one prominent "
                "central action pill.\n\n"
                "BACKGROUND: soft pastel gradient in Google-marketing "
                "style. Base direction: light diagonal wash. Colour "
                "mix pulls from a brand-tinted pastel palette — start "
                "from very light warm cream / off-white at the "
                "top-left and blend into a soft brand-accent tint "
                "(usually a lightened version of <BRAND_COLOR>) "
                "toward the bottom-right, or vice versa. Never a "
                "solid block, never a dark background — always airy, "
                "light, atmospheric.\n\n"
                "FLOATING UI CARDS (scattered across the canvas):\n"
                "  • 4–7 white / off-white rounded cards positioned "
                "at varying depths and rotations (subtle, ≤5 "
                "degrees). Each card has a soft drop shadow and 12–"
                "20px corner radius.\n"
                "  • Each card represents a DIFFERENT feature / use "
                "case of the product, taken from the campaign brief. "
                "Examples: a review card, an analytics card, a "
                "notification card, an action-item card, a revenue "
                "card, a chart snippet, a user profile chip, a "
                "message input pill.\n"
                "  • Cards contain real UI elements: small labels, "
                "bold numbers, subtle icons, rounded buttons, mini "
                "charts. Content of these cards must come from the "
                "actual brief (real feature names, real numbers if "
                "provided, real product-surface labels).\n"
                "  • Cards may partially overlap or clip at the "
                "canvas edges to create depth.\n\n"
                "CENTRAL ACTION PILL (the hero element):\n"
                "  • One large, prominent rounded pill or capsule "
                "positioned near the visual center. Format: an "
                "action prompt line like 'Connecting to [Brand]', "
                "'Analyze [thing]', 'Draft [content]', 'Summarize "
                "my [source]'. Left icon (a small brand logo or "
                "kebab-menu ⋯), center text in medium-weight "
                "sans-serif near-black, optional right-side action "
                "icon (up-arrow, send, sparkle).\n"
                "  • This pill is BIGGER than any other card — it's "
                "the anchor that ties all floating features together.\n\n"
                "ACCENT ELEMENTS:\n"
                "  • Small pill BUTTONS (2–4 total) scattered near "
                "the cards: '+ Create a marketing asset', '+ "
                "Analyze themes', '+ Draft reply'. Rounded, white "
                "fill, subtle border, small icon on the left.\n"
                "  • Optional user profile avatar + star rating "
                "element (bottom-left area) if the brief mentions "
                "reviews / users / testimonials.\n"
                "  • Optional 2–3 tiny thumbnail-image chips in a "
                "corner suggesting product photos or asset "
                "library.\n\n"
                "BRAND LOGO: small brand mark top-left (~40px) OR "
                "baked into the central action pill as the "
                "left-icon. Never dominant — this style is "
                "type-and-UI-forward, not logo-forward.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Square 1:1 → central pill at ~50% width, cards "
                "arranged in 4 corners.\n"
                "  • Landscape 16:9 / 1.91:1 → central pill spans "
                "wider, more cards along the horizontal band.\n"
                "  • Portrait 4:5 / 9:16 → central pill stays "
                "central, cards stack vertically above and below.\n"
                "NO CTA BUTTON. NO decorative shapes overlaid on "
                "cards. NO body paragraphs. NO chip highlight rows.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE PASTEL — the gradient background "
                "uses a soft mix of the brand's primary colour "
                "<BRAND_COLOR> lightened into a pastel wash:\n"
                "  • Background gradient = white / cream (~#FEFCF8) "
                "at one corner blending to a very soft brand-tinted "
                "pastel (10–20% opacity of <BRAND_COLOR>) at the "
                "opposite corner. If <BRAND_COLOR> is very dark, "
                "use a lighter analogous tint (e.g. deep navy → "
                "soft cornflower blue).\n"
                "  • Floating cards = white / off-white (#FFFFFF, "
                "#FCFAF6) with faint blue-grey shadows.\n"
                "  • Text on cards = near-black (#111 / #1F2937) "
                "for labels, medium grey (#666 / #6B7280) for "
                "captions.\n"
                "  • ACCENT COLOURS inside cards (chart lines, "
                "percentage badges, stars, action icons): soft "
                "blue (#4285F4), soft green (#34A853 for positive "
                "numbers), yellow (#FBBC05 for stars), plus one "
                "accent of <BRAND_COLOR>. Google-style multi-colour "
                "restraint.\n"
                "  • Central action pill = pure white fill with "
                "near-black text, small brand-accent icon on the "
                "left.\n"
                "  • Logo renders in its native brand colours "
                "(untouched)."
            ),
            "typography": (
                "Modern geometric sans-serif (Google Sans, Inter, "
                "SF Pro Display). Regular / medium weight for card "
                "labels and pill text, semibold for numbers, bold "
                "for headings if present. No serif. Small size — "
                "text is intimate and product-native, not shouty."
            ),
            "mood": (
                "Airy, calm, feature-rich, human. Feels like the "
                "hero section of a modern Google product landing "
                "page — many capabilities visible at once, held "
                "together by one clear action. NOT flashy, NOT "
                "cyberpunk, NOT dark-mode. Bright, breathable, "
                "aspirational."
            ),
            "elements_to_include": (
                "Soft brand-tinted pastel gradient background (white "
                "→ light brand-accent). 4–7 floating white UI cards "
                "with subtle shadows, each showing a different "
                "feature from the campaign brief (reviews, "
                "analytics, notifications, revenue, chart snippets, "
                "action items). ONE prominent central rounded pill "
                "with an action prompt derived from the brief. 2–4 "
                "small pill buttons ('+ Create X', '+ Analyze Y') "
                "scattered near the cards. Optional user avatar + "
                "star rating element and 2–3 tiny product-photo "
                "thumbnail chips. Small brand logo top-left (~40px) "
                "or baked into the central pill."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS ('Learn More', 'Book a Demo', "
                "'Sign Up', 'Get Started', 'Try Free') — this style "
                "is a feature showcase, not conversion. The action "
                "pill is INFORMATIONAL, not clickable-conversion.\n"
                "  • Standalone PILL CHIPS / HIGHLIGHT CHIPS as "
                "separate elements. The pill buttons above are "
                "'action prompts' with icons, not highlight chips.\n"
                "  • BODY PARAGRAPHS, HEADLINE BLOCKS, dense text "
                "walls — this style is UI-forward, not typography-"
                "forward.\n"
                "  • Dark backgrounds — always airy pastel.\n"
                "  • Cyberpunk / neon glow / holographic tech.\n"
                "  • 3D rendered mascots or characters.\n"
                "  • Photorealistic photography — this style is "
                "vector UI illustration, not photo.\n"
                "  • Prominent large brand logo overlay — the logo "
                "is small (~40px top-left) or subtle inside the "
                "central pill.\n"
                "  • Lorem / placeholder / invented feature names — "
                "every card label comes from the actual brief.\n"
            ),
        },
    },

    "software_product_integration_showcase": {
        "label": "Integration Ecosystem Showcase",
        "group": "software_product",
        "emoji": "🔗",
        "when_to_use": (
            "Software / SaaS product integrations — showing how the "
            "product connects to other tools, platforms, or "
            "ecosystems. Multiple app UI cards + integration pills + "
            "cross-tool product mockups on a soft pastel gradient. "
            "Best for integration launches, partnership "
            "announcements, ecosystem posts, 'works with your "
            "stack' messaging. Reference: Google Classroom + Gemini "
            "Guided Learning integration marketing visuals."
        ),
        "prompt_hint": (
            "soft pastel gradient background (peach-blue Google-"
            "marketing palette); multiple floating cards from "
            "DIFFERENT product surfaces / apps (main product UI, "
            "integrated app UI, an @-mention input, a stat card, "
            "a chart card); integration pills showing app names / "
            "logos; small AI sparkle or brand mark top-left; "
            "brand-adaptive palette; no CTA button, no bullets, "
            "no dense text"
        ),
        "visual_dna": {
            "composition": (
                "INTEGRATION ECOSYSTEM COLLAGE. Reference: Google "
                "'Gemini Guided Learning + Classroom' marketing "
                "hero — multiple floating cards showing UI from "
                "DIFFERENT integrated products, connected together "
                "by placement and integration pills.\n\n"
                "BACKGROUND: soft pastel gradient (Google marketing "
                "style). Multi-tone — start from soft warm cream / "
                "peach at one corner, blend through pale blue in "
                "the middle, into a soft brand-accent tint at the "
                "opposite corner. Airy, atmospheric, never dark, "
                "never a solid block.\n\n"
                "FLOATING UI CARDS FROM MULTIPLE PRODUCT "
                "SURFACES:\n"
                "  • 4–6 white / off-white rounded cards at varying "
                "positions and subtle rotations (≤5 degrees). Each "
                "card shows a fragment of a REAL PRODUCT UI. Unlike "
                "the feature-showcase style, these cards represent "
                "DIFFERENT PRODUCTS / APPS that integrate together, "
                "as named in the campaign brief.\n"
                "  • Examples: the main product's chat surface, a "
                "third-party app's window ('Resources' panel, "
                "'Classroom' card, 'Slack' thread, 'HubSpot' "
                "contact, 'Notion' page), a stats card showing "
                "usage / performance, a mini chart card, an @-"
                "mention input pill.\n"
                "  • Each card should visually resemble the actual "
                "app it represents (window chrome, colour tint, "
                "icon) so viewers recognise the integration.\n"
                "  • Cards partially overlap or clip at canvas edges "
                "for depth.\n\n"
                "INTEGRATION PILLS (the connective tissue):\n"
                "  • 1–3 small horizontal pill capsules showing app "
                "names with their small icon on the left. Examples: "
                "'📚 Classroom', '@ Notion', '💬 Slack', '⚡ "
                "Salesforce'. These pills sit between or beside the "
                "product cards, visually 'linking' them.\n"
                "  • Optional @-mention input pill ('+ @[AppName]') "
                "showing the moment of adding an integration.\n\n"
                "BRAND SPARKLE / IDENTITY MARK:\n"
                "  • Optional multi-colour SPARKLE icon (Google-"
                "style 4-pointed star, or the brand's own "
                "identity mark if it has a distinctive glyph) "
                "placed in the top-left corner as the visual anchor "
                "of 'AI / smart product'. Rendered in gradient "
                "brand colours or the classic Google-multi-colour "
                "sparkle (blue + red + yellow + green).\n"
                "  • Alternatively, small brand logo top-left "
                "(~40px, untouched).\n\n"
                "OPTIONAL STAT / OUTCOME CARDS:\n"
                "  • 1–2 slightly larger cards showing outcome "
                "metrics from the brief (e.g. 'Student usage — "
                "shared with 34 students — 8 / 10 / 16', "
                "'Performance by topic — donut chart with 2 topic "
                "labels'). These give the ecosystem a 'so what' "
                "outcome anchor.\n\n"
                "BRAND LOGO: small top-left OR baked into the "
                "central sparkle. Never dominant.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Square 1:1 → cards in a rough 2x2 arrangement "
                "around the center, sparkle + logo top-left.\n"
                "  • Landscape 16:9 / 1.91:1 → cards spread "
                "horizontally in a band, more integration pills "
                "between them.\n"
                "  • Portrait 4:5 / 9:16 → cards stack vertically, "
                "sparkle stays top-left.\n"
                "NO CTA BUTTON. NO decorative shapes overlaid on "
                "cards. NO body paragraphs. NO promotional chip "
                "rows.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE PASTEL WITH GOOGLE-STYLE MULTI-"
                "TONE. Gradient background pulls from a warmer "
                "mix than the feature-showcase style:\n"
                "  • Background gradient = soft peach / cream "
                "(~#FDF3E8) at one corner, blending through pale "
                "sky blue (~#EAF2FE) in the middle, into a soft "
                "brand-accent tint at the opposite corner. If "
                "<BRAND_COLOR> is very dark, use a lightened "
                "analogous tint for the accent zone.\n"
                "  • Floating cards = white / off-white (#FFFFFF, "
                "#FDFCFA) with faint drop shadows.\n"
                "  • Integrated-app cards preserve their real "
                "brand tints (Slack purple header, Classroom "
                "green icon, Notion white minimalism) so viewers "
                "recognise them.\n"
                "  • Text on cards = near-black (#111 / #1F2937), "
                "grey (#6B7280) for captions.\n"
                "  • Google-style multi-colour accent set for the "
                "sparkle: blue (#4285F4) + red (#EA4335) + yellow "
                "(#FBBC05) + green (#34A853). If the brand has its "
                "own signature glyph, use its colours instead.\n"
                "  • Chart lines / stat highlights: blue (#4285F4) "
                "or brand accent for positive trends, red "
                "(#EA4335) for negative.\n"
                "  • Logo renders in its native brand colours "
                "(untouched)."
            ),
            "typography": (
                "Modern geometric sans-serif (Google Sans, Inter, "
                "SF Pro Display). Small readable sizes — this "
                "style is UI-forward, not typography-forward. "
                "Medium weight for labels, semibold for numbers "
                "and integration names, regular for captions."
            ),
            "mood": (
                "Airy, connected, ecosystem-forward. Feels like "
                "the hero of a Google Cloud / Workspace integration "
                "launch page — multiple products living together, "
                "connected by soft visual bridges. NOT flashy, "
                "NOT dark, NOT gimmicky."
            ),
            "elements_to_include": (
                "Soft peach-blue-brand pastel gradient background. "
                "4–6 floating white UI cards representing "
                "DIFFERENT products / apps from the campaign brief "
                "(each visually resembling the app it stands for). "
                "1–3 small integration pills with app names + "
                "icons acting as visual links. Optional @-mention "
                "input pill. Optional multi-colour sparkle / "
                "identity mark top-left. 1–2 outcome stat cards "
                "with real numbers from the brief (usage, "
                "performance, adoption). Small brand logo top-left "
                "(~40px) or baked into the sparkle."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS ('Learn More', 'Book a Demo', "
                "'Sign Up', 'Get Started') — this style is an "
                "ecosystem showcase, not conversion.\n"
                "  • Standalone HIGHLIGHT PILL CHIPS as separate "
                "elements. The integration pills above are "
                "'app-name links' not chip rows.\n"
                "  • BODY PARAGRAPHS, dense typography, headline "
                "blocks — this style is UI-forward.\n"
                "  • Dark backgrounds — always airy pastel.\n"
                "  • Cyberpunk / neon / holographic effects.\n"
                "  • 3D rendered mascots or characters.\n"
                "  • Photorealistic photography — vector UI "
                "illustration only.\n"
                "  • Prominent large brand logo overlay — logo is "
                "small or omitted; sparkle acts as identity.\n"
                "  • Lorem / placeholder / invented app names — "
                "every integration pill and card label comes from "
                "the actual brief (real products the brief "
                "mentions integrating with).\n"
                "  • Fake logo replicas of third-party apps (use "
                "SUGGESTIVE styling — colour, icon shape — not "
                "exact copyrighted logos).\n"
            ),
        },
    },

    # ── 🧑‍💼 Consulting & IT Services (PwC / Deloitte / BCG reference set) ──
    # Three thought-leadership styles distilled from PwC's report covers.
    # Palette is BRAND-ADAPTIVE — every colour derives from the business DNA
    # primary brand colour (<BRAND_COLOR>), not from a hardcoded peach.
    "consulting_thought_leadership": {
        "label": "Thought Leadership Hero",
        "group": "consulting_services",
        "emoji": "📰",
        "when_to_use": (
            "Consulting firms / IT services / advisory / research reports — "
            "editorial thought-leadership hero with a large serif headline "
            "on a brand-tinted gradient panel and a full-width contextual "
            "photograph below. Best for report launches, whitepapers, "
            "sector insights, POV pieces. NO CTA — the headline IS the "
            "message. Reference: PwC 'The era of AI-native infrastructure'."
        ),
        "prompt_hint": (
            "vertical thought-leadership hero — brand logo top-left, "
            "large bold editorial serif headline on a brand-tinted "
            "gradient panel (top half), a full-width real photograph "
            "below (bottom half) that CONTEXTUALLY matches the "
            "campaign topic (data engineers for AI, executives in a "
            "boardroom for governance, a factory floor for manufacturing, "
            "etc.). No CTA, no chips, no bullets"
        ),
        "visual_dna": {
            "composition": (
                "TOP-BOTTOM SPLIT HERO — the canvas is divided into two "
                "clean stacked halves: TOP HALF is the text zone (on a "
                "brand-tinted gradient panel), BOTTOM HALF is a "
                "full-width contextual photograph. Reference: PwC 'The "
                "era of AI-native infrastructure: how agentic AI will "
                "reinvent delivery'.\n\n"
                "TOP HALF (text zone, ~50% of canvas height):\n"
                "  • Brand logo TOP-LEFT (untouched, small ~40–60px). "
                "MANDATORY position.\n"
                "  • Background: a soft gradient tinted with the brand "
                "primary colour at top-left, fading to clean white at "
                "the right / bottom-right of this half. The gradient is "
                "SUBTLE — never a solid coloured block, always a soft "
                "pastel wash.\n"
                "  • Below the logo (with generous whitespace above), a "
                "LARGE bold editorial SERIF headline in near-black "
                "(#111 or #1A1A1A). Headline typically 8–14 words, may "
                "wrap 3–4 lines. Line-height tight (~1.1), letter-"
                "spacing slightly reduced. The headline is the ONLY "
                "text element in the top half.\n"
                "  • No subheading, no bullets, no chips, no CTA — the "
                "composition breathes.\n\n"
                "BOTTOM HALF (photo zone, ~50% of canvas height, "
                "full-width, bleeds to left / right / bottom edges):\n"
                "  • Full-width real photograph that CONTEXTUALLY "
                "matches the campaign topic. Examples: AI / tech topic "
                "→ data engineers at neon-lit monitors in an operations "
                "room; governance / risk → executives in a boardroom "
                "reviewing charts; manufacturing → workers on a factory "
                "floor; healthcare → clinicians in a hospital corridor; "
                "sustainability → engineers at a solar farm. The photo "
                "MUST reflect the actual subject of the campaign — no "
                "generic stock 'business handshake' filler.\n"
                "  • Lighting: cinematic, moody-to-natural depending on "
                "the topic. Photo has real depth, real people (when "
                "present), real environment.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Square 1:1 → equal top/bottom halves.\n"
                "  • Portrait 4:5 / 9:16 → text takes top 45%, photo "
                "takes bottom 55% (photo gets more breathing room).\n"
                "  • Landscape 16:9 / 1.91:1 → FLIP to left/right "
                "split instead of top/bottom (text on left half, photo "
                "on right half). Logo still top-left of the whole "
                "canvas.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE — do NOT hardcode peach or orange. "
                "Derive the palette from the business DNA's primary "
                "brand colour <BRAND_COLOR>:\n"
                "  • Gradient panel background = a very soft pastel "
                "wash of <BRAND_COLOR> (roughly 15–25% opacity) fading "
                "to clean white. Never a saturated solid block.\n"
                "  • Headline text = near-black (#111 or #1A1A1A) for "
                "maximum readability. Never coloured.\n"
                "  • Logo renders in its native brand colours "
                "(untouched from the provided logo file).\n"
                "  • The photograph brings its own natural colours — "
                "these are ADDITIONAL to the palette, not conflicting.\n"
                "Examples: PwC red-orange DNA → soft peach gradient. "
                "Deloitte green DNA → soft mint gradient. IBM blue DNA "
                "→ soft cornflower gradient. Deep-navy DNA → soft "
                "steel-grey gradient. If <BRAND_COLOR> is very dark, "
                "use a lightened tint (mix with 70–80% white) for the "
                "gradient — never a black wash."
            ),
            "typography": (
                "Bold editorial SERIF for the headline (feels like "
                "Financial Times, Bloomberg Businessweek, PwC reports "
                "— TT Norms Serif, Sohne, Lyon, or similar). Weight "
                "700–900. Line-height ~1.1. No sans-serif in the "
                "headline. If the brief wants a sans-serif variant, "
                "use it only when the brand DNA explicitly calls for a "
                "modern-sans identity (Google, Meta, tech-native "
                "brands) — otherwise commit to the serif."
            ),
            "mood": (
                "Editorial, authoritative, calm, thought-provoking. "
                "Feels like a magazine cover or an executive research "
                "report. NOT flashy, NOT promotional, NOT salesy. The "
                "reader should feel this is a serious point of view "
                "worth their time."
            ),
            "elements_to_include": (
                "Brand logo TOP-LEFT (mandatory, untouched). ONE bold "
                "editorial serif HEADLINE (8–14 words, may wrap 3–4 "
                "lines, near-black text on brand-tinted gradient "
                "background). ONE full-width contextual PHOTOGRAPH in "
                "the opposite half of the canvas, subject matching "
                "the actual campaign topic (real people, real setting, "
                "cinematic lighting)."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book a "
                "Demo', 'Get Started', 'Choose', 'Explore', 'Read More' "
                "buttons or pills). This is thought-leadership, not "
                "conversion — the report headline IS the entire "
                "message.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS of any "
                "kind (no 'Advanced Analytics', 'Predictive Modeling' "
                "style pill labels). NEVER emit chips even if a "
                "text-density rule elsewhere suggests them.\n"
                "  • SUBHEADINGS, BODY PARAGRAPHS, BULLETS, ICON-"
                "LABELED FEATURES (the headline is the ONLY text on "
                "the image).\n"
                "Also avoid: hardcoded peach or orange palette when "
                "the brand DNA has a different primary colour; generic "
                "stock photography (business handshake, office "
                "high-five, laptop-on-desk) — the photo MUST match "
                "the campaign subject; sans-serif headlines when the "
                "brand identity supports serif; solid coloured "
                "background blocks (always a soft gradient wash); "
                "logo anywhere except top-left."
            ),
        },
    },

    "consulting_report_cover": {
        "label": "Report Cover with Accent Shapes",
        "group": "consulting_services",
        "emoji": "📊",
        "when_to_use": (
            "Consulting / IT services report covers — heading + short "
            "subheading + one-paragraph deck with a real photograph of "
            "people/scene on the opposite side. Clean editorial split, "
            "no decorative shapes. Best for annual reports, barometers, "
            "industry surveys, POV launches. Reference: PwC 'Two "
            "futures for jobs in an AI era — 2026 Global AI Jobs "
            "Barometer'."
        ),
        "prompt_hint": (
            "square report cover — brand logo top-left, bold serif "
            "heading + smaller subheading + one-paragraph deck on the "
            "left half (clean white bg), real professional photograph "
            "on the right half; clean 50/50 editorial split, no "
            "decorative shapes, no CTA"
        ),
        "visual_dna": {
            "composition": (
                "SPLIT REPORT COVER — text zone on the left ~50%, "
                "photo zone on the right ~50%. Clean editorial split. "
                "NO decorative geometric shapes, NO parallelograms, NO "
                "arrow-bars, NO brand-coloured accent shapes overlaid "
                "on the photo. Reference: PwC 'Two futures for jobs in "
                "an AI era' — but WITHOUT the geometric accent shapes "
                "shown in that reference.\n\n"
                "LEFT ZONE (text, ~50% of canvas width):\n"
                "  • Brand logo TOP-LEFT (untouched). MANDATORY.\n"
                "  • Background: clean white or a very soft "
                "brand-tinted wash (much lighter than the "
                "thought-leadership style). Feels like a bright "
                "boardroom presentation page.\n"
                "  • Below the logo (with generous whitespace), stack:\n"
                "     1. Bold editorial SERIF HEADING (5–10 words, may "
                "wrap 2–3 lines, near-black).\n"
                "     2. Smaller SUBHEADING or REPORT SUBTITLE (single "
                "line, ~10–15 words, near-black or dark grey, sits "
                "directly under the heading with slightly reduced "
                "weight).\n"
                "     3. Optional ONE short PARAGRAPH (2–3 sentences, "
                "~20–35 words) sitting below the subheading in regular-"
                "weight body text.\n"
                "  • No bullets, no chips, no icon-labeled features, "
                "no CTA — this is a report cover, not a landing "
                "page.\n\n"
                "RIGHT ZONE (photo only, ~50% of canvas):\n"
                "  • Real photograph of 1–2 professionals in a bright, "
                "authentic setting — office by a window, boardroom, "
                "collaborative workspace, industry-relevant "
                "environment. The subjects and setting MUST match the "
                "campaign topic. Natural daylight, editorial quality.\n"
                "  • The photograph fills the right half cleanly, "
                "bleeding to the top / right / bottom edges. A clean "
                "vertical seam between the text panel and the "
                "photograph.\n"
                "  • NO overlaid geometric shapes, NO parallelograms, "
                "NO arrow-bars, NO brand-coloured accent bars on top "
                "of or behind the photo. Just the photograph on its "
                "own — pure editorial photo, not a decorated one.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • ALL aspect ratios (square 1:1, landscape 16:9 / "
                "1.91:1, portrait 4:5 / 9:16) → strictly 50/50 "
                "left-right split. Never stack vertically. On portrait "
                "canvases the layout still holds — text zone becomes a "
                "tall narrow left column, photo fills the tall narrow "
                "right column.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE — palette derives from the business "
                "DNA's primary brand colour <BRAND_COLOR>:\n"
                "  • Text background = clean white with an optional "
                "very soft brand-tinted wash (~5–15% opacity of "
                "<BRAND_COLOR>).\n"
                "  • Heading + subheading + body = near-black "
                "(#111 / #1A1A1A) for maximum readability.\n"
                "  • The photograph brings its own natural tones. "
                "Composition should feel bright and daylit — no moody "
                "shadows.\n"
                "  • Logo renders in its native brand colours "
                "(untouched). The logo is the ONLY strong colour "
                "element on the layout — no additional decorative "
                "brand-coloured shapes."
            ),
            "typography": (
                "Bold editorial SERIF for the heading (FT / Bloomberg / "
                "PwC report aesthetic — TT Norms Serif, Sohne, Lyon). "
                "Weight 700–800 for the heading, weight 500–600 for the "
                "subheading (same serif family or a matched sans "
                "secondary). Body paragraph in a clean readable sans-"
                "serif or the same serif at regular weight. Confident "
                "hierarchy: heading dominates, subheading supports, "
                "body sits quietly."
            ),
            "mood": (
                "Editorial, professional, forward-looking, "
                "authoritative. Feels like a printed report cover or a "
                "keynote opener. NOT flashy, NOT gimmicky. The "
                "typography and the photograph carry the entire "
                "composition — restrained and confident."
            ),
            "elements_to_include": (
                "Brand logo TOP-LEFT (mandatory, untouched). Bold serif "
                "HEADING (5–10 words, 2–3 lines). Smaller SUBHEADING "
                "(single line, 10–15 words). Optional ONE short body "
                "PARAGRAPH (2–3 sentences). Real photograph of 1–2 "
                "professionals in a bright, authentic, topic-matching "
                "setting on the opposite half of the canvas — clean, "
                "no overlaid shapes."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • GEOMETRIC ACCENT SHAPES of any kind — NO "
                "parallelograms, NO arrow-bars, NO diagonal bars, NO "
                "brand-coloured shapes layered on / behind / beside "
                "the photograph. The photo stands ALONE on the right "
                "half. This is the #1 forbidden mistake for this "
                "style.\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book a "
                "Demo', 'Get Started', 'Choose', 'Explore', 'Read "
                "More' buttons or pills). This is a report cover, not "
                "a landing page.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS "
                "('Advanced Analytics', 'Predictive Modeling', etc.). "
                "NEVER emit chips even if a text-density rule "
                "elsewhere suggests them.\n"
                "  • ICON-LABELED FEATURES, BULLET LISTS, ICON ROWS "
                "(heading + subheading + one short body paragraph is "
                "the entire text ceiling).\n"
                "Also avoid: hardcoded orange / peach palette when the "
                "brand DNA has a different primary colour; generic "
                "stock photography — the subjects and setting must "
                "match the campaign topic; cyberpunk / neon / "
                "dark-mode aesthetics — this style is bright and "
                "editorial; sans-serif heading when the brand identity "
                "supports serif; logo anywhere except top-left."
            ),
        },
    },

    "consulting_context_photo": {
        "label": "Contextual Insight Panel",
        "group": "consulting_services",
        "emoji": "🌱",
        "when_to_use": (
            "Consulting / advisory / policy briefs where the topic has a "
            "strong VISUAL context (food security → farmer in a "
            "greenhouse, healthcare → clinician in a hospital, energy → "
            "engineer at a plant, education → students in a classroom). "
            "Tall serif heading + short subheading on a brand-tinted "
            "gradient left panel, contextual photograph on the right. "
            "Reference: PwC 'Food security is becoming more critical'."
        ),
        "prompt_hint": (
            "square insight-panel cover — brand logo top-left, tall bold "
            "serif heading and short subheading on a brand-tinted "
            "gradient LEFT HALF (50%), full-height real photograph of "
            "a person actively working in the topic's real environment "
            "on the RIGHT HALF (50%); no CTA, no bullets"
        ),
        "visual_dna": {
            "composition": (
                "LEFT PANEL + FULL-HEIGHT PHOTO — STRICT 50/50 vertical "
                "split. The LEFT HALF (exactly ~50% of canvas width) "
                "holds the text on a brand-tinted gradient; the RIGHT "
                "HALF (exactly ~50%) is a full-height contextual "
                "photograph of a real person working in the topic's "
                "real environment. Reference: PwC 'Food security is "
                "becoming more critical' — a woman in green work "
                "clothes tending to plants in a greenhouse (rendered "
                "here in a 50/50 layout).\n\n"
                "LEFT PANEL (text, exactly ~50% width, full height):\n"
                "  • Brand logo TOP-LEFT (untouched). MANDATORY.\n"
                "  • Background: soft brand-tinted gradient fading from "
                "the brand primary colour at top-left toward white at "
                "bottom-right (subtle, pastel).\n"
                "  • Below the logo, with generous whitespace above, "
                "a TALL bold editorial SERIF HEADING. This heading is "
                "designed to STACK VERTICALLY — 3–6 short lines, each "
                "1–3 words per line, forming a strong vertical column "
                "of text. Example: 'Food / security / is / becoming / "
                "more / critical'. Line-height slightly loose (~1.15).\n"
                "  • Below the heading, a smaller SUBHEADING of 1 "
                "sentence (10–20 words) in regular-weight text.\n"
                "  • No body paragraph, no bullets, no chips, no CTA — "
                "heading + subheading is the entire text load.\n\n"
                "RIGHT PHOTO ZONE (exactly ~50% width, full canvas "
                "height):\n"
                "  • Real photograph of a REAL PERSON actively WORKING "
                "in the topic's actual environment. Examples: food "
                "security → farmer / greenhouse worker tending crops; "
                "healthcare → clinician examining patient / lab "
                "technician at microscope; energy → engineer inspecting "
                "solar panels or wind turbines; education → teacher "
                "with students in a classroom; construction → project "
                "manager on-site with plans; agriculture → farmer in "
                "the field.\n"
                "  • The person must be ACTIVELY DOING something "
                "relevant, not just posing. Natural daylight (or "
                "environment-authentic lighting), real setting, real "
                "props / tools of the trade.\n"
                "  • The photo bleeds to the right, top, and bottom "
                "edges of the canvas. The seam between the gradient "
                "panel and the photo is clean and vertical.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • ALL aspect ratios (square 1:1, landscape 16:9 / "
                "1.91:1, portrait 4:5 / 9:16) → strict 50/50 left-"
                "right split. Never stack vertically. On portrait "
                "canvases the split still holds — text panel becomes "
                "a tall narrow left half, photo fills the tall narrow "
                "right half.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE — palette derives from the business "
                "DNA's primary brand colour <BRAND_COLOR>:\n"
                "  • Left panel gradient = soft wash of <BRAND_COLOR> "
                "(~20–30% opacity at top-left) fading to clean white "
                "at bottom-right. Never a solid coloured block.\n"
                "  • Heading + subheading = near-black (#111 / #1A1A1A) "
                "for maximum readability. Never coloured.\n"
                "  • Logo renders in its native brand colours "
                "(untouched).\n"
                "  • The photograph brings its own natural, "
                "environment-authentic colours (green for agriculture, "
                "blue-white for healthcare, orange-red for industrial, "
                "etc.) — these ADD to the palette without conflicting.\n"
                "Examples: PwC red-orange DNA → soft peach gradient. "
                "Deloitte green DNA → soft mint gradient. IBM blue DNA "
                "→ soft cornflower gradient. If <BRAND_COLOR> is very "
                "dark, lighten it to a pastel tint for the gradient — "
                "never a dark wash on the left panel."
            ),
            "typography": (
                "Bold editorial SERIF for the heading — the SAME "
                "aesthetic as PwC / Financial Times / Bloomberg "
                "Businessweek (TT Norms Serif, Sohne, Lyon or "
                "similar). Weight 700–800. Line-height ~1.15 (slightly "
                "loose to accommodate the stacked-vertical layout). "
                "Sub-heading in a matched serif at regular weight OR a "
                "clean sans at semibold. Commit to one serif system "
                "and stay consistent."
            ),
            "mood": (
                "Editorial, grounded, human, purposeful. The "
                "photograph does the emotional heavy lifting — a real "
                "person doing real work in a real place. The text "
                "panel provides restrained editorial framing. NOT "
                "flashy, NOT corporate-stock. Feels like a serious "
                "policy brief or research cover that respects the "
                "reader's intelligence."
            ),
            "elements_to_include": (
                "Brand logo TOP-LEFT (mandatory, untouched). TALL "
                "bold serif HEADING stacked vertically over 3–6 lines "
                "(1–3 words per line). Smaller single-line SUBHEADING "
                "(10–20 words) below the heading. Real photograph of "
                "ONE real person actively working in the topic's real "
                "environment on the right ~65% of the canvas (bleeds "
                "to top / right / bottom edges)."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book a "
                "Demo', 'Get Started', 'Choose', 'Explore', 'Read More' "
                "buttons or pills). This is thought-leadership, not "
                "conversion.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS "
                "('Advanced Analytics', 'Predictive Modeling', etc.). "
                "NEVER emit chips even if a text-density rule "
                "elsewhere suggests them.\n"
                "  • BODY PARAGRAPHS, BULLETS, ICON-LABELED FEATURES "
                "(heading + one-line subheading is the entire text on "
                "the image).\n"
                "Also avoid: hardcoded peach / orange palette when the "
                "brand DNA has a different primary colour; generic "
                "stock photography of business people in suits — the "
                "subject MUST be actively working in the topic's real "
                "environment; photos where the person is just posing "
                "or smiling at the camera (they should be engaged in "
                "the activity, not the viewer); horizontal heading "
                "layout — the heading MUST stack vertically to match "
                "the tall panel design; solid coloured background "
                "blocks (always a soft gradient wash); logo anywhere "
                "except top-left."
            ),
        },
    },

    "consulting_bold_photo_headline": {
        "label": "Bold Headline Over Photo",
        "group": "consulting_services",
        "emoji": "🌆",
        "when_to_use": (
            "Consulting / advisory / capability announcements — a "
            "cinematic full-canvas photograph as background with a "
            "large bold headline overlaid in white, and 1–2 KEY WORDS "
            "highlighted with a rounded rectangle in the brand accent "
            "colour. Logo top-RIGHT. Reference: Accenture 'Powering "
            "talent with the capability to perform, and the confidence "
            "to innovate'."
        ),
        "prompt_hint": (
            "square hero — full-canvas cinematic photograph background "
            "(blurred motion of professionals walking, cityscape, "
            "workspace, or thematic scene matching the campaign topic), "
            "large bold white sans-serif headline overlaid in the "
            "center-left with 1–2 key words highlighted using rounded "
            "rectangle backgrounds in the brand accent colour, thin "
            "vertical line accent running down the left edge, brand "
            "logo TOP-RIGHT; no CTA, no bullets, no chips"
        ),
        "visual_dna": {
            "composition": (
                "FULL-CANVAS PHOTO + BOLD HEADLINE OVERLAY. Reference: "
                "Accenture 'Powering talent with the capability to "
                "perform, and the confidence to innovate' — a moody "
                "cinematic photograph of blurred motion (people walking "
                "on a tiled floor from above) with a large bold white "
                "headline overlaid center-left and 2 words highlighted "
                "in brand-purple rounded rectangles.\n\n"
                "BACKGROUND (fills entire canvas):\n"
                "  • Real cinematic PHOTOGRAPH matching the campaign "
                "topic. Examples: talent / capability → blurred motion "
                "of professionals walking through a modern office or "
                "city plaza; innovation → engineers in a lab; growth "
                "→ a city skyline at dawn; sustainability → wind "
                "turbines against a moody sky. The photo has real "
                "depth, real environment, real subjects.\n"
                "  • Photo tone: DARK, MOODY, CINEMATIC. Enough dark "
                "area in the composition for white text to sit legibly "
                "overlaid. Motion blur is OK when the subject is "
                "movement.\n"
                "  • NO overlay tint, NO gradient wash across the "
                "whole photo — just the natural photograph.\n\n"
                "OVERLAID ELEMENTS:\n"
                "  • Brand logo TOP-RIGHT (untouched, ~40–60px). "
                "MANDATORY position for this style (NOT top-left).\n"
                "  • Thin vertical LINE (1–2px, white or brand accent) "
                "running down the left edge of the canvas from "
                "approximately 25% to 75% of the height, acting as a "
                "vertical framing accent (matches the reference).\n"
                "  • Large bold sans-serif HEADLINE in white, "
                "positioned CENTER-LEFT of the canvas (aligned to the "
                "vertical line). Headline typically 10–18 words, may "
                "wrap 3–5 lines. Line-height comfortable (~1.15). "
                "Weight 700–900.\n"
                "  • 1–2 KEY WORDS within the headline are highlighted "
                "with a ROUNDED RECTANGLE BACKGROUND in the brand "
                "accent colour <BRAND_COLOR>. The highlighted word "
                "text stays near-black or dark on the coloured "
                "rectangle for contrast. The rectangles are subtle — "
                "tight padding around the word, ~6–10px corner radius. "
                "Pick words the brief actually emphasises (verbs / "
                "outcomes / benefits like 'perform', 'innovate', "
                "'grow', 'transform', 'accelerate').\n"
                "  • NO subheading, NO bullets, NO chips, NO CTA — "
                "the headline is the entire message.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • Square 1:1 → headline occupies left ~65%, right "
                "~35% is photographic breathing room where the logo "
                "sits top-right.\n"
                "  • Portrait 4:5 / 9:16 → headline still center-left, "
                "may wrap more lines, vertical line spans a similar "
                "range.\n"
                "  • Landscape 16:9 / 1.91:1 → headline stays "
                "center-left occupying about 50% of the width, logo "
                "top-right, vertical accent line still on the left "
                "edge.\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE — accent colour derives from the "
                "business DNA's primary brand colour <BRAND_COLOR>:\n"
                "  • Photograph brings the dominant tonal palette — "
                "typically dark charcoal, black, deep blues, or moody "
                "greys. This is a DARK style by design so the white "
                "headline reads clearly.\n"
                "  • Headline text = pure white (#FFFFFF) or off-white "
                "(#F5F5F5).\n"
                "  • Key word highlight backgrounds = solid "
                "<BRAND_COLOR> rounded rectangles. If <BRAND_COLOR> is "
                "very dark, use a lightened pastel tint so the white "
                "text on top still contrasts; if <BRAND_COLOR> is "
                "bright (Accenture purple #A100FF, Deloitte green, "
                "IBM blue), use it as-is.\n"
                "  • Vertical line = white or brand accent, thin.\n"
                "  • Logo renders in its native brand colours "
                "(untouched)."
            ),
            "typography": (
                "Bold modern SANS-SERIF for the headline (Accenture "
                "Sans, Graphik Bold, Inter Bold, or similar). Weight "
                "700–900. Slightly tight tracking. Optional italic "
                "for a single emphasised word. NEVER serif for this "
                "style (the reference uses a bold sans). Confident, "
                "cinematic, editorial."
            ),
            "mood": (
                "Cinematic, aspirational, confident, human. The "
                "photograph delivers atmosphere and emotion; the "
                "headline delivers the message. Feels like an "
                "Accenture / Deloitte capability film still, an "
                "opening slide of a keynote, or an editorial magazine "
                "spread. NOT flashy, NOT gimmicky — restrained "
                "cinematic weight."
            ),
            "elements_to_include": (
                "Full-canvas cinematic REAL PHOTOGRAPH matching the "
                "campaign topic (dark, moody, room for text overlay). "
                "Brand logo TOP-RIGHT (mandatory, untouched). Thin "
                "vertical LINE accent running down the left edge. "
                "Large bold white sans-serif HEADLINE overlaid "
                "center-left (10–18 words, may wrap 3–5 lines). 1–2 "
                "KEY WORDS within the headline highlighted with a "
                "ROUNDED RECTANGLE background in the brand accent "
                "colour. Nothing else."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book "
                "a Demo', 'Get Started', 'Explore', 'Read More' "
                "buttons or pills). This is a capability / "
                "thought-leadership hero, not conversion.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS listed "
                "as separate elements. The ONLY chip-like element "
                "allowed is the rounded-rect KEY WORD HIGHLIGHT "
                "inside the headline itself.\n"
                "  • SUBHEADINGS, BODY PARAGRAPHS, BULLETS, ICON-"
                "LABELED FEATURES (the headline is the ONLY text on "
                "the image).\n"
                "  • Generic stock photography that doesn't match the "
                "campaign subject (business handshake, office "
                "high-five, laptop-on-desk clichés).\n"
                "  • Overlay gradients, colour washes, or filters over "
                "the whole photograph — the photo stays natural.\n"
                "  • Logo anywhere except TOP-RIGHT (this style flips "
                "the logo position — top-right, not top-left).\n"
                "  • Serif typography for the headline — this style "
                "uses BOLD SANS-SERIF only.\n"
                "  • More than 2 highlighted key words — 1 or 2 max.\n"
            ),
        },
    },

    "consulting_type_announcement": {
        "label": "Type-Only Announcement",
        "group": "consulting_services",
        "emoji": "🔤",
        "when_to_use": (
            "Consulting / IT services / product launches — pure "
            "typographic announcement on a clean white canvas with a "
            "small opener line and a HUGE headline where 1 key word is "
            "in the brand accent colour. Best for product / capability "
            "launches, partnership announcements, brand milestones. "
            "Reference: Accenture 'Introducing Accenture Edge'."
        ),
        "prompt_hint": (
            "square typographic announcement — pure white canvas, "
            "centered composition, small opener line at top-center "
            "('Introducing', 'Announcing', 'Presenting') then a HUGE "
            "bold sans-serif headline centered with 1 key word rendered "
            "in the brand accent colour, extreme whitespace, no photo, "
            "no CTA, no bullets"
        ),
        "visual_dna": {
            "composition": (
                "PURE TYPOGRAPHIC ANNOUNCEMENT. Reference: Accenture "
                "'Introducing Accenture Edge' — clean white canvas, "
                "small 'Introducing' at top-center, HUGE 'Accenture "
                "Edge' below where 'Edge' is in Accenture purple.\n\n"
                "CANVAS: pure white (#FFFFFF) — no photograph, no "
                "gradient, no texture, no decorative shapes. Extreme "
                "whitespace is the design.\n\n"
                "LAYOUT (all elements center-aligned, vertically "
                "centered around the canvas midpoint):\n"
                "  1. Optional small OPENER LINE at the top-center of "
                "the composition (about 35–40% down from the top). "
                "Words like 'Introducing', 'Announcing', 'Presenting', "
                "'Now Live:', 'Meet'. Regular-weight sans-serif, "
                "near-black, ~24–36pt.\n"
                "  2. HUGE HEADLINE directly below the opener with "
                "tight vertical spacing. This is the hero element — "
                "80–140pt bold sans-serif, centered, near-black. "
                "Typically 2–4 words. One (or occasionally two) of "
                "those words is rendered in the brand accent colour "
                "<BRAND_COLOR> as the visual hook. Example: "
                "'Introducing / Accenture [Edge]' where [Edge] is "
                "purple.\n"
                "  3. Optional tiny SUBTITLE (single line, ~14–20pt, "
                "grey #666) below the headline with generous "
                "whitespace above. Only include when the brief has a "
                "meaningful 3–8 word tagline to add. Skip otherwise.\n"
                "  4. Optional brand LOGO at bottom-center of the "
                "canvas (small, ~32–48px), or brand logo TOP-LEFT if "
                "the brand identity requires attribution. When the "
                "brand name is already IN the headline (like 'Accenture "
                "Edge'), the logo can be omitted — the wordmark IS "
                "the identity.\n\n"
                "That's the entire composition. Nothing else on the "
                "canvas.\n\n"
                "ASPECT RATIO NOTES:\n"
                "  • ALL aspect ratios (square 1:1, landscape 16:9 / "
                "1.91:1, portrait 4:5 / 9:16) → same centered "
                "typographic composition. Adjust headline size to fill "
                "the available width comfortably (bigger on square, "
                "wider spread on landscape, tighter wrap on "
                "portrait).\n"
            ),
            "palette": (
                "BRAND-ADAPTIVE — accent colour derives from the "
                "business DNA's primary brand colour <BRAND_COLOR>:\n"
                "  • Canvas = pure white (#FFFFFF). No wash, no tint.\n"
                "  • Opener line + main headline body = near-black "
                "(#111 / #1A1A1A).\n"
                "  • ONE key word within the headline = solid "
                "<BRAND_COLOR>. This is the ONLY colour element on "
                "the canvas besides the logo. If <BRAND_COLOR> is "
                "very light or would compete with black, deepen it "
                "slightly for readability.\n"
                "  • Optional subtitle = medium grey (#666).\n"
                "  • Logo (if included) renders in its native brand "
                "colours (untouched)."
            ),
            "typography": (
                "Modern bold SANS-SERIF for the headline (Accenture "
                "Sans, Graphik Bold, Inter Bold, SF Pro Display Bold, "
                "or similar). Weight 700–900 for the headline. "
                "Regular-weight version of the same family for the "
                "opener line and subtitle. Tight kerning on the huge "
                "headline. NEVER serif for this style — Accenture-"
                "style bold sans is the whole aesthetic."
            ),
            "mood": (
                "Bold, confident, ceremonial. Feels like a keynote "
                "'reveal' slide, a launch page, or an Apple-style "
                "product announcement. Extreme minimalism — the design "
                "is the RESTRAINT. NOT flashy, NOT decorative — the "
                "brand-accent word carries the entire visual impact."
            ),
            "elements_to_include": (
                "Pure white canvas. Optional small OPENER LINE at "
                "top-center ('Introducing', 'Announcing', "
                "'Presenting', etc.). HUGE bold sans-serif HEADLINE "
                "centered on the canvas (2–4 words, 80–140pt). ONE "
                "key word within the headline rendered in the brand "
                "accent colour <BRAND_COLOR>. Optional tiny subtitle "
                "line below (3–8 words). Optional brand logo at "
                "bottom-center OR top-left (or omit if the brand "
                "name is already in the headline)."
            ),
            "elements_to_avoid": (
                "ABSOLUTELY FORBIDDEN — DO NOT render any of these:\n"
                "  • ANY PHOTOGRAPH — this is a pure type-only "
                "announcement, no images, no illustrations, no "
                "decorative graphics.\n"
                "  • DECORATIVE SHAPES — no parallelograms, no arrow-"
                "bars, no geometric accents, no dots or lines, no "
                "sparkles.\n"
                "  • CTA BUTTONS of any kind (no 'Learn More', 'Book "
                "a Demo', 'Explore', 'Read More' buttons or pills). "
                "This is an announcement, not a conversion page.\n"
                "  • PILL CHIPS / TAG CHIPS / HIGHLIGHT CHIPS of any "
                "kind. The brand-accent word is inline styling, NOT a "
                "chip.\n"
                "  • BULLETS, ICON-LABELED FEATURES, LONG BODY "
                "PARAGRAPHS. The headline is the message.\n"
                "  • Backgrounds other than pure white (no gradients, "
                "no washes, no textures, no tinted washes).\n"
                "  • Serif typography — this style uses BOLD SANS-"
                "SERIF only.\n"
                "  • More than one key word coloured — one word max "
                "in the brand accent.\n"
            ),
        },
    },

    "edtech_promo": {
        "label": "EdTech Promo Poster",
        "group": "education",
        "emoji": "🎓",
        "when_to_use": "Courses, bootcamps, workshops, career programs, cohort launches, training offers with dates + pricing + CTA",
        "prompt_hint": (
            "vertical editorial promotional flyer for an education / training offer, "
            "clean purple-to-magenta gradient background OR clean off-white background "
            "with purple accents (NOT a dark cyberpunk / holographic tech scene), a real "
            "smiling learner or professional photo confined to ONE side of the layout, "
            "big bold sans-serif display headline with ONE brush-script accent word, "
            "flat rounded icon-and-label feature cards, a large clean pill callout with "
            "the price / date / duration in oversized numerals, a bright rounded CTA "
            "button, phone-number contact strip at the bottom, brand logo top-left"
        ),
        "visual_dna": {
            "composition": "Vertical portrait poster with CLEAR STRUCTURAL SECTIONS separated by clean whitespace — NOT a single busy scene. Top-to-bottom: (1) brand logo TOP-LEFT + optional small pill badge TOP-RIGHT ('FUTURE-READY', 'LIMITED SEATS'). (2) The upper half is split LEFT / RIGHT: on the left, a big multi-line bold display headline with ONE word rendered as a flowing hand-lettered brush-script accent, plus a 2-3 line supporting subhead below it; on the right, a photo of ONE real smiling learner / professional confined to its OWN clean area (bordered rounded rectangle, circular halo, or clean cutout on the gradient) — the person does NOT overlap the headline text and does NOT bleed into every corner. (3) A tidy 2x2 grid OR one horizontal row of 3-5 flat rounded feature cards, each card containing a simple line icon on the LEFT and a 1-2 word label on the RIGHT — cards have soft rounded corners, subtle borders, minimal glow. (4) A prominent large rounded-rectangle pill containing the hero stat (price like '₹30,000', duration like '2-MONTH INTENSIVE', salary like '20-45 LPA Package', or date like '1 June 2026') in oversized bold numerals. (5) A bright rounded CTA button ('ENROLL NOW', 'REGISTER TODAY', 'SECURE YOUR SPOT') with a small arrow icon. (6) Bottom contact strip: phone-icon pills with the phone numbers for each region, on one clean row. Whitespace between every section is generous — this is an editorial promotional flyer, not a busy tech collage.",
            "palette": "TWO acceptable options, pick ONE per image and commit to it. OPTION A (light): clean off-white or very pale lavender background, deep purple + magenta as the primary accent, small yellow or cyan pop for the CTA / hero stat. OPTION B (dark): smooth deep-purple to magenta gradient background (NOT navy-black, NOT cyberpunk), white typography, bright pink / cyan accents, one yellow pop for the CTA. In BOTH options the background is CALM and CLEAN — not filled with holographic tech overlays. Brand colour features as a highlight on the hero stat pill and the CTA.",
            "typography": "Heavy extra-bold condensed sans-serif for the display headline, in Title Case or ALL CAPS. ONE word inside the headline rendered as a flowing hand-lettered brush-script accent in a contrasting hue (magenta, hot pink, or yellow) with an underline swash beneath it — this is the SIGNATURE move. Clean modern sans-serif for feature-card labels, subheads, pill callout numerals, and contact strip. Oversized bold numerals inside the pill callout. All typography sits on a clean background, never fights a busy overlay.",
            "mood": "Aspirational promotional flyer energy — like a printed course brochure or a paid-social campaign hero. Bold, confident, promotional, high-contrast, unmissable, but STRUCTURED and READABLE. NOT cyberpunk, NOT gaming poster, NOT holographic sci-fi.",
            "elements_to_include": "ONE real photo of a smiling learner / young professional confined to its own bordered / circular / rounded-cutout zone on one side; a big bold headline block with a brush-script accent word; a 2x2 or 1x3 grid of flat rounded feature cards with icon-left + label-right; a single large pill callout with the hero stat; a bright rounded CTA button (with optional arrow); a bottom contact strip with phone-icon pills and phone numbers; brand logo top-left; optional small badge chip top-right; VERY subtle abstract graphic accents (dots, small brand shapes) at low opacity in empty background areas — nothing that competes with the content.",
            "elements_to_avoid": "Dark cyberpunk backgrounds; heavy holographic tech overlays; circuit-grid patterns filling the whole background; floating code snippets, floating dashboards, floating charts, floating graphs, brain diagrams, or AI network visualisations layered behind the person; the person photo integrated into or fused with tech elements; neon-glow outlines on every feature card; multiple competing glow layers; a 'gaming poster' or 'sci-fi movie poster' vibe; chromatic aberration on edges; scan-lines; multiple people; product mockups inside device frames; watercolour or hand-drawn textures; cartoon 3D characters; tiny typography; low-contrast layouts.",
        },
    },
}


def _text_density_rules() -> str:
    """The #1 image-quality rule — applied to EVERY style (Auto too).

    Root cause of the "image is too crowded" complaint: gpt-image-2 renders
    everything the Art Director asks for. When the brief has 5 features or
    a paragraph of copy, the model draws it all onto the canvas verbatim.
    This block forces the Art Director to DISTILL: 1 heading, at most 1
    one-line subheading, at most 3 short highlight chips. Long-form copy
    stays in the post caption, not on the image.
    """
    return (
        "\n════════════════════════════════════════════════════════════\n"
        "TEXT DENSITY (STRICT — the #1 image-quality rule)\n"
        "════════════════════════════════════════════════════════════\n\n"
        "The image MUST render text in this exact hierarchy — nothing more:\n\n"
        "  1. ONE HEADING — bold display, ≤ 6 words, the single core message.\n"
        "  2. Optional ONE SUBHEADING — a single sentence ≤ 12 words.\n"
        "     NEVER a paragraph. NEVER two sentences. NEVER a colon that\n"
        "     introduces a bullet list. If the campaign brief gives you a\n"
        "     paragraph, DISTILL it into one short sentence.\n"
        "  3. 1–3 KEY HIGHLIGHTS — each is a 2–4 word chip / icon-label.\n"
        "     Each highlight is a NOUN PHRASE, not a full sentence.\n\n"
        "DO NOT render any of the following on the image:\n"
        "  • Multi-line body paragraphs.\n"
        "  • Long descriptions, explainer copy, or storytelling text.\n"
        "  • Feature lists with more than 3 items.\n"
        "  • Numbered step-by-step instructions.\n"
        "  • Paragraph testimonials, reviews, or quotes longer than 1 line.\n"
        "  • Full URLs, long emails, or paragraphs of contact detail (a\n"
        "    single phone + short email pill at the bottom is fine).\n\n"
        "PRIORITY: Rendering these density rules matters MORE than\n"
        "decorative visual detail. When in doubt, leave empty space —\n"
        "crowded text kills the composition. Long-form content belongs\n"
        "in the post caption below the image, NOT on the image itself.\n"
        "════════════════════════════════════════════════════════════\n"
    )


def is_auto(style: Optional[str]) -> bool:
    if not style:
        return True
    return style.strip().lower() == AUTO_STYLE


def resolve(style: Optional[str]) -> dict:
    if not style:
        return IMAGE_STYLES[AUTO_STYLE]
    return IMAGE_STYLES.get(style.strip().lower()) or IMAGE_STYLES[AUTO_STYLE]


def build_agent1_system_prompt(style: Optional[str], default_prompt: str) -> str:
    """Return the Art Director SYSTEM prompt to use for this request.

    - Auto (or unknown style) → return default_prompt verbatim. Pipeline
      runs byte-for-byte identical to before the style feature existed.
    - Any explicit style → build a NEW system prompt from that style's
      visual_dna block. Replaces (does not augment) the default prompt,
      so TEMPLATE A/B language and "figure out a visual style" phrasing
      never enter the conversation.
    """
    if is_auto(style):
        # Auto path — append TEXT DENSITY rules to the default Art Director
        # prompt so gpt-image-2 stops rendering paragraphs of body copy.
        return default_prompt + _text_density_rules()
    entry = resolve(style)
    dna = entry.get("visual_dna") or {}
    label = entry["label"]
    hint = entry.get("prompt_hint", "")

    def _f(key: str, fallback: str) -> str:
        v = dna.get(key)
        return (v or fallback).strip()

    return f"""You are a Senior Art Director specialising in the {label} visual style.

Your ONLY job is to produce an image_prompt that renders in the {label} aesthetic. Every visual decision — composition, colour, texture, lighting, typography — flows from this style, not from the campaign's subject matter.

════════════════════════════════════════════════════════════
STYLE DNA — {label}
════════════════════════════════════════════════════════════

VISUAL DIRECTIVE:
{hint}

COMPOSITION:
{_f("composition", "Compose the scene to reflect the style directive above.")}

COLOUR PALETTE:
{_f("palette", f"Colours must match the style. Brand colour <BRAND_COLOR> only as accent (headline highlight, CTA button, thin divider) — never as background or dominant fill.")}

TYPOGRAPHY:
{_f("typography", "Typography treatment must match the style.")}

MOOD & LIGHTING:
{_f("mood", "Mood and lighting must match the style directive.")}

ELEMENTS TO INCLUDE:
{_f("elements_to_include", "Elements native to the style directive.")}

ELEMENTS TO AVOID:
{_f("elements_to_avoid", "Anything that would push the image away from the style directive.")}

════════════════════════════════════════════════════════════
HARD RULES (apply to every image regardless of style)
════════════════════════════════════════════════════════════

• Place the attached logo in the TOP-LEFT corner, used EXACTLY as provided. Do NOT stylise, recolour, or redraw the logo. Only the surrounding scene adopts the {label} aesthetic.
• Place a clear call-to-action button in the BOTTOM-LEFT or BOTTOM-RIGHT corner.
• Aspect ratio: <ASPECT_RATIO>
• Brand colour <BRAND_COLOR> appears as ACCENT only — never as the dominant fill or background.
• Keep the ENTIRE scene in sharp focus. No depth-of-field blur, no bokeh, no soft-focus effect.

════════════════════════════════════════════════════════════
CONTEXT (subject matter only — NOT visual style)
════════════════════════════════════════════════════════════

The campaign brief, post copy, and business category in the user message tell you WHAT the post is about — its subject, message, and message-hierarchy. Use them to decide what to depict.

Do NOT use them to decide HOW to depict it. The HOW is locked to {label}, always.

════════════════════════════════════════════════════════════
OUTPUT
════════════════════════════════════════════════════════════

Emit a single detailed image_prompt string that gpt-image-2 will render. Describe the scene in vivid detail — subject, composition, colours, textures, lighting, typography — every element consistent with the {label} aesthetic. Aim for 400-600 words in the prompt. No preamble, no meta-commentary. Just the prompt.
{"" if (style or "").strip().lower() in {"minimal_text", "luxury_jewelry_editorial", "temple_event_flyer", "deity_devotional_poster", "software_product_framework", "software_product_feature_collage", "software_product_integration_showcase", "consulting_thought_leadership", "consulting_report_cover", "consulting_context_photo", "consulting_bold_photo_headline", "consulting_type_announcement", "overseas_agency", "adaptive_context", "edtech_promo", "deadline_urgency_flyer", "user_intent_post", "designer_grade_post", "edit_style", "social_media_designer"} else _text_density_rules()}"""


# ─────────────────────────────────────────────────────────────────
# User-prompt STYLE LOCK block.
#
# The magic-image pipeline (Art Director) uses
# `build_agent1_system_prompt` and no longer needs this — the whole
# system prompt is swapped per style. The CAROUSEL director path
# still uses this block on the user prompt because its system prompt
# is architectural (multi-slide JSON contract), not stylistic, and
# swapping it wholesale would break the deck-output shape.
# ─────────────────────────────────────────────────────────────────
def build_style_lock_block(style: Optional[str]) -> str:
    """Return a STYLE LOCK user-prompt block for pipelines that CAN'T
    swap their system prompt per style (currently: carousel director).

    Empty string when style is Auto so the pipeline is byte-for-byte
    identical to before this feature existed.
    """
    if is_auto(style):
        return ""
    entry = resolve(style)
    dna = entry.get("visual_dna") or {}
    label = entry["label"]
    hint = entry.get("prompt_hint", "")

    def _f(key: str, fallback: str) -> str:
        v = dna.get(key)
        return (v or fallback).strip()

    return (
        "════════════════════════════════════════════════════════════\n"
        f"🎨 STYLE LOCK — RENDER STRICTLY IN {label.upper()} STYLE\n"
        "════════════════════════════════════════════════════════════\n"
        "This is a USER-SELECTED style override. It supersedes every\n"
        "default instruction, template rule, and system-prompt guidance\n"
        "you have received about picking a visual style.\n\n"
        f"MANDATORY STYLE: {label}\n"
        f"VISUAL DIRECTIVE: {hint}\n\n"
        f"COMPOSITION: {_f('composition', 'Compose to reflect the style.')}\n"
        f"COLOUR PALETTE: {_f('palette', 'Colours must match the style; brand colour only as accent.')}\n"
        f"TYPOGRAPHY: {_f('typography', 'Typography treatment must match the style.')}\n"
        f"MOOD & LIGHTING: {_f('mood', 'Mood and lighting must match the style.')}\n"
        f"ELEMENTS TO INCLUDE: {_f('elements_to_include', 'Elements native to the style.')}\n"
        f"ELEMENTS TO AVOID: {_f('elements_to_avoid', 'Anything that would push the image away from the style.')}\n\n"
        "OVERRIDE RULES:\n"
        f"  1. Do NOT \"figure out a visual style that fits\" — style is FROZEN to {label}.\n"
        f"  2. Composition, imagery, typography, mood are NOT your choice — they must match {label}.\n"
        f"  3. Do NOT default to 3D dashboard renders, glossy tech, product mockups, screen-glow\n"
        f"     scenes, or generic marketing hero visuals unless part of the {label} directive.\n"
        f"  4. Do NOT invent decorative 3D characters or mascots that don't belong in {label}.\n"
        f"  5. Every element MUST come from the {label} aesthetic.\n\n"
        "LOGO PRESERVATION: Render the brand logo EXACTLY as provided in the reference\n"
        f"image. Do NOT stylise the logo. Only the surrounding scene adopts the {label} aesthetic.\n"
        "════════════════════════════════════════════════════════════\n\n"
        + _text_density_rules()
    )


def public_catalog() -> list[dict]:
    """Shape returned by GET /config/image-styles. Grouping/ordering
    matches the picker layout. Visual DNA is intentionally omitted —
    it's an internal detail, not something the frontend needs."""
    return [
        {
            "slug": slug,
            "label": entry["label"],
            "group": entry["group"],
            "emoji": entry.get("emoji", ""),
            "when_to_use": entry["when_to_use"],
        }
        for slug, entry in IMAGE_STYLES.items()
    ]


# ═══════════════════════════════════════════════════════════════════════
# AUTO-STYLE ROUTING: business category → style group → best style
# ═══════════════════════════════════════════════════════════════════════
# Categories that don't yet have a curated style group route to `auto`
# (unchanged default behaviour). Add mappings here as we build more
# style groups (food_beverage, healthcare, education, finance, etc.).

CATEGORY_TO_STYLE_GROUP: dict[str, str] = {
    # Canonical 10 (+ personal) — from services/business_categorizer.py
    "software_product":   "software_product",
    "software_service":   "consulting_services",
    "physical_product":   "physical_product",
    "religious":          "religious",
    "travel_immigration": "travel_immigration",
    "education":          "education",
    # Still unmapped → fall through to `auto` for now:
    "human_services":     "auto",
    "food_beverage":      "auto",
    "healthcare":         "auto",
    "finance":            "auto",
    "personal":           "auto",
    # Legacy 4-value scheme — existing DNAs saved before the 10-category
    # migration still carry these; alias to the closest canonical group.
    "saas_product":       "software_product",
    "hardware_service":   "auto",
}


def styles_in_group(group: str) -> list[str]:
    """All style slugs whose `group` field matches. Order matches
    IMAGE_STYLES declaration order — deterministic across runs so the
    fallback (first style) is stable."""
    if not group:
        return []
    return [slug for slug, entry in IMAGE_STYLES.items()
            if (entry.get("group") or "").strip().lower() == group.strip().lower()]


def resolve_style_group_for_category(category: Optional[str]) -> str:
    """Category key → style group. Unknown/custom → 'auto'."""
    if not category:
        return "auto"
    cat = category.strip().lower()
    if cat.startswith("custom:"):
        return "auto"
    return CATEGORY_TO_STYLE_GROUP.get(cat, "auto")


_STYLE_PICKER_MODEL = None  # lazy-init via os.environ inside picker


def pick_best_style_for_brief(brief: str, group: str,
                              business_dna: Optional[dict] = None) -> str:
    """GPT-score styles in the group against the campaign brief; return
    the winning style_id. Deterministic fallbacks:

    - group == 'auto' or empty                → 'auto' (default pipeline)
    - only one style in group                 → that style
    - all styles in group used in last 7 days → least-recently-used one
    - GPT call fails / bad JSON               → first FRESH candidate
    - GPT returns a slug not in the group     → first FRESH candidate
    - GPT picks a recently-used slug          → snap to first FRESH candidate

    7-DAY MEMORY: styles in the group used within the last 7 days for
    this Business DNA are dropped from the candidate list before GPT
    scores them. Falls back to least-recently-used when everything's
    fresh has been exhausted.
    """
    if not group or group.strip().lower() == "auto":
        return AUTO_STYLE

    candidates = styles_in_group(group)
    if not candidates:
        return AUTO_STYLE
    if len(candidates) == 1:
        return candidates[0]

    # Build a compact catalog of the candidates so GPT can score them
    catalog_lines = []
    for slug in candidates:
        entry = IMAGE_STYLES[slug]
        catalog_lines.append(
            f"- {slug}: {entry['label']} — {entry.get('when_to_use', '').strip()}"
        )
    catalog_block = "\n".join(catalog_lines)

    dna_summary = ""
    if business_dna:
        dna_bits = []
        for k in ("company_name", "industry", "business_type", "description"):
            v = business_dna.get(k)
            if isinstance(v, str) and v.strip():
                dna_bits.append(f"{k}: {v.strip()[:200]}")
        if dna_bits:
            dna_summary = "BUSINESS DNA:\n" + "\n".join(dna_bits) + "\n\n"

    system_prompt = (
        "You are a Senior Art Director. Pick the ONE style slug that best "
        "fits the campaign brief. Reply with ONLY a JSON object, no prose.\n\n"
        "OUTPUT SCHEMA: {\"style\": \"<one of the slugs below>\"}\n\n"
        "CANDIDATE STYLES:\n" + catalog_block
    )
    user_prompt = f"{dna_summary}CAMPAIGN BRIEF:\n{(brief or '').strip()[:2000]}\n\nReturn the JSON."

    import os as _os, json as _json
    model = _os.environ.get("STYLE_PICKER_MODEL", "gpt-5-nano")

    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = _json.loads(raw)
        pick = str(parsed.get("style", "")).strip().lower()
        if pick in candidates:
            return pick
        # GPT returned junk — fall through to first candidate.
    except Exception:
        pass

    return candidates[0]
