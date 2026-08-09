"""Multi-step email-finding waterfall.

Legacy: apps/nexus-legacy/server/services/emailWaterfall.js
Exports (legacy): findEmail, step1Website, step2GitHub, step3Hunter,
                  step4Apollo, step5Permutator
"""
from __future__ import annotations
from typing import Any, Dict, Optional


def perms(first_name: str, last_name: str, domain: str) -> list[str]:
    """Legacy: perms(firstName, lastName, domain)."""
    return []


def is_personal(email: str) -> bool:
    from .email_permutator import is_personal_email
    return is_personal_email(email)


async def step1_website(
    first_name: str, last_name: str, domain: str
) -> Optional[Dict[str, Any]]:
    """Legacy: step1Website(...). Scrape company website for mailto links."""
    return None


async def step2_github(
    first_name: str, last_name: str, company: str
) -> Optional[Dict[str, Any]]:
    """Legacy: step2GitHub(...). Search GitHub commits / profile."""
    return None


async def step3_hunter(
    first_name: str, last_name: str, domain: str
) -> Optional[Dict[str, Any]]:
    """Legacy: step3Hunter(...). Hunter.io email-finder."""
    return None


async def step4_apollo(
    first_name: str, last_name: str, company: str
) -> Optional[Dict[str, Any]]:
    """Legacy: step4Apollo(...). Apollo.io person lookup."""
    return None


async def step5_permutator(
    first_name: str, last_name: str, domain: str
) -> Optional[Dict[str, Any]]:
    """Legacy: step5Permutator(...). Email-format permutations + Disify."""
    return None


async def find_email(
    first_name: str, last_name: str, company: str = "", domain: str = ""
) -> Dict[str, Any]:
    """Legacy: findEmail(firstName, lastName, company, domain).
    Runs the 5-step waterfall and returns the first match."""
    return {"email": None, "source": None, "confidence": 0.0, "stub": True}
