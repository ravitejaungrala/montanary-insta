"""GTM LinkedIn — SQS publish for jobs.

The durable record is always the `gtm_linkedin_jobs` row (created by
`auth.enqueue_job`). In AWS we ALSO push a tiny `{"job_id": N}` message to the
FIFO queue so the worker Lambda is triggered; `MessageGroupId = account_id`
serializes all of one account's jobs (one browser session per account at a time).

No-op when `GTM_LINKEDIN_SQS_URL` is unset (local / pre-deploy) — the job row
still exists and is run inline by the test-drive script. So this is safe to call
unconditionally from the enqueue path.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from core.config import GTM_LINKEDIN_SQS_URL
from gtm.linkedin.logs import setup as _setup_logs

_setup_logs()
logger = logging.getLogger("pipelyt.gtm_linkedin.queue")

_client = None


def _sqs():
    global _client
    if _client is None:
        import boto3  # lazy — keeps boto3 out of import paths that never publish
        _client = boto3.client("sqs")
    return _client


def publish_job(*, job_id: int, account_id: Optional[int]) -> bool:
    """Push {job_id} to SQS. Returns True if published, False if no queue is
    configured or the publish failed (best-effort — the DB row is the source of
    truth and the scheduler/worker can still pick it up)."""
    if not GTM_LINKEDIN_SQS_URL:
        logger.warning(
            "SQS: job %s NOT sent to a queue — GTM_LINKEDIN_SQS_URL is EMPTY on this Lambda. "
            "The job row exists in the DB but no worker will run it. Set GTM_LINKEDIN_SQS_URL "
            "on the app Lambda to the FIFO queue URL.", job_id,
        )
        return False
    try:
        kwargs = {
            "QueueUrl": GTM_LINKEDIN_SQS_URL,
            "MessageBody": json.dumps({"job_id": int(job_id)}),
        }
        if GTM_LINKEDIN_SQS_URL.endswith(".fifo"):
            # One ordered lane per account → never two browsers on one account.
            kwargs["MessageGroupId"] = str(account_id or "0")
            kwargs["MessageDeduplicationId"] = str(job_id)
        _sqs().send_message(**kwargs)
        logger.info("SQS: pushed job %s (account %s) to the queue OK", job_id, account_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(
            "SQS: FAILED to push job %s (account %s): %s. Check the app Lambda's IAM perms "
            "(sqs:SendMessage) and that GTM_LINKEDIN_SQS_URL points at the right queue.",
            job_id, account_id, e, exc_info=True,
        )
        return False
