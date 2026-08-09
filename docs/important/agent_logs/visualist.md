# LINKEDIN_VISUALIST Agent Logs

## Agent Name
**LINKEDIN_VISUALIST** (The Prompt Engineer)

## Purpose
Generates high-control image generation prompts (Deterministic, Visually Explicit, Layout Locked) for AI image models like Gemini/Imagen.

## Prompt Template
```python
prompt = f"""
You are NOT a designer. You are a PROMPT ENGINEER specialized in generating HIGH-CONTROL prompts for AI image generation models (Gemini / Imagen).

INPUT:
1. REFINED CAMPAIGN BRIEF: "{refined_brief}"
2. LINKEDIN POST CONTENT: "{linkedin_content}"

TASK:
Analyze the product and service mentioned...
Generate 3 HIGH-PERFORMANCE IMAGE GENERATION PROMPTS...

... [Detailed Rules for Square 1:1, PURE WHITE, Grid Split, Typography Blueprint, etc.] ...

OUTPUT FORMAT: STRICT JSON ONLY.
"""
```

## Actual Full Prompt (Raw)
```text
(Full Prompt Sent to Gemini)
You are NOT a designer. You are a PROMPT ENGINEER specialized in generating HIGH-CONTROL prompts for AI image generation models (Gemini / Imagen).

INPUT:
1. REFINED CAMPAIGN BRIEF: "Spenzo is an AI-powered marketing intelligence and optimization platform designed for modern growth teams to measure performance, forecast outcomes, and maximize ROI across channels. It connects seamlessly with platforms like Amazon Web Services, Snowflake, Google Ads, Meta Ads, and TikTok to unify data into a single intelligent layer. Powered by AI agents, Spenzo enables automated insights, MMM-based forecasting, and budget optimization, helping teams achieve higher ROAS, eliminate manual analysis, and make faster data-driven decisions. The campaign should highlight measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI. Visuals should focus on connected marketing stacks, performance dashboards, and budget allocation scenarios. The target audience includes marketing leaders, performance marketers, and data teams managing large-scale ad spend. The tone should be sharp, results-driven, and analytical, with a strong CTA to book a demo and optimize marketing performance. https://spenzo.io/"
2. LINKEDIN POST CONTENT: "{'viral_reach': "Is your marketing budget bleeding ROI due to fragmented data? It's time to demand more than just metrics \u2013 demand intelligence.\n\nIntroducing \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc, the AI-powered platform transforming how modern growth teams operate.\n\n\u21b3 Unify your entire marketing stack: AWS, Snowflake, Google Ads, Meta Ads, TikTok & more, into one intelligent layer.\n\u21b3 Achieve unprecedented accuracy: Our MMM-based forecasting boasts 92% model accuracy.\n\u21b3 Unlock massive growth: Drive up to 3.2x higher ROAS through automated budget optimization and insights.\n\nEliminate manual analysis and accelerate data-driven decisions with conversational AI. Stop guessing, start growing.\n\nReady to see your marketing spend truly perform? Discover \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc:\nhttps://spenzo.io/\n\nShare your thoughts on marketing ROI below! \ud83d\udc47", 'high_interaction': "Marketing leaders & data teams: What's the biggest roadblock to maximizing your campaign ROI right now? Is it fragmented data, slow insights, or inaccurate forecasting?\n\nImagine a world where your entire marketing stack \u2013 from Google Ads to Snowflake \u2013 speaks one language. That's the power of \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc.\n\n\u21b3 Seamlessly connects all your platforms for a unified data view.\n\u21b3 AI agents deliver automated, actionable insights, eliminating manual grunt work.\n\u21b3 Conversational AI makes complex data accessible, enabling faster decisions.\n\nWe're helping teams achieve 3.2x higher ROAS and 92% model accuracy with ease.\n\nTag a colleague who needs to see this, or tell us: What's your biggest data challenge? Share below! \ud83d\udc47", 'follower_growth': "Elevate your marketing performance with AI that truly understands your data. \n\nAt \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc, we believe the future of growth is intelligent, automated, and predictive. That's why we built \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc.\n\n\u21b3 Gain automated insights and MMM-based forecasting with 92% accuracy.\n\u21b3 Optimize budgets across channels for higher ROAS (e.g., 3.2x).\n\u21b3 Unify data from AWS, Google Ads, Meta Ads, TikTok & more, effortlessly.\n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for cutting-edge marketing intelligence that empowers data teams and marketing leaders to make smarter, faster decisions. Stay ahead of the curve in AI-powered optimization. \n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for more AI insights \ud83d\udd14"}"

TASK:
Analyze the product and service mentioned in the refined brief and post content.
Generate 3 HIGH-PERFORMANCE IMAGE GENERATION PROMPTS that align dynamically with the campaign niche.

Your output will be directly used by an AI image model.
So prompts must be:
- Deterministic
- Visually explicit
- Layout locked
- No ambiguity
- No interpretation needed

DO NOT explain anything.
DO NOT describe design thinking.
ONLY generate FINAL IMAGE PROMPTS.

---

CORE IMAGE GENERATION RULES (SQUARE 1024X1024 SPLIT-LAYOUT):

Every generation_prompt MUST follow this EXACT structure:

1. START with the DIMENSIONAL ANCHOR:
"STRICT SQUARE 1:1 RATIO. RESOLUTION 1024x1024. EVERY ELEMENT MUST FIT WITHIN A PERFECT SQUARE CANVAS."

2. CANVAS:
- PURE WHITE background (#FFFFFF)

3. GRID & ELEMENTS (FLUID SPLIT-SCREEN DESIGN):
- Use the provided square-padded brand logo at top-left. DO NOT change, redraw, or modify it. It is an IMMUTABLE FIXED BITMAP ASSET. \ud83d\udd20\ud83d\udee1\ufe0f
- TAGLINE: Place a professional product/service tagline (e.g., "Market Intelligence Platform" or "AI-Powered CRM") in small, clean uppercase text above the heading.
- LEFT 53% SECTION: One high-impact "MAGNET HOOK" headline and a benefit-driven sub-heading.
- RIGHT 47% SECTION (FREE/FLUID COMPOSITE ZONE): Create a dynamic visual narrative WITHOUT a fixed shape or circular mask. The visual should flow naturally.
- Inside the fluid zone: High-fidelity professional imagery showing the "Problem vs Solution" narrative.
- Layout: Balanced split-screen composition inside a 1024x1024 frame.

4. MAGNET HOOK COPYWRITING (IRONCLAD TYPO & REPETITION CONTROL):
- **BLUEPRINT ANCHOR RULE:** For any word longer than 5 letters, you MUST provide a "Letter-by-Letter Blueprint" in the final prompt (e.g. Render the word "PREDICTIVE" (P-R-E-D-I-C-T-I-V-E)).
- **ANTI-HYPHEN GUARD (STRICT):** Instruct the model: "The hyphenated spelling in parentheses is a blueprint instruction ONLY. DO NOT RENDER THE HYPHENS in the final image. Render every word cleanly." \ud83d\udccf\ud83d\udee1\ufe0f
- **NON-REPETITION LOCK:** Instruct the model: "EACH WORD MUST BE RENDERED EXACTLY ONCE. Do not repeat words, phrases, or characters. Consecutively repeated words (e.g., 'THE THE') are fatal layout errors." \ud83d\udee0\ufe0f\ud83d\udee1\ufe0f
- **QUOTE-SOURCING:** Wrap headlines/sub-headings in explicit "DOUBLE QUOTES".
- SUB-HEADING: Must be CONCISE (under 8 words).
- Style: Heavy modern sans-serif. Color: Black with #FF4500 highlights.

5. VISUAL SUBJECT (FLUID PROBLEM vs SOLUTION NARRATIVE):
Inside the right-side Fluid Zone, create a transformation narrative using EXCLUSIVE BRAND ORANGE (#FF4500) effects:
- INSTRUCTION: Identify the user's PROBLEM and your product's SOLUTION.
- PROBLEM REPRESENTATION: Visualize complexity/chaos subtlely using dimmed, fragmented grey nodes or tangled lines.
- SOLUTION REPRESENTATION: The product node (Hero) MUST transform the chaos into clean, vibrant, GLOWING brand-orange path-lines (#FF4500) that flow logically into order. \ud83c\udfa8\ud83d\udee1\ufe0f
- BRAND ORANGE ACCENTS: Use #FF4500 for all highlights, glowing flows, data-visualization curves, and atmospheric shades. Ensure a premium, unified brand feel.
- DYNAMIC ALIGNMENT: 
    - FOR INTEGRATIONS/PLATFORMS/TOOLS/AI/SOFTWARE/APIs: Show a "one-to-many" or "many-to-one" network flow using platform icons and orange integration paths.
    - FOR ANALYTICS: Show data distilled into a high-fidelity orange growth curve.
    - FOR PEOPLE: Show authentic individuals (#FF4500 accents in environment) collaborating or looking at the camera with professional confidence.
- PREFERRED STYLE: High-end editorial photography or clean 3D Infographics, cinematic lighting, natural textures, and professional white space.
- FORBIDDEN (STRICLY NO): NO robotic arms, NO glowing robot faces, NO chaotic neon "AI junk". Keep it "Enterprise Premium".
- LOGO INTEGRITY: THE LOGO IS FIXED. DO NOT TOUCH IT. \ud83d\udd20\ud83d\udee1\ufe0f

6. HARD NEGATIVE (STRICT BANNER DENIAL):
Include EXACTLY this line:
"NO wide shots, NO banner, NO rectangle, NO horizontal flow, NO 16:9 ratio, NO 3:1 ratio, NO social cover, NO distorted text, NO misspelled words, NO blurry elements, NO low resolution"

7. call to action:
- Identify url from the refined brief
- Add call to action button(#ff4500) with the url Bold show only the domain name ex: pipelyt.com in the bottom right corner

8. END WITH FINAL SQUARE LOCK:
"PERFECT SQUARE 1:1, editorial split-layout, bold typography, high-fidelity branding, ultra high resolution"

OUTPUT FORMAT: STRICT JSON ONLY.
```

## Actual Response (Raw JSON)
```json
[
  {
    "platform": "LinkedIn",
    "layout": "Social Graphic",
    "name": "Global Strategist",
    "heading": "MAXIMIZE YOUR ROAS",
    "sub_heading": "Unlock 3.2x higher return.",
    "generation_prompt": "STRICT SQUARE 1:1 RATIO. RESOLUTION 1024x1024. EVERY ELEMENT MUST FIT WITHIN A PERFECT SQUARE CANVAS. PURE WHITE background (#FFFFFF). Use the provided square-padded brand logo at top-left. DO NOT change, redraw, or modify it. It is an IMMUTABLE FIXED BITMAP ASSET. \ud83d\udd20\ud83d\udee1\ufe0f LEFT 53% SECTION: Render the word \\\"MARKETING\\\" (M-A-R-K-E-T-I-N-G). Render the word \\\"INTELLIGENCE\\\" (I-N-T-E-L-L-I-G-E-N-C-E). Render the word \\\"PLATFORM\\\" (P-L-A-T-F-O-R-M). This tagline in small, clean uppercase text above the heading. Heavy modern sans-serif typeface, color Black. Render the word \\\"MAXIMIZE\\\" (M-A-X-I-M-I-Z-E). Render the word \\\"YOUR\\\". Render the word \\\"ROAS\\\". This headline in heavy modern sans-serif, color Black with #FF4500 highlights. Render the word \\\"UNLOCK\\\" (U-N-L-O-C-K). Render \\\"3.2x\\\". Render the word \\\"HIGHER\\\" (H-I-G-H-E-R). Render the word \\\"RETURN\\\" (R-E-T-U-R-N). This sub-heading in heavy modern sans-serif, color Black. The hyphenated spelling in parentheses is a blueprint instruction ONLY. DO NOT RENDER THE HYPHENS in the final image. Render every word cleanly. EACH WORD MUST BE RENDERED EXACTLY ONCE. Do not repeat words, phrases, or characters. Consecutively repeated words (e.g., 'THE THE') are fatal layout errors. RIGHT 47% SECTION (FREE/FLUID COMPOSITE ZONE): Create a dynamic visual narrative showing the problem vs solution. High-fidelity 3D Infographic style. Visualize fragmented grey data nodes subtly representing disparate platforms like AWS, Snowflake, Google Ads, Meta Ads, TikTok (use their distinct, recognizable logos/icons in muted grey). From a central, intelligent Spenzo node, GLOWING #FF4500 (brand orange) path-lines emerge, unifying these fragmented grey nodes into a single, clean, logical data flow. These orange paths converge into a vibrant, ascending data-visualization growth curve, clearly displaying \\\"3.2x ROAS\\\" as a glowing #FF4500 metric. Ensure premium, unified brand feel. NO robotic arms, NO glowing robot faces, NO chaotic neon 'AI junk'. Keep it 'Enterprise Premium'. Layout: Balanced split-screen composition inside a 1024x1024 frame. Add a call to action button in #FF4500 color in the bottom right corner showing bold \\\"spenzo.io\\\". NO wide shots, NO banner, NO rectangle, NO horizontal flow, NO 16:9 ratio, NO 3:1 ratio, NO social cover, NO distorted text, NO misspelled words, NO blurry elements, NO low resolution. PERFECT SQUARE 1:1, editorial split-layout, bold typography, high-fidelity branding, ultra high resolution"
  },
  {
    "platform": "LinkedIn",
    "layout": "Social Graphic",
    "name": "Collaborative Partner",
    "heading": "UNIFY YOUR MARKETING STACK",
    "sub_heading": "Automate insights. Accelerate decisions.",
    "generation_prompt": "STRICT SQUARE 1:1 RATIO. RESOLUTION 1024x1024. EVERY ELEMENT MUST FIT WITHIN A PERFECT SQUARE CANVAS. PURE WHITE background (#FFFFFF). Use the provided square-padded brand logo at top-left. DO NOT change, redraw, or modify it. It is an IMMUTABLE FIXED BITMAP ASSET. \ud83d\udd20\ud83d\udee1\ufe0f LEFT 53% SECTION: Render the word \\\"AI\\\". Render the word \\\"OPTIMIZATION\\\" (O-P-T-I-M-I-Z-A-T-I-O-N). Render the word \\\"PLATFORM\\\" (P-L-A-T-F-O-R-M). This tagline in small, clean uppercase text above the heading. Heavy modern sans-serif typeface, color Black. Render the word \\\"UNIFY\\\". Render the word \\\"YOUR\\\". Render the word \\\"MARKETING\\\" (M-A-R-K-E-T-I-N-G). Render the word \\\"STACK\\\". This headline in heavy modern sans-serif, color Black with #FF4500 highlights. Render the word \\\"AUTOMATE\\\" (A-U-T-O-M-A-T-E). Render the word \\\"INSIGHTS\\\" (I-N-S-I-G-H-T-S). Render the word \\\"ACCELERATE\\\" (A-C-C-E-L-E-R-A-T-E). Render the word \\\"DECISIONS\\\" (D-E-C-I-S-I-O-N-S). This sub-heading in heavy modern sans-serif, color Black. The hyphenated spelling in parentheses is a blueprint instruction ONLY. DO NOT RENDER THE HYPHENS in the final image. Render every word cleanly. EACH WORD MUST BE RENDERED EXACTLY ONCE. Do not repeat words, phrases, or characters. Consecutively repeated words (e.g., 'THE THE') are fatal layout errors. RIGHT 47% SECTION (FREE/FLUID COMPOSITE ZONE): Create a dynamic visual narrative. High-end editorial photography style. Two professional, ethnically diverse marketing leaders (one male, one female) collaborating confidently, looking at a holographic, transparent display that serves as a Spenzo dashboard. Subtle, dimmed grey tangled lines or fragmented data points are visible in the background, symbolizing fragmented data. The holographic display projects clean, unified, GLOWING #FF4500 data streams and a performance dashboard with a prominent \\\"92% model accuracy\\\" metric. The individuals have professional confidence, with subtle #FF4500 accents in their environment (e.g., a screen glow or light reflection). NO robotic arms, NO glowing robot faces, NO chaotic neon 'AI junk'. Keep it 'Enterprise Premium'. Layout: Balanced split-screen composition inside a 1024x1024 frame. Add a call to action button in #FF4500 color in the bottom right corner showing bold \\\"spenzo.io\\\". NO wide shots, NO banner, NO rectangle, NO horizontal flow, NO 16:9 ratio, NO 3:1 ratio, NO social cover, NO distorted text, NO misspelled words, NO blurry elements, NO low resolution. PERFECT SQUARE 1:1, editorial split-layout, bold typography, high-fidelity branding, ultra high resolution\"\n  },
  {
    "platform": "LinkedIn",
    "layout": "Social Graphic",
    "name": "Modern Expert",
    "heading": "FORECAST WITH PRECISION",
    "sub_heading": "Predict outcomes. Optimize budgets.",
    "generation_prompt": "STRICT SQUARE 1:1 RATIO. RESOLUTION 1024x1024. EVERY ELEMENT MUST FIT WITHIN A PERFECT SQUARE CANVAS. PURE WHITE background (#FFFFFF). Use the provided square-padded brand logo at top-left. DO NOT change, redraw, or modify it. It is an IMMUTABLE FIXED BITMAP ASSET. \ud83d\udd20\ud83d\udee1\ufe0f LEFT 53% SECTION: Render the word \\\"AI-POWERED\\\" (P-O-W-E-R-E-D). Render the word \\\"GROWTH\\\" (G-R-O-W-T-H). Render the word \\\"PLATFORM\\\" (P-L-A-T-F-O-R-M). This tagline in small, clean uppercase text above the heading. Heavy modern sans-serif typeface, color Black. Render the word \\\"FORECAST\\\" (F-O-R-E-C-A-S-T). Render the word \\\"WITH\\\". Render the word \\\"PRECISION\\\" (P-R-E-C-I-S-I-O-N). This headline in heavy modern sans-serif, color Black with #FF4500 highlights. Render the word \\\"PREDICT\\\" (P-R-E-D-I-C-T). Render the word \\\"OUTCOMES\\\" (O-U-T-C-O-M-E-S). Render the word \\\"OPTIMIZE\\\" (O-P-T-I-M-I-Z-E). Render the word \\\"BUDGETS\\\" (B-U-D-G-E-T-S). This sub-heading in heavy modern sans-serif, color Black. The hyphenated spelling in parentheses is a blueprint instruction ONLY. DO NOT RENDER THE HYPHENS in the final image. Render every word cleanly. EACH WORD MUST BE RENDERED EXACTLY ONCE. Do not repeat words, phrases, or characters. Consecutively repeated words (e.g., 'THE THE') are fatal layout errors. RIGHT 47% SECTION (FREE/FLUID COMPOSITE ZONE): Create a dynamic visual narrative. Clean 3D Infographics style. Depict multiple budget allocation scenarios. Initial grey, scattered budget segments or fragmented pie charts (problem representation) are seamlessly transformed by GLOWING #FF4500 (brand orange) algorithms into neatly allocated, vibrant orange budget segments, showcasing optimization. A high-fidelity digital dashboard is integrated into the scene, displaying a prominent \\\"92% Model Accuracy\\\" gauge and an upward-trending, #FF4500-highlighted predictive growth line, symbolizing maximized ROAS. Emphasize logical flow and order. NO robotic arms, NO glowing robot faces, NO chaotic neon 'AI junk'. Keep it 'Enterprise Premium'. Layout: Balanced split-screen composition inside a 1024x1024 frame. Add a call to action button in #FF4500 color in the bottom right corner showing bold \\\"spenzo.io\\\". NO wide shots, NO banner, NO rectangle, NO horizontal flow, NO 16:9 ratio, NO 3:1 ratio, NO social cover, NO distorted text, NO misspelled words, NO blurry elements, NO low resolution. PERFECT SQUARE 1:1, editorial split-layout, bold typography, high-fidelity branding, ultra high resolution\"\n  }\n]\n```
