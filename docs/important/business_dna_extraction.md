# Business DNA Extraction Architecture

This document provides a comprehensive overview of the Business DNA Extraction pipeline used in Pipelyt. It is designed to act as context for other AI agents to understand exactly how user URLs are converted into robust branding profiles.

## 1. High-Level Workflow Overview

When a user provides a `business_url` or a `product_url`, the system executes the following steps:
1. **URL Normalization**: Standardizes the input to ensure a valid `https://` protocol.
2. **Concurrent Data Scraping**: Simultaneously hits two external APIs (`Jina AI` and `Microlink`) to gather layout text, metadata, and visual context.
3. **Screenshot Download**: Pulls the raw binary image of the generated screenshot.
4. **Multimodal AI Processing**: Sends the text, metadata, and image to Gemini 2.5 Flash to synthesize the final brand DNA.
5. **Fallback Checks**: Validates the extracted logo and tagline against known fallbacks.

---

## 2. Tools & APIs Used

The system heavily relies on external services to bypass standard web-scraping limitations (like dynamic React/Vue SPAs that don't render HTML server-side).

### A. Jina AI Reader API
- **Endpoint**: `https://r.jina.ai/{url}`
- **Why it's used**: Jina AI renders the URL utilizing a headless browser and returns purely sanitized **Markdown**. This bypasses heavy JavaScript rendering blockers, removes bloated HTML tags, and provides Gemini with highly semantic text content, making it incredibly effective for reading value propositions and features.

### B. Microlink API
- **Endpoint**: `https://api.microlink.io/?url={url}&screenshot=true`
- **Why it's used**: Microlink is a powerful metadata aggregation service. It instantly returns the site's explicit `title`, `description`, an authoritative `logo` URL based on sophisticated favicon/meta-graph scraping, and a generated `screenshot` URL. 

### C. Google Gemini 2.5 Flash (Multimodal)
- **Model**: `gemini-2.5-flash`
- **Why it's used**: Gemini 2.5 Flash has native multimodal vision capabilities. Instead of recursively parsing CSS files to guess brand colors, we stream the physical `screenshot_bytes` directly into the prompt. The AI literally "looks" at the website design to extract exact hex color codes mapped to buttons, text, and backgrounds.

---

## 3. The Multimodal Prompt

The exact prompt injected into the `gemini-2.5-flash` model, along with the injected data variables, is structured like this:

```text
Extract Business DNA for URL: {url}

MICROLINK METADATA:
Title: {title}
Description: {description}
Expected Logo URL: {logo_url if logo_url else 'None'}

WEBSITE TEXT CONTENT (Markdown format, up to 15k chars):
---
{jina_markdown[:15000]}
---

TASK:
You are provided with the website's textual content, metadata, and a screenshot of the website (as an image).
1. Select 4 brand colors: [primary(color used for buttons and links ), secondary(color used for text), accent(color used for highlights), background(color used for background)]. Look for the main visual theme of the UI elements in the provided screenshot image. Output colors as hex codes.
2. OVERVIEW: Generate a COMPREHENSIVE BUSINESS/PRODUCT REPORT (at least 15-20 meaningful sentences). 
   IMPORTANT: Use the textual context and screenshot to deduce the industry and value proposition. Act as a Creative Director. NEVER state that information is missing; instead, build the brand's story based on available signals.
3. TAGLINE: Generate a CATCHY BRAND TAGLINE (under 10 words).
4. COMPANY_NAME / PRODUCT_NAME: Identify the specific name.
5. Verify the logo URL. Use the Expected Logo URL provided above.

Return strictly as JSON (NO markdown code blocks, just raw JSON):
{{
    "product_name": "...",
    "company_name": "...",
    "logo_url": "...",
    "tagline": "...",
    "colors": {{ "primary": "#hex", "secondary": "#hex", "accent": "#hex", "background": "#hex" }},
    "fonts": ["Font Name"],
    "brand_values": ["..."],
    "brand_aesthetic": ["..."],
    "brand_tone": ["..."],
    "overview": "..."
}}
```

*Note: The actual screenshot image is attached to the Gemini request sequentially after this textual prompt via `google.genai.types.Part.from_bytes()`.*

---

## 4. Expected Final Output Structure

The AI responds strictly with a parsed JSON object. After generating the content, the backend applies slight fallbacks (e.g., if a logo isn't found, it falls back to `https://logo.clearbit.com/{domain}`). Finally, it appends the source `url` and `screenshot_url` to the dictionary.

This is the exact JSON structure delivered to the front-end and saved into the user's PostgreSQL database under the `business_dna` JSONB payload:

```json
{
  "product_name": "Stripe",
  "company_name": "Stripe",
  "logo_url": "https://images.stripeassets.com/.../favicon.png",
  "tagline": "Financial Infrastructure to Grow Your Revenue",
  "colors": {
    "primary": "#635BFF",
    "secondary": "#0A2540",
    "accent": "#FF6C37",
    "background": "#FFFFFF"
  },
  "fonts": [
    "Inter",
    "sans-serif"
  ],
  "brand_values": [
    "Reliability",
    "Innovation"
  ],
  "brand_aesthetic": [
    "Modern",
    "Clean",
    "Tech-forward"
  ],
  "brand_tone": [
    "Authoritative",
    "Direct"
  ],
  "overview": "Stripe stands as a pivotal financial infrastructure platform... [15-20 sentences mapping the extracted Jina text]",
  "url": "https://stripe.com",
  "screenshot_url": "https://iad.microlink.io/..."
}
```

## Summary for Agent Awareness
If another agent needs to interact with the Business DNA, it should expect the above dictionary format. The colors (specifically `colors.primary`) are guaranteed to represent the primary interactable components (like CTA buttons), while `colors.secondary` represents the core text hierarchy color.
