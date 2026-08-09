"""Encrypted browser-profile blob store (S3).

Persists a Playwright persistent-context ``user_data_dir`` per LinkedIn account so
the SAME logged-in browser profile (cookies + localStorage + IndexedDB + cache +
preferences) is reused on every automation run — the industry-standard "capture
one profile, reuse it" model. The profile directory is tar.gz'd, Fernet-encrypted
(``crypto.encrypt_bytes``), and stored as a single object in S3.

NO passwords are ever stored — only the resulting session profile. Every function
is best-effort and never raises into the worker: a missing S3 config / missing
profile just means "no profile to reuse" (the caller falls back to a fresh login).

S3 layout: ``linkedin-profiles/<account_id>/profile.tar.gz.enc``
"""
from __future__ import annotations

import io
import logging
import os
import tarfile
import tempfile
from typing import Optional

from core.s3_utils import get_s3_client

from . import crypto

logger = logging.getLogger("pipelyt.gtm.linkedin.profile_store")

_PREFIX = "linkedin-profiles"
# DEDICATED private bucket for LinkedIn session profiles — deliberately SEPARATE
# from the general uploads bucket (S3_BUCKET_NAME), which may be public. Set
# GTM_LINKEDIN_PROFILE_BUCKET to a private bucket to enable S3 storage.
_BUCKET_ENV = "GTM_LINKEDIN_PROFILE_BUCKET"
# When no dedicated bucket is configured (local dev / tests) we store the encrypted
# blob on the local filesystem — we NEVER fall back to the public uploads bucket.
# Set GTM_LINKEDIN_PROFILE_DIR to control the local location.
_LOCAL_DIR_ENV = "GTM_LINKEDIN_PROFILE_DIR"


def s3_key(account_id: int) -> str:
    """Deterministic key for an account's encrypted profile blob (S3 key / local path)."""
    return f"{_PREFIX}/{int(account_id)}/profile.tar.gz.enc"


def _bucket() -> Optional[str]:
    return (os.getenv(_BUCKET_ENV) or "").strip() or None


def _client():
    """boto3 S3 client. Reuses the shared builder; falls back to a direct client so
    the profile bucket works even if the general S3_BUCKET_NAME isn't configured."""
    c = get_s3_client()
    if c is not None:
        return c
    try:
        import boto3  # lazy
        from core.config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
        kw: dict = {"region_name": AWS_REGION or "us-east-1"}
        if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME") and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            kw["aws_access_key_id"] = AWS_ACCESS_KEY_ID
            kw["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
        return boto3.client("s3", **kw)
    except Exception:  # noqa: BLE001
        return None


def _local_path(key: str) -> str:
    base = os.getenv(_LOCAL_DIR_ENV) or os.path.join(tempfile.gettempdir(), "li_profiles")
    return os.path.join(base, key)


def _backend_label() -> str:
    """Human-readable storage backend for logs — 's3:<bucket>' or 'local:<dir>'."""
    b = _bucket()
    if b:
        return f"s3:{b}"
    return f"local:{os.getenv(_LOCAL_DIR_ENV) or os.path.join(tempfile.gettempdir(), 'li_profiles')}"


def _put_blob(key: str, data: bytes) -> bool:
    """Store bytes at `key` — the dedicated S3 bucket when configured, else local."""
    bucket = _bucket()
    s3 = _client() if bucket else None
    if s3 and bucket:
        s3.put_object(Bucket=bucket, Key=key, Body=data)
        return True
    p = _local_path(key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return True


def _get_blob(key: str) -> Optional[bytes]:
    """Fetch bytes at `key` — the dedicated S3 bucket when configured, else local. None if absent."""
    bucket = _bucket()
    s3 = _client() if bucket else None
    if s3 and bucket:
        try:
            return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception:  # noqa: BLE001 — NoSuchKey / 404 / transient
            return None
    p = _local_path(key)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    return None


def _tar_dir_to_bytes(dir_path: str) -> bytes:
    buf = io.BytesIO()
    # arcname="." → the archive holds the DIRECTORY CONTENTS, so extraction
    # restores them straight into the target user_data_dir.
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(dir_path, arcname=".")
    return buf.getvalue()


def _extract_bytes_to_dir(blob: bytes, dir_path: str) -> None:
    os.makedirs(dir_path, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        # We author these archives ourselves (trusted), but use the "data" filter
        # when available (py3.12+) to guard against path traversal regardless.
        try:
            tar.extractall(dir_path, filter="data")  # type: ignore[call-arg]
        except TypeError:
            tar.extractall(dir_path)


def save_profile(account_id: int, user_data_dir: str) -> Optional[str]:
    """tar.gz + encrypt ``user_data_dir`` and store it (S3 or local). Returns the
    storage key on success, else None (best-effort — never raises into the worker)."""
    if not user_data_dir or not os.path.isdir(user_data_dir):
        logger.warning("save_profile: user_data_dir missing for account %s: %s", account_id, user_data_dir)
        return None
    key = s3_key(account_id)
    try:
        enc = crypto.encrypt_bytes(_tar_dir_to_bytes(user_data_dir))
        _put_blob(key, enc)
        logger.info("save_profile: stored profile for account %s (%d bytes) via %s → %s",
                    account_id, len(enc), _backend_label(), key)
        return key
    except Exception:  # noqa: BLE001 — best-effort; a failed save must not kill the job
        logger.exception("save_profile: failed for account %s", account_id)
        return None


def load_profile(account_id: int, dest_dir: str) -> bool:
    """Fetch + decrypt + extract the account's profile into ``dest_dir``.
    Returns True if a profile was restored, False if none exists yet / on error
    (the caller then starts from a fresh/logged-out browser)."""
    enc = _get_blob(s3_key(account_id))
    if enc is None:
        logger.info("load_profile: no stored profile for account %s", account_id)
        return False
    try:
        _extract_bytes_to_dir(crypto.decrypt_bytes(enc), dest_dir)
        logger.info("load_profile: restored profile for account %s → %s", account_id, dest_dir)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("load_profile: failed to decrypt/extract for account %s", account_id)
        return False


def delete_profile(account_id: int) -> None:
    """Remove an account's stored profile (e.g. on disconnect). Best-effort."""
    key = s3_key(account_id)
    bucket = _bucket()
    try:
        if bucket:
            s3 = _client()
            if s3:
                s3.delete_object(Bucket=bucket, Key=key)
        else:
            p = _local_path(key)
            if os.path.exists(p):
                os.remove(p)
    except Exception:  # noqa: BLE001
        logger.warning("delete_profile: failed for account %s", account_id)
