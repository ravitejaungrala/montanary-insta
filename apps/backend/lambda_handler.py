import json
import logging
import os
import traceback
import time
from datetime import datetime
import re

# ── Config hydrators ─────────────────────────────────────────────────────
# Lambda has a hard 4 KB limit on env vars. Long-tail config (OAuth secrets,
# API keys, Stripe price IDs, etc) lives outside the Lambda env panel and is
# hydrated into os.environ at cold-start. We support TWO sources:
#
#   1. S3 JSON   — preferred. Set CONFIG_S3_BUCKET + CONFIG_S3_KEY. The
#                  Lambda role needs s3:GetObject on the bucket.
#                  Almost-zero cost ($0 for a small file + a few cold
#                  starts/day; no per-secret monthly fee).
#
#   2. Secrets Manager (legacy)  — set APP_SECRETS_ARN. ~$0.50/mo per secret.
#                  Kept for backward compat with older deployments; new
#                  envs should use the S3 path.
#
# Both call `os.environ.setdefault()` so Lambda-panel env vars always win
# over hydrated values — useful for per-environment overrides.
#
# Local dev: neither runs. apps/backend/.env handles everything as today.
# ─────────────────────────────────────────────────────────────────────────

def _hydrate_config_from_s3() -> None:
    """Cold-start hook: fetch config JSON from S3 and merge into os.environ.

    Required env vars on the Lambda:
      CONFIG_S3_BUCKET  e.g. pipelyt-config
      CONFIG_S3_KEY     e.g. staging/config.json  (or production/config.json)

    Both must be set, otherwise this is a no-op (covers local dev where
    .env is the source of truth).
    """
    bucket = os.environ.get("CONFIG_S3_BUCKET", "").strip()
    key = os.environ.get("CONFIG_S3_KEY", "").strip()
    if not bucket or not key:
        return
    try:
        import boto3  # noqa: WPS433 — runtime import keeps cold-start light when unused
        client = boto3.client("s3")
        obj = client.get_object(Bucket=bucket, Key=key)
        payload = obj["Body"].read().decode("utf-8") or "{}"
        data = json.loads(payload)
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)):
                # setdefault — Lambda-panel env vars override hydrated values
                # so you can flip a single key per-env without re-uploading
                # the whole JSON.
                os.environ.setdefault(str(k), str(v))
        logging.getLogger("pipelyt.lambda").info(
            "S3 config hydrate ok — loaded %d keys from s3://%s/%s",
            len(data), bucket, key,
        )
    except Exception as exc:  # noqa: BLE001
        # Don't crash cold-start — log and continue with whatever env vars
        # are present so the Lambda can still serve health checks.
        logging.getLogger("pipelyt.lambda").error(
            "S3 config hydrate failed (bucket=%s key=%s): %s",
            bucket, key, exc,
        )


def _hydrate_secrets_from_aws() -> None:
    """Legacy: pull from AWS Secrets Manager. Only runs if APP_SECRETS_ARN
    is set on the Lambda. New environments should use _hydrate_config_from_s3
    instead — it costs almost nothing and avoids the per-secret monthly fee.
    """
    arn = os.environ.get("APP_SECRETS_ARN", "").strip()
    if not arn:
        return
    try:
        import boto3  # noqa: WPS433 — runtime import keeps cold-start light when unused
        client = boto3.client("secretsmanager")
        payload = client.get_secret_value(SecretId=arn).get("SecretString") or "{}"
        data = json.loads(payload)
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)):
                os.environ.setdefault(str(k), str(v))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("pipelyt.lambda").error(
            "Secrets Manager hydrate failed: %s", exc,
        )


# Run S3 hydrator first (preferred). Secrets Manager runs second so any
# values left unset by the S3 JSON can still come from a legacy secret if
# both are configured (unlikely in practice — pick one).
_hydrate_config_from_s3()
_hydrate_secrets_from_aws()
# ─────────────────────────────────────────────────────────────────────────

from mangum import Mangum
from main import app

# This is the handler that AWS Lambda will call
_handler = Mangum(app, lifespan="off")
logger = logging.getLogger("pipelyt.lambda")

# CORS allowed origins — env-driven so prod never ships with wildcard.
# Set FRONTEND_URL to a comma-joined list, e.g.:
#   FRONTEND_URL=https://app.pipelyt.ai,https://pipelyt.ai
# Falls back to "*" ONLY if no origins configured (dev convenience).
_raw_origins = (os.environ.get("FRONTEND_URL", "") or "").strip()
_ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

_TRUSTED_REGEX = r"^https?://([a-z0-9-]+\.)*(pipelyt\.ai|neuzenai\.com|amplifyapp\.com|localhost|127\.0\.0\.1)(:\d+)?$"

_CORS_BASE = {
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Requested-With,X-Scheduler-Secret",
    "Access-Control-Allow-Credentials": "true",
}


def _cors_headers_for(origin: str | None) -> dict:
    """Return CORS headers with the right Allow-Origin for this request.
    - If the request origin matches the trusted regex, echo it back.
    - Otherwise, if explicit origins are configured in FRONTEND_URL, use the first one.
    - Fallback to "*" only as a last resort.
    """
    if not origin:
        allow = _ALLOWED_ORIGINS[0] if _ALLOWED_ORIGINS else "*"
    elif re.match(_TRUSTED_REGEX, origin, re.IGNORECASE):
        allow = origin
    elif _ALLOWED_ORIGINS and origin in _ALLOWED_ORIGINS:
        allow = origin
    else:
        allow = _ALLOWED_ORIGINS[0] if _ALLOWED_ORIGINS else "*"
    
    return {"Access-Control-Allow-Origin": allow, **_CORS_BASE}


# Back-compat: keep CORS_HEADERS symbol but populate it dynamically per request.
# Default to first configured origin for error paths where we don't know the origin.
CORS_HEADERS = _cors_headers_for(None)

def handler(event, context):
    try:
        # 1. LOG INCOMING EVENT
        logger.info(f"Incoming Lambda event: {json.dumps(event)[:200]}...")

        # 1.5 ASYNC CAMPAIGN-TASK INVOCATIONS:
        # /start-campaign self-invokes this Lambda with `InvocationType=Event`
        # and a payload like {"campaign_task": True, "user_id": ..., "brief":
        # ..., "plan": [...], "targets": {...}}. We detect that payload here
        # and run `process_bulk_campaign` DIRECTLY without going through
        # Mangum/FastAPI. The user-facing /start-campaign returns instantly
        # while THIS invocation runs in the background for up to 15 min.
        #
        # Why this dispatch lives in lambda_handler and not as a FastAPI
        # background_task: BackgroundTasks in Mangum + Lambda deadlocks the
        # response (Lambda waits for the task to finish before returning,
        # so the user sees ERR_EMPTY_RESPONSE after 30s). The async invoke
        # decouples user response timing from heavy AI work entirely.
        if isinstance(event, dict) and event.get('campaign_task') is True:
            logger.info(
                f"[campaign_task] dispatch event user={event.get('user_id')} "
                f"slots={len(event.get('plan') or [])}"
            )
            try:
                from routers.content import process_bulk_campaign
                process_bulk_campaign(
                    user_id=event['user_id'],
                    brief=event['brief'],
                    plan=event['plan'] or [],
                    targets=event['targets'] or {},
                )
                logger.info(f"[campaign_task] done user={event.get('user_id')}")
                return {"statusCode": 200, "body": "campaign processed"}
            except Exception as exc:
                logger.error(f"[campaign_task] failed: {exc}", exc_info=True)
                # Returning a non-2xx from an async invoke goes to the DLQ
                # if configured; for now we just log and return 500 so AWS
                # records the failure in CloudWatch.
                return {"statusCode": 500, "body": str(exc)}

        # 2. AUTOMATED SCHEDULER RECOVERY:
        is_scheduled = event.get('source') == 'aws.events' or event.get('detail-type') == 'Scheduled Event'
        
        if is_scheduled:
            logger.info("EventBridge Schedule detected. Routing to /scheduler/process")
            scheduler_headers = {
                "accept": "*/*",
                "content-type": "application/json",
                "host": "scheduler.internal",
                **CORS_HEADERS,
            }
            # Forward the shared secret so the authenticated endpoint accepts the call.
            scheduler_secret = os.environ.get("SCHEDULER_SECRET")
            if scheduler_secret:
                scheduler_headers["x-scheduler-secret"] = scheduler_secret

            # Create a valid AWS Lambda Proxy v2 event for Mangum
            event = {
                "version": "2.0",
                "routeKey": "POST /scheduler/process",
                "rawPath": "/scheduler/process",
                "rawQueryString": "",
                "headers": scheduler_headers,
                "requestContext": {
                    "accountId": "anonymous",
                    "apiId": "scheduler",
                    "domainName": "scheduler.internal",
                    "domainPrefix": "scheduler",
                    "http": {
                        "method": "POST",
                        "path": "/scheduler/process",
                        "protocol": "HTTP/1.1",
                        "sourceIp": "127.0.0.1",
                        "userAgent": "AWS-EventBridge-Scheduler"
                    },
                    "requestId": context.aws_request_id,
                    "routeKey": "POST /scheduler/process",
                    "stage": "$default",
                    "time": datetime.utcnow().strftime("%d/%b/%Y:%H:%M:%S +0000"),
                    "timeEpoch": int(time.time() * 1000)
                },
                "body": "{}",
                "isBase64Encoded": False
            }

        request_path = event.get('rawPath', event.get('path', 'unknown'))
        logger.info(f"Lambda event received for path: {request_path}")
        
        # 3. ROBUST HTTP DETECTION (Supports Function URLs and API Gateway)
        is_http_v2 = isinstance(event, dict) and event.get("version") == "2.0" and "requestContext" in event
        is_http_v1 = isinstance(event, dict) and "httpMethod" in event and "path" in event
        
        if is_http_v1 or is_http_v2:
            # Mangum handles the conversion. If it fails, fallback to manual error.
            try:
                # Per-request CORS: echo the request Origin only if allow-listed.
                req_headers = event.get("headers") or {}
                # Headers can come in either case; normalize.
                req_origin = req_headers.get("origin") or req_headers.get("Origin")
                cors = _cors_headers_for(req_origin)
                response = _handler(event, context)
                if "headers" not in response: response["headers"] = {}
                response["headers"].update(cors)
                return response
            except Exception as mangum_err:
                logger.error(f"Mangum Adapter Failure: {mangum_err}")
                raise mangum_err
        else:
            logger.warning(f"UNRECOGNIZED TRIGGER DETECTED: {json.dumps(event)[:1000]}")
            return {
                "statusCode": 200, 
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "status": "Non-HTTP event ignored",
                    "source": event.get('source', 'unknown'),
                    "request_id": context.aws_request_id
                })
            }

    except Exception as e:
        logger.error(f"CRITICAL HANDLER ERROR: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "Critical Handler Error",
                "message": str(e),
                "request_id": context.aws_request_id
            })
        }
