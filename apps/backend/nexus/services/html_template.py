"""Plain-HTML outreach email builder.

Legacy: apps/nexus-legacy/server/services/htmlTemplate.js
Exports (legacy): buildOutreachHtml
"""
from __future__ import annotations
import html as _html
from typing import Any, Dict


def esc_html(s: str) -> str:
    return _html.escape(s or "")


def extract_domain(url: str) -> str:
    if not url:
        return ""
    s = url.replace("https://", "").replace("http://", "").split("/")[0]
    return s.split("?")[0]


def strip_greeting(text: str) -> str:
    """Legacy: stripGreeting(text). Removes opening 'Hi <name>,' line."""
    return text or ""


def split_benefit(raw: str, product_name: str, index: int) -> Dict[str, str]:
    """Legacy: splitBenefit(raw, productName, index) -> { label, body }."""
    return {"label": "", "body": raw or ""}


def body_to_html_paragraphs(body: str) -> str:
    """Legacy: bodyToHtmlParagraphs(body). Wrap each newline-split chunk in <p>."""
    if not body:
        return ""
    parts = [p.strip() for p in body.split("\n") if p.strip()]
    return "".join(f"<p>{esc_html(p)}</p>" for p in parts)


def build_outreach_html(
    lead: Dict[str, Any],
    campaign: Dict[str, Any],
    subject: str,
    body: str,
    product_name: str = "",
    benefits: list | None = None,
    cta_url: str = "",
) -> str:
    """Legacy: buildOutreachHtml({ lead, campaign, subject, body,
                                    productName, benefits, ctaUrl })."""
    return body_to_html_paragraphs(body)
