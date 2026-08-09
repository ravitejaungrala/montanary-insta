"""Deterministic signature for outbound auto-replies.

Kept in one place so the poller (`ms_mail_sync`), the connector sync
(`connectors/inbound_sync`), and the manual `/nexus/inbound/reply-to-lead`
endpoint all sign replies identically. The reply model ends on "Best," and is
told not to add a signature block, so the sender name is otherwise inconsistent.
"""

from __future__ import annotations


def ensure_signature(reply: str, brand: str) -> str:
    """Ensure the reply is signed with the brand/product name on its own final
    line (after the sign-off). The reply model ends on "Best," and is told not
    to add a signature, so the sender name is inconsistent — this makes it
    deterministic. Idempotent: if the reply already ends with the brand name,
    it's returned unchanged (so a reply the model happened to sign isn't
    double-signed)."""
    if not reply or not brand:
        return reply
    body = reply.rstrip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if lines and lines[-1].casefold() == brand.strip().casefold():
        return body
    return body + "\n" + brand.strip()
