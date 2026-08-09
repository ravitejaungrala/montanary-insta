"""Email permutator — generate format variants + verify.

Legacy: apps/nexus-legacy/server/services/emailPermutator.js
Exports (legacy): generatePermutations, prioritizeBySize, verifyEmailDisify,
                  domainFromCompany, isPersonalEmail
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


def clean(s: Optional[str]) -> str:
    """Legacy helper: lowercase, strip non-alpha."""
    return (s or "").lower().translate(str.maketrans("", "", " ,.-_'\""))


def generate_permutations(first_name: str, last_name: str, domain: str) -> List[str]:
    """Legacy: generatePermutations(firstName, lastName, domain).
    Returns variants like john@, j.smith@, john.smith@, jsmith@, etc."""
    return []


def prioritize_by_size(
    perms: List[str], first_name: str, last_name: str, domain: str, company_size: int
) -> List[str]:
    """Legacy: prioritizeBySize(perms, firstName, lastName, domain, companySize)."""
    return perms


async def verify_email_disify(email: str) -> Dict[str, Any]:
    """Legacy: verifyEmailDisify(email). Calls Disify SMTP-check service."""
    return {"valid": False, "deliverable": False, "stub": True}


def domain_from_company(name: str) -> str:
    """Legacy: domainFromCompany(name) — best-effort domain inference."""
    return ""


def is_personal_email(email: str) -> bool:
    """Legacy: isPersonalEmail(email). Gmail/Yahoo/Hotmail/Outlook etc."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    personal_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                        "live.com", "icloud.com", "aol.com", "protonmail.com"}
    return domain in personal_domains
