"""Symmetric encryption for LinkedIn secrets at rest.

Used to encrypt:
  • the captured LinkedIn session-cookie blob stored in
    gtm_linkedin_accounts.encrypted_cookies, and
  • the transient login password carried in a `linkedin_login` SQS job payload.

There is intentionally NO plaintext fallback: if `LINKEDIN_COOKIE_KEY` is not
configured, encrypt/decrypt raise — we never silently persist a session in the
clear (a leaked LinkedIn cookie = full account takeover).

Key: a 32-byte urlsafe-base64 Fernet key in the `LINKEDIN_COOKIE_KEY` env var
(ideally sourced from AWS Secrets Manager). Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet

from core.config import LINKEDIN_COOKIE_KEY


class LinkedInKeyMissing(RuntimeError):
    """Raised when LINKEDIN_COOKIE_KEY is not configured."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    key = (LINKEDIN_COOKIE_KEY or "").strip()
    if not key:
        raise LinkedInKeyMissing(
            "LINKEDIN_COOKIE_KEY is not set — refusing to handle LinkedIn "
            "secrets without an encryption key."
        )
    try:
        return Fernet(key.encode())
    except Exception as e:  # noqa: BLE001 — invalid key shape
        raise LinkedInKeyMissing(f"LINKEDIN_COOKIE_KEY is not a valid Fernet key: {e}") from e


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string → urlsafe token string (store this)."""
    if plaintext is None:
        raise ValueError("cannot encrypt None")
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a token produced by `encrypt` → original UTF-8 string."""
    if not token:
        raise ValueError("cannot decrypt empty token")
    return _cipher().decrypt(token.encode("ascii")).decode("utf-8")


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt raw bytes → Fernet token bytes. Used for the binary browser-profile
    tarball (which is not UTF-8 text, so `encrypt` can't take it)."""
    if data is None:
        raise ValueError("cannot encrypt None")
    return _cipher().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    """Decrypt a token produced by `encrypt_bytes` → original raw bytes."""
    if not token:
        raise ValueError("cannot decrypt empty token")
    return _cipher().decrypt(token)
