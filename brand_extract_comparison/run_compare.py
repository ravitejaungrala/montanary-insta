"""
Standalone brand-extraction comparison: context.dev vs pipelyt's Jina path.

NOT part of pipelyt. The Jina functions below are copied VERBATIM from
apps/backend/services/business_dna_service.py so the extraction is byte-for-byte
identical to what extract_business_dna() feeds Gemini (steps 1-2: fetch_jina_base
-> get_nav_links_from_jina -> fetch_jina_fast on up to 8 sub-pages -> compile).

Reads CONTEXT_DEV_API_KEY from the environment (not hard-coded).
Writes <domain>__pipelyt_jina.md and <domain>__context_dev.md per domain.
"""
import os
import re
import json
import requests
import concurrent.futures
from urllib.parse import urlparse

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAINS = ["neuzenai.com", "spenzo.io", "zyntegrate.com"]
CONTEXT_KEY = os.environ.get("CONTEXT_DEV_API_KEY", "")


# ===================================================================
# VERBATIM COPIES from apps/backend/services/business_dna_service.py
# ===================================================================
def normalize_url(url: str):
    if not url:
        return url
    url = url.strip().lower()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


def fetch_jina_base(url):
    """Fetches base URL via headless browser allowing React/SPA hydration and link summary."""
    print(f"  [jina-base] {url}")
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
        print(f"  [jina-base] FAILED: {e}")
        return ""


def fetch_jina_fast(url):
    """Fetches secondary pages fast without the browser engine overhead."""
    print(f"  [jina-fast] {url}")
    try:
        headers = {
            "X-Retain-Images": "none",
            "Accept": "text/markdown",
        }
        resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  [jina-fast] FAILED {url}: {e}")
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
        all_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', markdown)
    else:
        all_links = LINK_ITEM_RE.findall(match.group(1))

    base_domain = urlparse(base_url).netloc.replace("www.", "")
    valid_urls = []
    seen = set()

    ignore_keywords = ['privacy', 'terms', 'cookie', 'login', 'signup', 'register', 'legal', 'twitter.com', 'linkedin.com', 'instagram.com', 'facebook.com']

    for text, link in all_links:
        link_lower = link.lower()
        if link_lower in seen:
            continue
        link_domain = urlparse(link).netloc.replace("www.", "")
        if base_domain not in link_domain:
            continue
        if any(ignore in link_lower for ignore in ignore_keywords):
            continue
        path = urlparse(link).path
        if path == "" or path == "/" or "mailto:" in link_lower or "tel:" in link_lower:
            continue
        seen.add(link_lower)
        valid_urls.append(link)

    return valid_urls[:8]


# ===================================================================
# Pipelyt Jina aggregation (extract_business_dna steps 1-2, verbatim logic)
# ===================================================================
def pipelyt_jina_extract(url):
    url = normalize_url(url)
    jina_base_markdown = fetch_jina_base(url) or "No textual content extracted."

    sub_links = get_nav_links_from_jina(jina_base_markdown, url)
    print(f"  discovered {len(sub_links)} sub-links: {sub_links}")

    compiled_markdown = f"--- HOMEPAGE SOURCE ({url}) ---\n\n{jina_base_markdown}\n\n"

    if sub_links:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sub_links)) as executor:
            future_to_url = {executor.submit(fetch_jina_fast, s): s for s in sub_links}
            for future in concurrent.futures.as_completed(future_to_url):
                s = future_to_url[future]
                try:
                    data = future.result()
                    if data and len(data) > 200:
                        compiled_markdown += f"--- SUB-PAGE SOURCE ({s}) ---\n{data}\n\n"
                except Exception as exc:
                    print(f"  {s} sub-page fetch failed: {exc}")

    return compiled_markdown, sub_links


def context_dev_extract(domain):
    resp = requests.get(
        f"https://api.context.dev/v1/brand/retrieve?domain={domain}",
        headers={"Authorization": f"Bearer {CONTEXT_KEY}"},
        timeout=40,
    )
    return resp.status_code, resp.json()


def main():
    if not CONTEXT_KEY:
        print("WARNING: CONTEXT_DEV_API_KEY not set; context.dev calls will 401.")

    summary = []
    for domain in DOMAINS:
        print(f"\n==== {domain} ====")

        # --- pipelyt Jina ---
        compiled, sub_links = pipelyt_jina_extract(domain)
        jina_path = os.path.join(OUT_DIR, f"{domain.replace('.', '_')}__pipelyt_jina.md")
        header = (
            f"# Pipelyt Jina extraction — {domain}\n\n"
            f"> This is the EXACT `compiled_markdown` that `extract_business_dna()` feeds to Gemini.\n"
            f"> Homepage via `fetch_jina_base` (browser engine) + {len(sub_links)} sub-pages via `fetch_jina_fast`.\n\n"
            f"**Sub-links crawled ({len(sub_links)}):**\n"
            + ("".join(f"- {s}\n" for s in sub_links) if sub_links else "- (none discovered)\n")
            + f"\n**Total compiled length:** {len(compiled):,} chars (Gemini sees first 80,000)\n\n---\n\n"
        )
        with open(jina_path, "w", encoding="utf-8") as f:
            f.write(header + compiled)
        print(f"  wrote {jina_path} ({len(compiled):,} chars)")

        # --- context.dev ---
        ctx_path = os.path.join(OUT_DIR, f"{domain.replace('.', '_')}__context_dev.md")
        try:
            code, payload = context_dev_extract(domain)
            with open(ctx_path, "w", encoding="utf-8") as f:
                f.write(f"# context.dev brand retrieve — {domain}\n\n")
                f.write(f"**HTTP {code}**\n\n")
                f.write("```json\n")
                f.write(json.dumps(payload, indent=2, ensure_ascii=False))
                f.write("\n```\n")
            print(f"  wrote {ctx_path} (HTTP {code})")
            ctx_ok = code == 200
        except Exception as e:
            with open(ctx_path, "w", encoding="utf-8") as f:
                f.write(f"# context.dev brand retrieve — {domain}\n\nERROR: {e}\n")
            print(f"  context.dev FAILED: {e}")
            ctx_ok = False

        summary.append((domain, len(compiled), len(sub_links), ctx_ok))

    print("\n==== SUMMARY ====")
    print(f"{'domain':<18}{'jina_chars':>12}{'sublinks':>10}{'ctx_ok':>8}")
    for d, jc, sl, ok in summary:
        print(f"{d:<18}{jc:>12,}{sl:>10}{str(ok):>8}")


if __name__ == "__main__":
    main()
