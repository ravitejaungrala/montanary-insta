"""
file name:Pipelyt AI Agents
All individual Gemini agent functions used in the multi-agent pipeline.
"""
import json
import logging
from google import genai
from google.genai import types
from core.config import GEMINI_API_KEY

logger = logging.getLogger("pipelyt.agents")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _call_agent(agent_name, prompt, model_name='gemini-flash-lite-latest'):
    """Helper to call Gemini and parse JSON response."""
    if not client:
        return {"error": "AI client not initialized"}
    try:
        res = client.models.generate_content(model=model_name, contents=prompt)
        text = res.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"Agent parsing error in {agent_name}: {e}")
        return {"error": "Invalid JSON", "raw": text if 'text' in locals() else str(e)}


def _refine_brief_agent(campaign_brief, user_context=""):
    """Agent 1: Refines the user's campaign brief for maximum marketing impact."""
    prompt = f"""
    You are a Senior Social Media Growth Strategist for the following profile:
    {user_context}

    TASK:
    Refine the following campaign brief for maximum VIRAL REACH and FOLLOWER GROWTH.

    STRATEGIC FOCUS (PRIORITY):
    - STRICTLY PRIORITIZE any technical facts, product specifications, or brand unique value propositions found in the provided REFERENCE DOCUMENTS/KNOWLEDGE BASE above.
    - Focus on "hook-driven" content that stops the scroll.
    - Optimize for shareability and community engagement.
    - Ensure the brand voice is authoritative yet highly accessible.

    ANTI-HALLUCINATION RULE (CRITICAL):
    - DO NOT invent or add specific statistics, percentages, dollar figures, or numeric claims (e.g., "3.2x ROAS", "$10M+", "92% accuracy") unless they are explicitly stated in the original brief or the Business DNA/Knowledge Base above.
    - Only reference verifiable facts from the provided context.

    BRIEF TO REFINE:
    "{campaign_brief}"

    Return STRICTLY in this JSON format:
    {{ "refined_brief": "..." }}
    """
    return _call_agent("REFINER", prompt)


def _research_agent(refined_brief, user_context=""):
    """Agent 2: Deep research into company/product, target audience, trends, and problem-solving opportunities."""
    prompt = f"""
    You are a Deep Research AI.

    USER/COMPANY CONTEXT:
    {user_context}

    TASK: Analyze this refined brief: "{refined_brief}"

    TASK:
    1. Identify any specific Company, Brand, or Product name mentioned (e.g., NEUZEN AI, SWASS, Pipelyt).
    2. Define a crystal clear Target Audience.
    3. Analyze the current Trending Context or tech landscape.
    4. Highlight the specific Problem-Solving Opportunity.
    5. PROVIDE DEEP ANALYSIS: Extract specific facts and technical details from the REFERENCE DOCUMENTS/KNOWLEDGE BASE in the user context to define the Product's unique edge.

    Return STRICTLY in this JSON format (ALL VALUES MUST BE STRINGS, NO NESTED OBJECTS):
    {{
        "target_audience": "...",
        "trending_context": "...",
        "problem_solving_opportunity": "...",
        "company_product_analysis": "..."
    }}
    """
    return _call_agent("RESEARCHER", prompt)


def _content_agent(refined_brief, research, platforms, user_context=""):
    """Agent 3: Creates strategic variants using platform-specific brand voice and Bold Unicode."""
    platforms_str = ", ".join(platforms)
    prompt = f"""
    You are the content creator for the following company/user profile:
    {user_context}

    Create 3 content variants for EACH of these platforms: {platforms_str} based on our brand voice and the brief below.

    BRIEF: "{refined_brief}"
    RESEARCH: {json.dumps(research)}

    STYLE GUIDELINES:
    1. TONE: Professional and authoritative. Use "STOP-THE-SCROLL" hooks that address a specific frustration or massive opportunity in the first line. Be specific to the FEATURE in the brief — not generic "AI is changing everything" copy.
    2. KNOWLEDGE BASE ENFORCEMENT: All variants MUST incorporate specific terminology, facts, or technical points found in the provided REFERENCE DOCUMENTS. Do not hallucinate features, statistics, percentages, or metrics that are not in the brief or knowledge base.
    3. PLATFORM CHARACTER LIMITS & FORMATTING:
       - Twitter: STRICTLY UNDER 240 CHARACTERS. Standard text only (no bold Unicode).
       - LinkedIn: Under 3,000 characters. Use professional, value-driven tone. Start with a hook.
       - Instagram: Under 2,200 characters. Use engaging, catchy hooks and emojis.
       - Facebook: Under 5,000 characters for high readability.

    4. BRANDING RULES:
       - Use Bold Unicode for brand names (e.g., 𝗡𝗘𝗨𝗭𝗘𝗡 𝗔𝗜, 𝗦𝗪𝗔𝗦𝗦 𝗔𝗜, 𝗟𝗲𝗻𝘀𝗔𝗜) ONLY on LinkedIn, Facebook, and Instagram.
       - Use arrow bullets (↳) for key points.
       - Use strategic line breaks for a premium feel.

    5. STRATEGIC VARIANTS & GOALS (MANDATORY):
    - "viral_reach": Broad hooks, "Us vs Them" logic, high-sharing potential. Goal: Maximum Platform Reach.
    - "high_interaction": Discussion starters, "Stop doing [Common Mistake]", Poll-style questions. Goal: Maximize Likes & Comments.
    - "follower_growth": Value authority, "The blueprint for [Problem]", Relationship-building CTAs. Goal: Increase Followers.

    6. ENGAGEMENT PROTOCOL:
    Every variant MUST end with a high-impact engagement anchor (CTA) tailored to the specific variant goal (e.g., "Join the conversation below 👇", "Follow for more AI insights 🔔").

    Return STRICTLY in this JSON format (DO NOT ommit any platform listed in {platforms_str}):
    {{
        "recommendation": {{ "best_variant": "viral_reach/high_interaction/follower_growth", "reason": "..." }},
        "content": {{
            "platform_name": {{
                "viral_reach": "platform specific reach content...",
                "high_interaction": "platform specific interaction content...",
                "follower_growth": "platform specific growth variant..."
            }}
        }}
    }}
    """
    data = _call_agent("COPYWRITER", prompt)
    if "content" in data:
        data["content"] = {k.lower(): v for k, v in data["content"].items()}
    return data


def _linkedin_visualist_agent(linkedin_content, refined_brief, primary_color="#FF4500"):
    """Agent 4: LinkedIn Visualist - Generates layout concepts and background-only image prompts.

    Text (heading, subheading, CTA) is added programmatically via PIL after image generation
    to guarantee zero typos and exact brand compliance.
    """
    prompt = f"""
    You are a SENIOR ART DIRECTOR. Your job: generate 3 DISTINCT BACKGROUND IMAGE PROMPTS for a social media ad.

    CAMPAIGN BRIEF: "{refined_brief}"
    BRAND PRIMARY COLOR (hex): {primary_color}

    ---

    ### STEP 1 — UNDERSTAND THE CAMPAIGN
    Read the brief carefully. Extract:
    - The core USER BENEFIT (what problem does this solve for the user?)
    - The EMOTIONAL TONE (aspirational? precise? energetic? calm?)
    - The INDUSTRY/DOMAIN (tech, wellness, finance, fashion, logistics, etc.)

    Use this understanding to drive EVERY creative decision below.
    Do NOT use generic concepts — all 3 variants must visually reflect the specific campaign.

    ---

    ### CRITICAL RULE — NO MARKETING LANGUAGE IN IMAGE PROMPTS
    The generation_prompt goes directly to an image AI. NEVER use words like:
    "marketing", "budget", "ROI", "ROAS", "analytics", "dashboard", "data", "KPI",
    "optimization", "campaign", "spend", "revenue", "metrics", "performance", "report".
    These trigger the image AI to generate full ad creatives with baked-in text.
    Instead: describe PURELY VISUAL AND ARTISTIC scenes using shape, color, light, and motion.

    COMPLETELY FORBIDDEN (image AI adds text/UI):
    - Dashboards, charts, screens, or any UI
    - Boardroom meetings, presentations, people pointing at screens
    - Any element described with financial or marketing terminology

    PROVEN TO WORK (no text artifacts):
    - Abstract 3D light streams flowing and converging (describe color and direction only)
    - Glowing geometric forms (prisms, rings, crystalline shapes) floating in dramatic light
    - Clean sculptural 3D objects with studio lighting
    - Single dramatic silhouette (back or side angle only) facing abstract light source
    - Fluid or organic flowing forms representing movement, growth, or clarity
    - Architectural environments (minimalist corridors, vast open spaces, geometric rooms)
    - Macro nature (flowing water, light through glass, crystal formations, light refraction)

    ---

    ### HEADINGS — CAMPAIGN-SPECIFIC OUTCOME HOOKS
    Write headings that reflect the actual campaign benefit. Examples by category:
    - Productivity/AI tools: "WORK AT LIGHT SPEED", "ZERO MANUAL EFFORT", "AUTOMATE THE GRIND"
    - Finance/Planning: "SPEND WITH PRECISION", "CONTROL EVERY DOLLAR", "PLAN SMARTER TODAY"
    - Health/Wellness: "FEEL BETTER FASTER", "STRENGTH FROM WITHIN", "YOUR BEST SELF NOW"
    - E-commerce/Retail: "SELL MORE EVERY DAY", "GROWTH WITHOUT LIMITS", "YOUR BRAND SCALES"
    - Generic: derive your own from the brief

    Rules:
    - No invented numbers, percentages, or dollar figures
    - Heading: action-oriented, punchy, specific to the campaign benefit
    - Sub-heading: expand on the outcome — 1–2 short sentences, no stats

    ---

    ### LAYOUT TYPES (choose one per variant — must vary across the 3):

    **"left-text"** — Text block on the LEFT half. Visual element on the RIGHT.
    - Best for: professional, authoritative, benefit-driven campaigns
    - Composition: Left 50% = clean open space for text. Right 50% = visual subject.

    **"center-text"** — Text spans full width, anchored at bottom. Visual fills entire canvas.
    - Best for: aspirational, cinematic, emotional campaigns
    - Composition: Full canvas visual. Bottom third reserved for text overlay.

    **"right-visual"** — Visual element centered or right. Text will overlay left.
    - Best for: product-focused, energetic, bold campaigns
    - Composition: Center or right = visual subject. Left = dark/open gradient.

    ---

    ### VISUAL STYLE DIVERSITY (each variant MUST be visually distinct):
    - Variant 1: Abstract light/energy (flowing streams, beams, particles)
    - Variant 2: 3D geometric/sculptural (objects, shapes, architecture, materials)
    - Variant 3: Organic/cinematic (nature macro, silhouette, dramatic environment)

    Or choose any 3 distinct styles that best match the campaign — the key is NO TWO look alike.

    ---

    ### COMPOSITION RULES:
    - For "left-text": Left 50% must be open/dark enough for text. Visual = right side.
    - For "center-text": Full canvas visual OK. Bottom 30% slightly darker for readability.
    - For "right-visual": Similar to left-text — keep left side clean.
    - NO text, NO numbers, NO UI, NO labels anywhere in the generated image.

    ---

    ### OUTPUT FORMAT (STRICT JSON ARRAY — exactly 3 items):
    [
      {{
        "platform": "LinkedIn",
        "layout_type": "left-text | center-text | right-visual",
        "name": "Creative concept name (campaign-specific, e.g. 'Precision Flow')",
        "heading": "CAMPAIGN-SPECIFIC OUTCOME HOOK",
        "sub_heading": "Specific benefit sentence. No invented stats.",
        "generation_prompt": "STRICT SQUARE 1:1, 1024x1024, 8k, ultra-sharp, photorealistic render. SCENE: [Campaign-inspired pure visual — shapes, colors, light, motion. NO marketing words, NO UI, NO screens, NO text of any kind. Composition: [describe where text space is — e.g. left 50% open dark gradient, right 50% = visual]. STYLE: Cinematic studio lighting, crisp edges, rich contrast. ABSOLUTE RULE: NO text, NO typography, NO labels, NO numbers, NO logos, NO watermarks anywhere. Pure background visual only.]"
      }}
    ]

    RETURN ONLY THE JSON ARRAY. No explanation.
    """
    return _call_agent("LINKEDIN_VISUALIST", prompt)


def _visual_critic_agent(image_bytes, original_prompt, refined_brief, primary_color="#FF4500", heading="", sub_heading=""):
    """Agent 6: Visual Quality Critic - Audits generated images for background quality only.

    IMPORTANT: Text (heading, subheading, logo, CTA) is added AFTER image generation via PIL.
    The critic should verify background visual quality — NOT check whether AI-generated text matches,
    because all text in the final image was composited by PIL code, not Gemini.
    """
    if not client:
        return {"is_valid": True}
    prompt = f"""
    You are the Lead Visual Quality Auditor for a two-pass image pipeline.

    CONTEXT — HOW THIS IMAGE WAS CREATED:
    1. Gemini generated a BACKGROUND visual (photorealistic scene, no text).
    2. PIL (Python code) composited text and UI elements ONTO the background:
       - Logo: top-left corner
       - Heading (line 1, brand color): "{heading.upper().split()[0] + ' ' + heading.upper().split()[1] if len(heading.split()) >= 2 else heading.upper()}"
       - Heading (line 2, white): "{' '.join(heading.upper().split()[2:]) if len(heading.split()) > 2 else ''}"
       - Sub-heading (gray): "{sub_heading}"
       - CTA pill button: bottom-left, brand color {primary_color}

    YOUR JOB: Audit the BACKGROUND VISUAL QUALITY only. The text overlay is correct by design.

    BACKGROUND QUALITY CHECKLIST:
    1. SHARPNESS: Is the background scene sharp and in focus (not blurry or soft)? Blurry backgrounds = score penalty.
    2. RELEVANCE: Is the background scene directly relevant to this brief: "{refined_brief[:200]}"?
    3. PROFESSIONALISM: Does it look like a premium, high-end marketing asset (not stock-photo generic)?
    4. BACKGROUND TEXT ARTIFACTS: Does the BACKGROUND (behind the PIL text) contain any AI-generated text, watermarks, or labels that were NOT added by PIL? (PIL text is expected — check ONLY for extra Gemini-generated text beneath it.)
    5. COMPOSITION: Is the left side dark enough for the text overlay to be readable?
    6. LOGO VISIBILITY: Is the logo (top-left) clearly visible and not obscured?

    SCORING:
    - 9-10: Sharp, relevant, premium, no background text artifacts.
    - 7-8: Minor issues (slightly soft background, or scene is somewhat generic).
    - 5-6: Background is blurry OR background scene is completely irrelevant to the campaign.
    - 1-4: Background has Gemini-generated text artifacts OR is completely wrong scene.

    Return STRICTLY in this JSON format:
    {{
        "rating_out_of_10": (int),
        "reason": "Brief assessment of background quality, sharpness, relevance.",
        "improvement_advice": "Specific suggestion to improve the BACKGROUND SCENE prompt. E.g. 'Make background sharper, add more lighting contrast on left side.'",
        "is_valid": true | false
    }}

    CRITICAL RULE: Set `is_valid` to `false` only if rating <= 5 (blurry, irrelevant, or has background text artifacts).
    """
    try:
        res = client.models.generate_content(
            model='gemini-flash-lite-latest',
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(res.text)
    except Exception as e:
        logger.error(f"Visual Critic failed: {e}")
        return {"is_valid": True, "critique": "Audit skipped due to error"}


def _critic_agent(final_payload, campaign_brief, user_context=""):
    """Agent 5: The Strategic Critic - Verifies everything for accuracy and branding."""
    prompt = f"""
    You are the Senior Strategic Critic for following user profile:
    {user_context}

    Your job is to verify the entire generated campaign payload for consistency, branding, and impact.

    ORIGINAL BRIEF: "{campaign_brief}"
    GENERATED PAYLOAD: {json.dumps(final_payload, indent=2)}

    VERIFICATION CHECKLIST:
    1. Does the content align with the original brief?
    2. Are the branding rules (Bold Unicode, tone) applied?
    3. Are the visuals (generation prompts) distinct and platform-optimized?
    4. Are there any critical errors or inconsistencies?

    Return STRICTLY in this JSON format:
    {{
        "is_valid": true | false,
        "critique": "Overall strategic assessment...",
        "adjustments": "Specific tiny fixes if needed (otherwise empty string)"
    }}
    """
    return _call_agent("CRITIC", prompt)


def _planner_agent(refined_brief, research, platforms, days=7, user_context=""):
    """Agent 5: Creates a multi-day, multi-platform strategic campaign plan."""
    from datetime import datetime
    platforms_str = ", ".join(platforms)
    current_date = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""
    You are a Senior Social Media Campaign Architect for the following company/user profile:
    {user_context}

    TODAY'S DATE: {current_date}
    USER PROFILE CONTEXT: {user_context}

    Create a high-impact {days}-day posting plan for these platforms: {platforms_str}.
    All generated 'date' and 'time' values MUST be in the USER'S LOCAL TIMEZONE.

    BRIEF: "{refined_brief}"
    RESEARCH: {json.dumps(research)}

    TASK: Generate a {days}-day content calendar starting from {current_date}.
    Distribute posts logically across the platforms.

    Return STRICTLY in this JSON format (a list of posts):
    {{
        "plan": [
            {{
                "week": 1,
                "date": "YYYY-MM-DD",
                "day": "DayName",
                "channel": "Platform",
                "content_type": "PostType (use ONLY 'Text' or 'Image')",
                "topic": "Creative Hook or Title",
                "theme": "Strategery (e.g. Awareness, Conversion, Education, Community)",
                "cta": "Primary Call to Action",
                "time": "HH:MM (24h format, eg: 09:00, 15:30)"
            }}
        ]
    }}

    RULES:
    1. Start the first post from {current_date}. Every post MUST be on or after this date.
    2. ONLY suggest 'Text' or 'Image' in the content_type field.
    3. Vary the themes for a balanced feed.
    4. Suggest high-engagement times (e.g. 9:00 AM, 5:00 PM) based on platform best practices.
    """
    return _call_agent("PLANNER", prompt)
