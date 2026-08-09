"""MJML / Pexels-backed email-template builder.

Legacy: apps/nexus-legacy/server/services/templateService.js
Exports (legacy): buildEmailHtml
"""
from __future__ import annotations
from typing import Any, Dict, List


async def fetch_pexels_image(query: str) -> str:
    """Legacy: fetchPexelsImage(query) — hero image URL."""
    return ""


def xml_esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def body_to_hook(body: str) -> str:
    """Legacy: bodyToHook(body) — first compelling sentence."""
    return (body or "").split(".")[0].strip()


def body_to_mjml_paragraphs(body: str) -> str:
    """Legacy: bodyToMjmlParagraphs(body)."""
    if not body:
        return ""
    return "\n".join(
        f"<mj-text>{xml_esc(p)}</mj-text>" for p in body.split("\n") if p.strip()
    )


def parse_benefit(benefit: Any, fallback_label: str = "") -> Dict[str, str]:
    """Legacy: parseBenefit(benefit, fallbackLabel)."""
    if isinstance(benefit, dict):
        return {"label": benefit.get("label", fallback_label),
                "body": benefit.get("body", "")}
    return {"label": fallback_label, "body": str(benefit or "")}


def build_mjml_template(
    subject: str,
    body: str,
    product_name: str = "",
    benefits: List[Any] | None = None,
    cta_text: str = "",
    cta_url: str = "",
    hero_image_url: str = "",
) -> str:
    """Legacy: buildMjmlTemplate({...}). Returns MJML string."""
    return ""


async def build_email_html(
    subject: str,
    body: str,
    product_name: str = "",
    benefits: List[Any] | None = None,
    cta_text: str = "",
    cta_url: str = "",
) -> str:
    """Legacy: buildEmailHtml({ subject, body, productName, benefits,
                                ctaText, ctaUrl }).
    Renders MJML → HTML. For now returns the body as plain HTML."""
    return body or ""
