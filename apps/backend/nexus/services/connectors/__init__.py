"""Mailbox connectors — let a workspace connect its OWN sending mailbox via
OAuth so outreach emails are sent FROM the customer's mailbox instead of the
shared Apollo/Resend sender.

Modules:
  outlook  — Microsoft Graph (Outlook / Microsoft 365 / personal MSA),
             delegated multi-tenant OAuth 2.0 authorization-code flow.
  gmail    — Google Workspace / Gmail, OAuth 2.0 authorization-code flow.

Each connector module exposes the SAME public API (build_auth_url,
exchange_code, get_valid_access_token, send_for_account, is_configured),
so the router + sequencer can treat providers uniformly via get_connector().
"""

from types import ModuleType
from typing import Optional

# Providers we know how to drive. Keep in sync with the modules above and the
# `provider` value stored on NexusConversationAccount.
SUPPORTED_PROVIDERS = ("outlook", "gmail")


def get_connector(provider: Optional[str]) -> Optional[ModuleType]:
    """Return the connector module for a provider name, or None if unknown.

    Imported lazily so a misconfigured provider can't break import of the
    package (and to avoid a circular import at module load time).
    """
    key = (provider or "").strip().lower()
    if key == "outlook":
        from nexus.services.connectors import outlook
        return outlook
    if key == "gmail":
        from nexus.services.connectors import gmail
        return gmail
    return None
