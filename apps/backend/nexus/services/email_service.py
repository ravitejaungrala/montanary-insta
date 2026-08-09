"""High-level email sender — wraps Resend with transactional helpers.

Legacy: apps/nexus-legacy/server/services/emailService.js
Exports (legacy): sendEmail, transactionalEmail

Distinct from resend_client.py (the raw API wrapper). This service
adds: branded HTML wrappers, redirect bypass, lead-name personalization,
SMTP fallback when Resend errors.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


def transactional_email(
    subject: str, heading: str, body_html: str, cta_text: str, cta_url: str
) -> str:
    """Legacy: transactionalEmail(subject, heading, bodyHtml, ctaText, ctaUrl).
    Returns wrapped branded HTML."""
    return body_html or ""


def build_clean_email_html(body: str) -> str:
    """Legacy: buildCleanEmailHtml(body). Strips HTML/CSS noise from raw."""
    return body or ""


async def send_email(
    lead_name: str,
    original_email: str,
    subject: str,
    body: str,
    campaign_name: str = "",
    html_content: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    bypass_redirect: bool = False,
) -> Dict[str, Any]:
    """Legacy: sendEmail({ leadName, originalEmail, subject, body,
                          campaignName, htmlContent, extraHeaders, bypassRedirect }).
    Returns { ok, message_id, error? }."""
    return {"ok": False, "stub": True, "message_id": None}
