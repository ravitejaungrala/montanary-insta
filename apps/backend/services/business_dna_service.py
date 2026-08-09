import requests
import json
import logging
import concurrent.futures
import re
from urllib.parse import urlparse
from core.config import GEMINI_API_KEY
from services.ai_service import client
from services.retry_helper import call_with_retry
from google.genai import types

logger = logging.getLogger("pipelyt.dna")

# Business DNA extraction models — primary + fallback.
# PRIMARY   = gemini-flash-lite-latest (alias currently pointing to 3.1-flash-lite,
#             $0.25 in / $1.50 out per 1M tokens as of 2026-07-08)
# FALLBACK  = gemini-2.5-flash-lite (older stable version, cheaper at
#             $0.10 in / $0.40 out per 1M tokens). Used when the primary
#             exhausts its retry attempts on transient errors, or when the
#             alias hot-swaps to a version that's temporarily unavailable.
DNA_PRIMARY_MODEL  = "gemini-flash-lite-latest"
DNA_FALLBACK_MODEL = "gemini-2.5-flash-lite"

def _parse_dna_json(text: str) -> dict:
    """
    Parse a Gemini JSON response defensively.

    Gemini's `response_mime_type="application/json"` doesn't guarantee strictly
    valid JSON for large string fields — it frequently emits raw newlines and
    other control characters inside long string values (our ``overview`` is
    4-6KB of prose, which is where it typically breaks with
    ``Expecting ',' delimiter: line N column M`` errors).

    Strategy:
      1. Strip Markdown code fences if Gemini wrapped the output.
      2. Try ``json.loads(..., strict=False)`` — allows control chars in
         strings (including the common raw-newline case).
      3. As a last resort, regex-extract the most important fields so the
         caller can still hand the user a partial DNA instead of a total
         failure. The caller's own fallbacks (logo resolution, favicon
         fallback, etc.) fill in the rest.

    Never raises — returns at least an empty dict.
    """
    if not text:
        return {}

    # 1. Strip common Gemini wrappers.
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    # 2. Non-strict JSON: tolerates unescaped control chars inside strings.
    try:
        return json.loads(cleaned, strict=False)
    except Exception as e:
        logger.warning(f"strict=False JSON parse still failed: {e}. Falling back to regex extraction.")

    # 3. Last-resort regex extraction. We accept lossy parsing here — better
    #    to ship partial DNA than to fail the whole extract_business_dna call.
    partial = {}
    for field in ("company_name", "product_name", "tagline", "logo_url"):
        m = re.search(
            rf'"{field}"\s*:\s*"((?:\\.|[^"\\])*)"',
            cleaned,
            re.DOTALL,
        )
        if m:
            partial[field] = m.group(1)

    overview_m = re.search(
        r'"overview"\s*:\s*"((?:\\.|[^"\\])*)"',
        cleaned,
        re.DOTALL,
    )
    if overview_m:
        partial["overview"] = overview_m.group(1)

    if partial:
        logger.info(f"Regex fallback recovered fields: {list(partial.keys())}")
        partial["_partial_parse"] = True
    return partial


def normalize_url(url: str):
    """
    Normalizes 'spenzo.io' or 'www.spenzo.io' to 'https://spenzo.io'
    """
    if not url: return url
    url = url.strip().lower()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


def _verify_image_url(candidate: str, timeout: float = 4.0) -> bool:
    """
    Returns True iff `candidate` resolves (HTTP 200) to something whose
    Content-Type starts with 'image/'. Safe no-op on empty / malformed input.

    Used to keep hallucinated logo URLs from leaking to the frontend — LLMs
    frequently guess a plausible pattern like `${domain}/${slug}.png` that does
    not actually exist, and Microlink sometimes returns stale favicon guesses.
    """
    if not candidate or not isinstance(candidate, str):
        return False
    if not candidate.lower().startswith(("http://", "https://")):
        return False

    try:
        # HEAD first — cheap and most image hosts support it.
        resp = requests.head(candidate, timeout=timeout, allow_redirects=True)
        # Some CDNs reject HEAD (405) or return 0 content-length — fall back to
        # a tiny ranged GET in that case.
        if resp.status_code in (405, 403, 501):
            resp = requests.get(
                candidate,
                timeout=timeout,
                allow_redirects=True,
                headers={"Range": "bytes=0-0"},
                stream=True,
            )
        if resp.status_code >= 400:
            return False
        content_type = (resp.headers.get("Content-Type") or "").lower()
        # Some servers omit Content-Type on HEAD; tolerate that only if the URL
        # has an obvious image extension.
        if content_type.startswith("image/"):
            return True
        if not content_type:
            return candidate.lower().split("?")[0].rsplit(".", 1)[-1] in (
                "png", "jpg", "jpeg", "gif", "svg", "webp", "ico",
            )
        return False
    except Exception as e:
        logger.info(f"Logo verify miss for {candidate}: {e}")
        return False


def _domain_from_url(url: str) -> str:
    """Returns the bare host (no scheme, no trailing slash, no www.) for a URL."""
    try:
        host = urlparse(url).netloc or url
        return host.replace("www.", "").strip("/")
    except Exception:
        return url


def _host_variants(host: str) -> list:
    """
    Small, conservative set of plausible host rewrites for a given hostname.

    Cases covered:
      - www. prefix toggled on/off  (foo.com <-> www.foo.com)
      - hyphen dropped from the second-level label  (z-ninth.com -> zninth.com)
        Only triggered when a hyphen is actually present — we never invent one.

    Order: original first, then variants. De-duplicated.
    """
    if not host:
        return []

    host = host.strip().lower()
    bare = host[4:] if host.startswith("www.") else host

    variants = [host, bare, "www." + bare]

    parts = bare.split(".")
    if len(parts) >= 2 and "-" in parts[0]:
        no_hyphen = parts[0].replace("-", "")
        if no_hyphen and no_hyphen != parts[0]:
            alt_bare = ".".join([no_hyphen] + parts[1:])
            variants.extend([alt_bare, "www." + alt_bare])

    ordered = []
    for h in variants:
        if h and h not in ordered:
            ordered.append(h)
    return ordered


def _url_with_host_variants(url: str) -> list:
    """
    Expands a single URL into candidate URLs that swap only the host.
    Preserves scheme/path/query so we can probe `/logo.png` across related hosts.
    """
    if not url:
        return []
    try:
        parsed = urlparse(url)
    except Exception:
        return [url]
    if not parsed.netloc:
        return [url]
    return [
        parsed._replace(netloc=h).geturl()
        for h in _host_variants(parsed.netloc)
    ]


def _resolve_logo_url(ai_candidate: str, microlink_candidate: str, site_url: str) -> str:
    """
    Returns the first candidate whose URL actually serves an image. Always
    returns *something* — the Google favicon fallback is near-universal.

    Order of preference (each tier is further expanded by host variants — see
    _host_variants — so a brand at `zninth.com` is still found when the user
    typed `z-ninth.com` or vice versa):
      1. Logo the AI extracted from page content (most specific if it exists)
      2. Microlink's detected logo (usually a well-known favicon/og:image)
      3. Clearbit logo service (high-quality for established brands)
      4. Google S2 favicons (works for effectively every reachable domain)

    Every candidate passes through _verify_image_url (HEAD/GET with an
    image/* Content-Type gate), so the first match is guaranteed reachable.
    """
    domain = _domain_from_url(site_url)
    domain_variants = _host_variants(domain)

    candidates = []
    candidates.extend(_url_with_host_variants(ai_candidate))
    candidates.extend(_url_with_host_variants(microlink_candidate))
    for h in domain_variants:
        candidates.append(f"https://logo.clearbit.com/{h}")
    for h in domain_variants:
        candidates.append(f"https://www.google.com/s2/favicons?domain={h}&sz=256")

    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if _verify_image_url(c):
            logger.info(f"Logo resolved via verified candidate: {c}")
            return c

    # Last-resort: return the Google favicon URL unverified. Its endpoint is
    # always up even when it has nothing for a domain (returns a generic icon).
    fallback = f"https://www.google.com/s2/favicons?domain={domain}&sz=256"
    logger.warning(f"All logo candidates failed verification; returning {fallback}")
    return fallback

def fetch_microlink(url):
    logger.info(f"Fetching Microlink data for {url}")
    try:
        resp = requests.get(f"https://api.microlink.io/?url={url}&screenshot=true", timeout=20)
        resp.raise_for_status()
        return resp.json().get('data', {})
    except Exception as e:
        logger.warning(f"Microlink fetch failed: {e}")
        return {}

def fetch_jina_base(url):
    """Fetches base URL via headless browser allowing React/SPA hydration and link summary."""
    logger.info(f"Fetching Jina AI (Browser Engine) markdown for {url}")
    try:
        headers = {
            "X-With-Links-Summary": "true",
            "X-Engine": "browser",
            "X-Timeout": "15",
            "Accept": "text/markdown",
        }
        resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Jina AI Base fetch failed: {e}")
        return ""

def fetch_jina_fast(url):
    """Fetches secondary pages fast without the browser engine overhead."""
    logger.info(f"Fetching Jina AI (Fast Mode) mapping for {url}")
    try:
        headers = {
            "X-Retain-Images": "none",
            "Accept": "text/markdown",
            # Sometimes SPAs need browser engine even for subpages, we'll try fast first
        }
        resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Jina AI Fast fetch failed for {url}: {e}")
        return ""

def get_nav_links_from_jina(markdown, base_url):
    """Pulls the generated nav-links block Jina appends via X-With-Links-Summary."""
    LINKS_SECTION_RE = re.compile(
        r'(?:Buttons\s*&\s*Links|Links/Buttons):\s*\n(.*?)(?:\n\n|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    LINK_ITEM_RE = re.compile(r'-\s*\[([^\]]+)\]\((https?://[^)]+)\)')
    
    match = LINKS_SECTION_RE.search(markdown)
    if not match:
        logger.info("No Links block found in Jina response. Falling back to all links parsing.")
        all_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', markdown)
    else:
        all_links = LINK_ITEM_RE.findall(match.group(1))

    base_domain = urlparse(base_url).netloc.replace("www.", "")
    valid_urls = []
    seen = set()
    
    ignore_keywords = ['privacy', 'terms', 'cookie', 'login', 'signup', 'register', 'legal', 'twitter.com', 'linkedin.com', 'instagram.com', 'facebook.com']
    
    for text, link in all_links:
        link_lower = link.lower()
        if link_lower in seen: continue
        
        # Must be same domain or subdomain
        link_domain = urlparse(link).netloc.replace("www.", "")
        if base_domain not in link_domain:
            continue
            
        # Ignore garbage pages
        if any(ignore in link_lower for ignore in ignore_keywords):
            continue
            
        # Ignore resolving to just the homepage / hashes
        path = urlparse(link).path
        if path == "" or path == "/" or "mailto:" in link_lower or "tel:" in link_lower:
            continue
            
        seen.add(link_lower)
        valid_urls.append(link)
        
    # Limit to 8 subpages to prevent extremely long timeouts/rate limits
    return valid_urls[:8]


def extract_business_dna(url: str, is_product: bool = False):
    """
    Crawls URL deeply using Jina AI and generates an exhaustive Knowledge Base via Gemini.
    """
    # Defense-in-depth: an empty URL routes to `https://r.jina.ai/` which
    # returns Jina AI's own homepage as the "extracted" content. Refuse
    # empty inputs at the service boundary so no caller (router, script,
    # nexus pipeline) can accidentally pollute a user's brand DNA.
    if not isinstance(url, str) or not url.strip():
        raise ValueError("extract_business_dna requires a non-empty URL")
    try:
        url = normalize_url(url)
        logger.info(f"Extracting {'Product' if is_product else 'Business'} DNA (Deep Crawl) from URL: {url}")
        
        # 1. Fetch Jina (base SPA rendering) & Microlink concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_jina_base = executor.submit(fetch_jina_base, url)
            future_microlink = executor.submit(fetch_microlink, url)
            
            jina_base_markdown = future_jina_base.result() or "No textual content extracted."
            microlink_data = future_microlink.result() or {}

        # 2. Extract Links & Fetch Sub-pages concurrently
        sub_links = get_nav_links_from_jina(jina_base_markdown, url)
        logger.info(f"Discovered {len(sub_links)} sub-links for knowledge base: {sub_links}")
        
        compiled_markdown = f"--- HOMEPAGE SOURCE ({url}) ---\n\n{jina_base_markdown}\n\n"
        
        if sub_links:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(sub_links)) as executor:
                future_to_url = {executor.submit(fetch_jina_fast, sub_url): sub_url for sub_url in sub_links}
                for future in concurrent.futures.as_completed(future_to_url):
                    sub_url = future_to_url[future]
                    try:
                        data = future.result()
                        if data and len(data) > 200: # Ensure we got actual content
                            compiled_markdown += f"--- SUB-PAGE SOURCE ({sub_url}) ---\n{data}\n\n"
                    except Exception as exc:
                        logger.warning(f"{sub_url} sub-page fetch failed: {exc}")

        # 3. Extract Microlink Metadata & Screenshot
        logo_data = microlink_data.get("logo") or {}
        logo_url = logo_data.get("url")
        
        screenshot_data = microlink_data.get("screenshot") or {}
        screenshot_url = screenshot_data.get("url")
        
        title = microlink_data.get("title", "")
        description = microlink_data.get("description", "")

        screenshot_bytes = None
        if screenshot_url:
            try:
                img_resp = requests.get(screenshot_url, timeout=10)
                if img_resp.status_code == 200:
                    screenshot_bytes = img_resp.content
                    logger.info("Successfully fetched screenshot image.")
            except Exception as e:
                logger.warning(f"Failed to fetch screenshot image: {e}")

        # 4. Construct Multi-page Knowledge Base AI Prompt
        prompt = f"""
        Extract Business DNA Knowledge Base for URL: {url}
        
        MICROLINK METADATA:
        Title: {title}
        Description: {description}
        Expected Logo URL: {logo_url if logo_url else 'None'}

        AGGREGATED WEBSITE CONTENT (Markdown format including Homepage and Sub-pages):
        ---
        {compiled_markdown[:80000]}  # 80k char limit for performance 
        ---

        TASK:
        You are provided with deeply aggregated website textual content (including specific product/services subpages), metadata, and a screenshot of the homepage.
        
        1. COLOR PALETTE: Select 4 brand colors: [primary(color used for buttons and links), secondary(color used for text), accent(color used for highlights), background(color used for background)]. Look for the main visual theme of the UI elements in the provided screenshot image. Output colors as hex codes.
        2. KNOWLEDGE BASE (overview): Generate an EXHAUSTIVE BUSINESS/PRODUCT KNOWLEDGE BASE. Do NOT write a short summary. Write a highly detailed, structured report that explicitly outlines:
           - Core Offerings & Master Value Proposition.
           - Explicit list of ALL Products & Services identified across the text.
           - Specific Use Cases and Capabilities.
           - Target Audience and Industries.
           (Return this as a PLAIN TEXT string inside the JSON under the key "overview". DO NOT use Markdown formatting, NO asterisks, NO bolding, NO hashes. Use UPPERCASE TEXT and spacing/newlines to delineate sections).
        3. TAGLINE: Generate a CATCHY BRAND TAGLINE (under 10 words).
        4. {'PRODUCT_NAME: Identify the specific NAME of this product.' if is_product else 'COMPANY_NAME: Identify the name of the company.'}
        5. LOGO: Find the absolute URL for the primary brand logo. If Expected Logo URL above is 'None', deeply search the Markdown textual content for an image URL that looks like the main brand logo. If you still find nothing, output 'https://logo.clearbit.com/{url.split('//')[-1].split('/')[0]}'.
        6. CATEGORY: Classify this business into EXACTLY ONE of the eleven
           categories below. This drives (a) the image-generation industry
           playbook and (b) which visual-style group the Auto pipeline
           picks a style from. Read the definitions carefully and pick the
           BEST fit. If genuinely ambiguous, prefer the category that best
           describes what a customer BUYS (the deliverable) rather than
           how the business operates.

           Emit the raw slug (lowercase, underscore) as the value.

           - "software_product" — A SOFTWARE product that customers use
             directly through a browser, mobile app, desktop app, or API.
             Sold as self-serve subscriptions or licenses. What the
             customer buys is access to the software itself.
             Examples: Salesforce, Notion, Stripe, Google Workspace,
             Microsoft 365, Swiggy, Uber, Netflix, Figma, Slack, GitHub,
             Zoom, Shopify, Airbnb, Canva.

           - "software_service" — Human-delivered professional / IT
             services (often digital / knowledge-work). The deliverable is
             a project, report, strategy, or custom build produced by the
             team.
             Examples: PwC, Accenture, Infosys, TCS, McKinsey, marketing
             agencies, web-development agencies, design studios, SEO
             firms, ad agencies, dev shops, growth agencies, business
             coaches, financial advisors, law firms, accounting firms.

           - "physical_product" — A TANGIBLE object the customer buys and
             physically owns / wears / uses. Sold direct-to-consumer,
             wholesale, or retail.
             Examples: jewelry brands, clothing / apparel / fashion,
             watches, cosmetics / skincare / makeup, cars, electronics,
             gadgets, furniture, home decor, books, toys, sports
             equipment, kitchenware, packaged goods (non-food).

           - "human_services" — On-location, physical, hands-on services
             that require someone to be present at a place (customer's
             home, business, or a physical site). Local trades.
             Examples: plumbing, electrical, HVAC, construction,
             renovation contractors, cleaning services, landscaping,
             pest control, salons / spas / barbershops, gyms / personal
             trainers, auto repair, moving services, handyman services.

           - "travel_immigration" — Travel, tourism, visa / immigration
             consulting, study-abroad agencies.
             Examples: travel agencies, tour operators, visa consultants,
             immigration lawyers, study-abroad advisors, ticketing
             portals.

           - "religious" — Religious or spiritual institutions and
             practices.
             Examples: temples, churches, mosques, gurdwaras, ashrams,
             meditation centers, yoga studios, spiritual coaches.

           - "food_beverage" — Restaurants, cafes, packaged food brands,
             catering, food delivery, bakeries, beverage brands.
             Examples: restaurants, cafes, food trucks, cloud kitchens,
             packaged snacks, bakeries, breweries, coffee brands.

           - "healthcare" — Clinics, hospitals, dental, mental wellness,
             physiotherapy, telemedicine, diagnostic labs.
             Examples: hospitals, dental clinics, physio, mental health
             apps, telemedicine platforms, diagnostic labs, wellness
             centers.

           - "education" — Schools, universities, coaching institutes,
             online course platforms, tutoring.
             Examples: schools, colleges, EdTech platforms, coaching
             institutes, tutoring services, certification providers.

           - "finance" — Banks, investment advisors, insurance, crypto,
             lending, accounting software / firms.
             Examples: banks, insurance companies, mutual fund advisors,
             fintech apps, crypto exchanges, lending platforms.

           - "personal" — Individual creators / personal brands with no
             formal business (rare — only if the website is a personal
             portfolio with no product / service being sold).

           If the business fits BOTH a software product AND a service
           (e.g. HubSpot which sells software but also runs an agency),
           pick the PRIMARY revenue driver. When unsure, prefer
           "software_service" over "software_product" for consulting-heavy
           businesses.

        Return strictly as JSON (NO markdown code blocks outside of the "overview" value, just raw JSON):
        {{
            "product_name": "{'The product name' if is_product else ''}",
            "company_name": "{'' if is_product else 'The company name'}",
            "logo_url": "String containing the absolute logo URL",
            "tagline": "...",
            "colors": {{ "primary": "#hex", "secondary": "#hex", "accent": "#hex", "background": "#hex" }},
            "fonts": ["Font Name"],
            "brand_values": ["..."],
            "brand_aesthetic": ["..."],
            "brand_tone": ["..."],
            "category": "one of: software_product | software_service | physical_product | human_services | travel_immigration | religious | food_beverage | healthcare | education | finance | personal",
            "overview": "Detailed string containing the knowledge base. MUST use strict \\n for newlines, do not output raw literal line breaks."
        }}
        """
        
        # 5. Call Gemini with retry + fallback model.
        #
        # Strategy:
        #   1. Try PRIMARY (gemini-flash-lite-latest) with 3-retry exponential
        #      backoff. Transient errors (429 rate limit / 5xx / timeout /
        #      network) get retried; non-transient (spend cap / auth / bad
        #      input) fail fast.
        #   2. If primary exhausts retries, fall back to gemini-2.5-flash-lite
        #      (older, cheaper, independent version — survives cases where
        #      the `-latest` alias is hot-swapped to a broken preview).
        #   3. If fallback also fails, re-raise so the outer except block
        #      returns the error-shaped dict.
        contents = [prompt]
        if screenshot_bytes:
            contents.append(types.Part.from_bytes(data=screenshot_bytes, mime_type='image/png'))

        _dna_config = types.GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
        )

        res = None
        _last_primary_err = None
        try:
            res = call_with_retry(
                lambda: client.models.generate_content(
                    model=DNA_PRIMARY_MODEL,
                    contents=contents,
                    config=_dna_config,
                ),
                label=f"Gemini/BUSINESS_DNA/{DNA_PRIMARY_MODEL}",
            )
        except Exception as _primary_err:
            _last_primary_err = _primary_err
            logger.warning(
                f"[DNA] primary model {DNA_PRIMARY_MODEL!r} exhausted retries "
                f"— falling back to {DNA_FALLBACK_MODEL!r}. "
                f"Last error: {_primary_err}"
            )
            try:
                res = call_with_retry(
                    lambda: client.models.generate_content(
                        model=DNA_FALLBACK_MODEL,
                        contents=contents,
                        config=_dna_config,
                    ),
                    label=f"Gemini/BUSINESS_DNA/{DNA_FALLBACK_MODEL}",
                )
                logger.info(
                    f"[DNA] fallback model {DNA_FALLBACK_MODEL!r} succeeded "
                    f"after primary failed"
                )
            except Exception as _fallback_err:
                logger.error(
                    f"[DNA] BOTH models failed. "
                    f"primary={_last_primary_err} fallback={_fallback_err}"
                )
                raise  # outer except block returns the error-shaped dict
        
        # Defensive JSON parse — Gemini occasionally emits raw newlines inside
        # the long `overview` string which breaks strict json.loads. See
        # _parse_dna_json for the full strategy (non-strict + regex fallback).
        dna_data = _parse_dna_json(res.text) or {}
        if not dna_data:
            logger.error(
                f"Gemini returned no parseable JSON for {url}. "
                f"Raw first 500 chars: {res.text[:500]!r}"
            )
        
        # Fallback processing
        if is_product and not dna_data.get("product_name"):
            domain = url.split("//")[-1].split("/")[0].replace("www.", "").split(".")[0].capitalize()
            dna_data["product_name"] = domain

        # Always resolve to a verified, reachable image URL. LLMs happily
        # hallucinate logo paths ('${domain}/${slug}.png') that 404 in the
        # browser, so never trust the AI output without a HEAD check.
        ai_logo = dna_data.get("logo_url") or ""
        dna_data["logo_url"] = _resolve_logo_url(
            ai_candidate=ai_logo if isinstance(ai_logo, str) and ai_logo.startswith("http") else "",
            microlink_candidate=logo_url or "",
            site_url=url,
        )
                
        if not dna_data.get("tagline") or dna_data.get("tagline") == "...":
            name = dna_data.get("company_name", "Your Brand")
            dna_data["tagline"] = f"{name}: Elevating your digital presence."

        # Normalise the classifier output. Gemini occasionally returns the
        # display label ("SaaS Product") or hyphenated forms — coerce to the
        # canonical lowercase-underscore slug. Anything unrecognised becomes
        # an empty string so downstream code falls back to the safe default
        # (current SaaS/service prompt).
        # Canonical 11 (source of truth: services/business_categorizer.py).
        # Legacy aliases from the pre-migration DNAs are also accepted and
        # rewritten to their canonical equivalent so downstream routing
        # (industry playbook + auto-style group) always sees a valid key.
        _legacy_alias = {
            "saas_product":     "software_product",
            "hardware_service": "human_services",
        }
        try:
            from services.business_categorizer import CANONICAL_KEYS as _valid_categories
        except Exception:
            _valid_categories = {
                "software_product", "software_service", "physical_product",
                "human_services", "travel_immigration", "religious",
                "food_beverage", "healthcare", "education", "finance",
                "personal",
            }
        raw_cat = str(dna_data.get("category") or "").strip().lower()
        raw_cat = raw_cat.replace("-", "_").replace(" ", "_")
        raw_cat = _legacy_alias.get(raw_cat, raw_cat)  # migrate legacy → canonical
        dna_data["category"] = raw_cat if raw_cat in _valid_categories else ""

        # Ensure source metadata is recorded
        dna_data["url"] = url
        dna_data["screenshot_url"] = screenshot_url
            
        return dna_data
        
    except Exception as e:
        logger.error(f"DNA Extraction failed: {e}")
        return { "error": str(e), "company_name": url, "overview": f"Failed to extract for {url}: {e}" }
