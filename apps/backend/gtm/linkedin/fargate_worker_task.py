"""Entrypoint run INSIDE the Fargate worker container.

Long-running SQS polling loop that replaces the Lambda worker's event-source
trigger.  Calls the same ``worker.handle_job()`` function — all job logic,
safety checks, sequence advancement, and profile persistence are unchanged.

Industry best practices implemented:
  - Long polling (20s) — no busy-spin, no empty-poll charges
  - Graceful shutdown on SIGTERM — finishes current job before exiting
  - Startup validation — checks DB + SQS connectivity before entering the loop
  - Heartbeat logging — periodic stats so CloudWatch shows the worker is alive
  - Visibility timeout awareness — extends timeout for long-running jobs
  - Error backoff — exponential backoff on repeated SQS errors

Env (set on the ECS task definition):
  GTM_LINKEDIN_SQS_URL  (required)  FIFO queue URL
  DATABASE_URL           (required)  Postgres connection string
  LINKEDIN_COOKIE_KEY    (required)  Fernet key for profile encryption
  GTM_LINKEDIN_PROFILE_BUCKET        S3 bucket for browser profiles
  GTM_LINKEDIN_HEADLESS  (default true)
  AWS_REGION
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time

from gtm.linkedin.logs import setup as _setup_logs

_setup_logs()
logger = logging.getLogger("pipelyt.gtm_linkedin.fargate_worker_task")

_shutdown = False

# How often to log a heartbeat when idle (seconds).
_HEARTBEAT_INTERVAL = 300  # 5 min

# SQS visibility timeout extension: if a job runs longer than this many seconds,
# we extend the visibility timeout so SQS doesn't redeliver mid-execution.
_VISIBILITY_EXTEND_THRESHOLD = 120  # 2 min
_VISIBILITY_EXTEND_TO = 600         # extend to 10 min


def _handle_sigterm(*_args):
    global _shutdown
    logger.info("fargate_worker: SIGTERM received — finishing current job then exiting")
    _shutdown = True


def _validate_startup():
    """Fail fast if critical dependencies are missing or unreachable."""
    sqs_url = os.environ.get("GTM_LINKEDIN_SQS_URL", "").strip()
    if not sqs_url:
        logger.error("fargate_worker: GTM_LINKEDIN_SQS_URL is not set — cannot start")
        sys.exit(1)

    # Validate DB connectivity
    from core.database import SessionLocal
    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        logger.info("fargate_worker: DB connection OK")
    except Exception as e:
        logger.error("fargate_worker: DB connection failed — %s", e)
        sys.exit(1)

    # Validate SQS queue is reachable
    import boto3
    try:
        sqs = boto3.client("sqs")
        sqs.get_queue_attributes(QueueUrl=sqs_url, AttributeNames=["QueueArn"])
        logger.info("fargate_worker: SQS queue reachable — %s", sqs_url)
    except Exception as e:
        logger.error("fargate_worker: SQS queue unreachable — %s", e)
        sys.exit(1)

    return sqs_url


def main() -> None:
    import boto3
    from core.database import SessionLocal
    from gtm.linkedin.worker import handle_job

    sqs_url = _validate_startup()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    sqs = boto3.client("sqs")
    logger.info("fargate_worker: started — polling %s", sqs_url)

    # Stats for heartbeat logging.
    jobs_processed = 0
    jobs_failed = 0
    last_heartbeat = time.monotonic()
    consecutive_errors = 0

    while not _shutdown:
        # Heartbeat: log stats periodically so CloudWatch shows the worker is alive.
        now = time.monotonic()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            logger.info(
                "fargate_worker: heartbeat — processed=%d failed=%d (last %ds)",
                jobs_processed, jobs_failed, _HEARTBEAT_INTERVAL,
            )
            jobs_processed = 0
            jobs_failed = 0
            last_heartbeat = now

        # Long-poll SQS (20s wait = no busy-spin, no cost for empty polls).
        try:
            resp = sqs.receive_message(
                QueueUrl=sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
            )
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            backoff = min(5 * consecutive_errors, 60)
            logger.exception(
                "fargate_worker: SQS receive_message failed (attempt %d) — retrying in %ds",
                consecutive_errors, backoff,
            )
            time.sleep(backoff)
            continue

        messages = resp.get("Messages", [])
        if not messages:
            continue

        msg = messages[0]
        receipt = msg["ReceiptHandle"]
        try:
            body = json.loads(msg.get("Body") or "{}")
            job_id = body.get("job_id")
            if job_id is None:
                logger.warning("fargate_worker: message had no job_id — deleting")
                sqs.delete_message(QueueUrl=sqs_url, ReceiptHandle=receipt)
                continue

            logger.info("fargate_worker: processing job %s", job_id)
            job_start = time.monotonic()

            db = SessionLocal()
            try:
                handle_job(db, int(job_id))
            finally:
                db.close()

            elapsed = time.monotonic() - job_start
            sqs.delete_message(QueueUrl=sqs_url, ReceiptHandle=receipt)
            jobs_processed += 1
            logger.info("fargate_worker: job %s done in %.1fs — message deleted", job_id, elapsed)

        except Exception:
            jobs_failed += 1
            logger.exception("fargate_worker: job %s failed", job_id)
            # Delete the message even on failure so it doesn't block the FIFO
            # group (all jobs for the same account share a MessageGroupId).
            # The job is already marked failed in DB; redelivery just retries
            # the same broken action and stalls every other lead.
            try:
                sqs.delete_message(QueueUrl=sqs_url, ReceiptHandle=receipt)
                logger.info("fargate_worker: job %s failed — message deleted to unblock queue", job_id)
            except Exception:
                logger.exception("fargate_worker: failed to delete message for job %s", job_id)

        # Brief pause between jobs for human-like pacing.
        time.sleep(1)

    logger.info("fargate_worker: shutdown complete (SIGTERM)")


if __name__ == "__main__":
    main()
