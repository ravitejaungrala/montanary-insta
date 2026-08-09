"""Per-request API cost ledger.

One `CostLedger` per /generate-content request. Records:
  • Every Gemini call (model, tokens, cost)
  • Every OpenAI text call (Agent 1 art director, GPT-5)
  • Every OpenAI image call (Agent 2 gpt-image-2)

At end of request:
  1. Writes one row to a local CSV: apps/backend/api_cost_ledger.csv
  2. Uploads the CSV to S3: s3://<bucket>/cost_ledgers/api_cost_ledger.csv

Instrumented call sites publish into the current-request ledger via a
thread-local (contextvar) so signatures don't have to change.

Pricing tables reflect 2026 official rates (see PRICING_LAST_UPDATED).
Update the tables when providers change pricing.
"""
from __future__ import annotations

import contextvars
import csv
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pipelyt.cost_ledger")

PRICING_LAST_UPDATED = "2026-07-07"

# ═══════════════════════════════════════════════════════════════════════
# Environment detection — a single unified CSV is used for local, staging,
# and prod; each row carries an ENVIRONMENT column so the sheet can be
# filtered / pivoted per env in Excel.
#
# Detection precedence:
#   1. PIPELYT_ENV env var override (values: 'local' | 'staging' | 'prod')
#   2. AWS_LAMBDA_FUNCTION_NAME:
#        • 'pipelyt-stagging-backend' (or contains 'staging'/'stagging') → staging
#        • 'pipelyt-backend' (or any other Lambda name)                  → prod
#   3. No Lambda env var → local
# ═══════════════════════════════════════════════════════════════════════
_LAMBDA_FN_NAME = (os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or "").strip().lower()
_ENV_OVERRIDE = (os.environ.get("PIPELYT_ENV") or "").strip().lower()

if _ENV_OVERRIDE in ("local", "staging", "prod"):
    _ENV_LABEL = _ENV_OVERRIDE
elif _LAMBDA_FN_NAME:
    if "staging" in _LAMBDA_FN_NAME or "stagging" in _LAMBDA_FN_NAME:
        _ENV_LABEL = "staging"
    else:
        _ENV_LABEL = "prod"
else:
    _ENV_LABEL = "local"

# ═══════════════════════════════════════════════════════════════════════
# PRICING TABLES (USD)
# ═══════════════════════════════════════════════════════════════════════
# Only includes models that the tracked pipeline paths (magic image +
# carousel + text agents) actually invoke. Image Agent v4 (Gemini image
# fallback) is intentionally EXCLUDED because we don't instrument it and
# the user doesn't want it appearing in the per-agent CSV.
#
# NOTE on the "gemini-flash-lite-latest" alias — Google has this alias
# resolving to Gemini 3.1 Flash-Lite (as of 2026-07-07 the latest stable
# lite model). Pricing therefore matches gemini-3.1-flash-lite:
#   $0.25 / 1M input tokens
#   $1.50 / 1M output tokens
# If Google hot-swaps the alias to a different version in the future,
# update this table (they email 2 weeks before rolling the alias).
GEMINI_PRICING: dict[str, dict[str, float]] = {
    # Primary text model — alias currently resolves to 3.1-flash-lite
    "gemini-flash-lite-latest":  {"input_per_1m": 0.25, "output_per_1m": 1.50},
    "gemini-3.1-flash-lite":     {"input_per_1m": 0.25, "output_per_1m": 1.50},
    # Fallback text model — older stable version used when the primary's
    # alias hot-swaps to a broken preview or the primary exhausts its
    # retry attempts. Priced at the older (cheaper) tier.
    "gemini-2.5-flash-lite":     {"input_per_1m": 0.10, "output_per_1m": 0.40},
}

# OpenAI — per 1M tokens for text; per-image for gpt-image-2
OPENAI_PRICING: dict[str, dict[str, float]] = {
    # Text models (chat completions) — used by Magic Agent 1 (art director)
    # and the carousel director. Kept the alternate tiers in case env vars
    # switch AGENT1_MODEL to a mini/nano SKU.
    # gpt-5.1 = current primary for Agent 1 (adaptive reasoning, better
    #           constraint-following on the Physical Product playbook).
    # gpt-5   = fallback (older stable version, same pricing).
    "gpt-5.1":       {"input_per_1m": 5.00,  "output_per_1m": 30.00},
    "gpt-5":         {"input_per_1m": 5.00,  "output_per_1m": 30.00},
    "gpt-5-mini":    {"input_per_1m": 0.75,  "output_per_1m": 4.50},
    "gpt-5-nano":    {"input_per_1m": 0.20,  "output_per_1m": 1.25},
    # Image model — used by Magic Agent 2 + carousel slide rendering.
    # gpt-image-2 at high quality 1024x1024 = $0.211/image plus per-1M
    # token costs for the text prompt input and any reference-image tokens.
    "gpt-image-2":   {
        "per_image_high":       0.211,
        "per_image_medium":     0.053,
        "per_image_low":        0.006,
        "text_input_per_1m":    5.00,
        "image_input_per_1m":   8.00,
    },
}

# ═══════════════════════════════════════════════════════════════════════
# Per-request ledger
# ═══════════════════════════════════════════════════════════════════════
_current_ledger: contextvars.ContextVar[Optional["CostLedger"]] = (
    contextvars.ContextVar("current_cost_ledger", default=None)
)


class CostLedger:
    """One accumulator per /generate-content request. Not thread-safe on
    its own, but each request has its own instance and instrumented calls
    fire from the request thread (or a ThreadPool whose parent context we
    propagate via context.copy())."""

    def __init__(
        self,
        *,
        user_id: Any = None,
        user_email: str = "",
        business_dna: str = "",
        post_type: str = "",
        trace_id: str = "",
        image_style: str = "",
    ):
        self.started_at = datetime.now(timezone.utc)
        self.trace_id = trace_id or f"led_{uuid.uuid4().hex[:10]}"
        self.user_id = str(user_id) if user_id is not None else ""
        self.user_email = user_email or ""
        self.business_dna = business_dna or ""
        self.post_type = post_type or ""
        # User-selected style (or "auto"). Logged in the [cost_ledger]
        # summary line for traceability. Deliberately NOT surfaced as a
        # CSV column so the analytical schema stays at 50 cols.
        self.image_style = (image_style or "auto") or "auto"
        self._lock = threading.Lock()

        # Gemini text agents (refine / research / content / cultural / critic
        # / dna extraction / offline researcher fallback / etc.)
        self.gemini_calls = 0
        self.gemini_models: set[str] = set()
        self.gemini_input_tokens = 0
        self.gemini_output_tokens = 0
        self.gemini_cost = 0.0

        # OpenAI text (Agent 1 = GPT-5 art director; possibly others)
        self.openai_text_calls = 0
        self.openai_text_models: set[str] = set()
        self.openai_text_input_tokens = 0
        self.openai_text_output_tokens = 0
        self.openai_text_cost = 0.0

        # OpenAI image (Agent 2 = gpt-image-2, and any other image renders)
        self.openai_image_calls = 0
        self.images_generated = 0
        self.openai_image_text_input_tokens = 0
        self.openai_image_image_input_tokens = 0
        self.openai_image_cost = 0.0

        # Per-agent breakdown for the detailed CSV (agent_time_cost_ledger.csv).
        # Keyed by canonical slot name (see AGENT_SLOTS). Each entry accumulates
        # calls, tokens, cost, and processing time across possibly-multiple
        # invocations of the same agent (e.g. Agent 1 fires 3x for 3 variants).
        self.agent_stats: dict[str, dict[str, Any]] = {
            slot: {
                "calls":         0,
                "model":         "",       # last-seen model name for this slot
                "input_tokens":  0,
                "output_tokens": 0,
                "images":        0,        # only meaningful for image slot
                "time_sec":      0.0,
                "cost":          0.0,
            }
            for slot in AGENT_SLOTS
        }

    # ---- Recording API ------------------------------------------------
    def _tally_agent(
        self, slot: str, *, model: str, input_tokens: int, output_tokens: int,
        images: int, time_sec: float, cost: float,
    ) -> None:
        """Add call metrics to the given canonical agent slot. No-op if slot
        is not a known AGENT_SLOTS entry (misspelled callers won't crash).

        TIME_SEC is tracked as MAX across calls to reflect WALL-CLOCK time
        the user actually waited. Rationale: for parallel calls (Agent 1
        / Agent 2 fan out across 3 variants in a ThreadPoolExecutor),
        summing worker times would double-count concurrent work. For the
        rare sequential retry (e.g. RESEARCHER grounded → offline
        fallback), max slightly underestimates but the error is small.
        Tokens / cost / calls / images are still additive since those
        DO scale linearly with concurrent work.
        """
        if slot not in self.agent_stats:
            logger.warning(f"[cost_ledger] unknown agent slot {slot!r} — ignoring")
            return
        s = self.agent_stats[slot]
        s["calls"] += 1
        if model:
            s["model"] = model
        s["input_tokens"] += int(input_tokens or 0)
        s["output_tokens"] += int(output_tokens or 0)
        s["images"] += int(images or 0)
        # WALL-CLOCK: max, not sum. Parallel workers get the longest single
        # call, which is what the user actually waited for.
        s["time_sec"] = max(s["time_sec"], float(time_sec or 0.0))
        s["cost"] += float(cost or 0.0)

    def record_gemini_text(
        self, *, model: str, input_tokens: int = 0, output_tokens: int = 0,
        agent_slot: str = "", time_sec: float = 0.0,
    ) -> None:
        cost = _gemini_text_cost(model, input_tokens, output_tokens)
        with self._lock:
            self.gemini_calls += 1
            self.gemini_models.add(model)
            self.gemini_input_tokens += int(input_tokens or 0)
            self.gemini_output_tokens += int(output_tokens or 0)
            self.gemini_cost += cost
            if agent_slot:
                self._tally_agent(
                    agent_slot, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    images=0, time_sec=time_sec, cost=cost,
                )

    def record_openai_text(
        self, *, model: str, input_tokens: int = 0, output_tokens: int = 0,
        agent_slot: str = "ART_DIRECTOR", time_sec: float = 0.0,
    ) -> None:
        cost = _openai_text_cost(model, input_tokens, output_tokens)
        with self._lock:
            self.openai_text_calls += 1
            self.openai_text_models.add(model)
            self.openai_text_input_tokens += int(input_tokens or 0)
            self.openai_text_output_tokens += int(output_tokens or 0)
            self.openai_text_cost += cost
            self._tally_agent(
                agent_slot, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                images=0, time_sec=time_sec, cost=cost,
            )

    def record_openai_image(
        self,
        *,
        model: str = "gpt-image-2",
        quality: str = "high",
        images: int = 1,
        text_input_tokens: int = 0,
        image_input_tokens: int = 0,
        agent_slot: str = "IMAGE_GENERATOR",
        time_sec: float = 0.0,
    ) -> None:
        cost = _openai_image_cost(
            model, quality, images, text_input_tokens, image_input_tokens
        )
        with self._lock:
            self.openai_image_calls += 1
            self.images_generated += int(images or 0)
            self.openai_image_text_input_tokens += int(text_input_tokens or 0)
            self.openai_image_image_input_tokens += int(image_input_tokens or 0)
            self.openai_image_cost += cost
            # For images, treat text_input + image_input as the "input tokens"
            # aggregate on the per-agent line so users see a single number.
            self._tally_agent(
                agent_slot, model=model,
                input_tokens=(int(text_input_tokens or 0) + int(image_input_tokens or 0)),
                output_tokens=0,
                images=images, time_sec=time_sec, cost=cost,
            )

    # ---- Totals -------------------------------------------------------
    @property
    def total_cost_usd(self) -> float:
        return (
            self.gemini_cost
            + self.openai_text_cost
            + self.openai_image_cost
        )


# ═══════════════════════════════════════════════════════════════════════
# Cost formulas
# ═══════════════════════════════════════════════════════════════════════
def _gemini_text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = GEMINI_PRICING.get(model)
    if not p or "output_per_1m" not in p:
        # Fallback to flash-lite pricing for unknown models
        p = GEMINI_PRICING["gemini-flash-lite-latest"]
    return (
        (int(input_tokens or 0) / 1_000_000) * p["input_per_1m"]
        + (int(output_tokens or 0) / 1_000_000) * p["output_per_1m"]
    )


def _openai_text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = OPENAI_PRICING.get(model, OPENAI_PRICING["gpt-5"])
    return (
        (int(input_tokens or 0) / 1_000_000) * p["input_per_1m"]
        + (int(output_tokens or 0) / 1_000_000) * p["output_per_1m"]
    )


def _openai_image_cost(
    model: str, quality: str, images: int, text_input_tokens: int, image_input_tokens: int
) -> float:
    p = OPENAI_PRICING.get(model, OPENAI_PRICING["gpt-image-2"])
    quality_key = f"per_image_{quality.lower()}" if quality else "per_image_high"
    per_image = p.get(quality_key, p.get("per_image_high", 0.211))
    text_in_cost = (int(text_input_tokens or 0) / 1_000_000) * p.get("text_input_per_1m", 5.00)
    img_in_cost = (int(image_input_tokens or 0) / 1_000_000) * p.get("image_input_per_1m", 8.00)
    return int(images or 0) * per_image + text_in_cost + img_in_cost


# ═══════════════════════════════════════════════════════════════════════
# Context helpers (used by instrumented call sites)
# ═══════════════════════════════════════════════════════════════════════
def start_ledger(
    *,
    user_id: Any = None,
    user_email: str = "",
    business_dna: str = "",
    post_type: str = "",
    trace_id: str = "",
    image_style: str = "",
) -> CostLedger:
    """Create and set a ledger for the current request context. Call once
    per /generate-content request at entry."""
    ledger = CostLedger(
        user_id=user_id,
        user_email=user_email,
        business_dna=business_dna,
        post_type=post_type,
        trace_id=trace_id,
        image_style=image_style,
    )
    _current_ledger.set(ledger)
    return ledger


def get_current_ledger() -> Optional[CostLedger]:
    """Return the ledger for the current request context (or None if
    none active — instrumented calls silently no-op in that case)."""
    return _current_ledger.get()


# ═══════════════════════════════════════════════════════════════════════
# CSV write + S3 upload
# ═══════════════════════════════════════════════════════════════════════
# SINGLE unified CSV across local / staging / prod. Each row carries an
# ENVIRONMENT column so the sheet can be filtered per-env in Excel. The
# filename has no env suffix so all three environments append to the
# same S3 object.
#
# Before each write, the current S3 file is downloaded to the local
# working path (`_sync_local_from_s3`). This is what makes the append
# safe across ephemeral Lambda filesystems: without it, each Lambda
# cold-start would start with an empty file and its upload would
# clobber the entire history.
_AGENT_CSV_BASENAME = "pipelyt_post_generation_time_and_cost.csv"

# AWS Lambda mounts the deployment package at /var/task/ READ-ONLY. Only
# /tmp/ is writable inside a Lambda container. When we detect a Lambda
# runtime, switch the local working path to /tmp so download+append+upload
# actually succeeds. Local dev / containerised hosts keep writing next to
# the code (apps/backend/pipelyt_post_generation_time_and_cost.csv).
if _LAMBDA_FN_NAME:
    AGENT_CSV_PATH = Path("/tmp") / _AGENT_CSV_BASENAME
else:
    AGENT_CSV_PATH = Path(__file__).resolve().parent.parent / _AGENT_CSV_BASENAME
AGENT_S3_CSV_KEY = f"cost_ledgers/{_AGENT_CSV_BASENAME}"

logger.info(
    f"[cost_ledger] init: env={_ENV_LABEL!r} lambda={_LAMBDA_FN_NAME!r} "
    f"agent_csv_path={AGENT_CSV_PATH}"
)

# Canonical agent slots (each becomes 6 columns in the CSV).
# Order chosen to mirror pipeline stage order for readability.
# Intentionally EXCLUDES:
#   • IMAGE_CHECK (audit path, uses gemini-2.5-flash, not on hot path)
#   • Image Agent v4 (Gemini image fallback — user doesn't want it tracked)
AGENT_SLOTS = [
    "REFINER",          # Gemini flash-lite-latest — refines the raw brief
    "CULTURAL",         # Gemini flash-lite-latest — cultural calendar / holidays
    "RESEARCHER",       # Gemini flash-lite-latest — grounded web research
    "COPYWRITER",       # Gemini flash-lite-latest — writes platform captions
    "ART_DIRECTOR",     # OpenAI gpt-5 — Agent 1 (magic) OR Carousel director
    "IMAGE_GENERATOR",  # OpenAI gpt-image-2 — Agent 2 (magic) OR carousel slide renderer
    "CRITIC",           # Gemini flash-lite-latest — final quality critique
]

# The legacy api_cost_ledger.csv was retired — the per-agent CSV below
# is the sole source of truth now. No CSV_HEADER / CSV_PATH constants
# remain because that file is no longer written or uploaded.


# Per-agent breakdown CSV — one row per campaign, 6 columns per agent slot.
# Column order matches the stakeholder-specified layout:
#   S.NO | DATE_TIME_UTC | USER_EMAIL | BUSINESS_DNA | POST_TYPE |
#   TOTAL_PROCESSING_TIME_SEC | TOTAL_COST_USD |
#   [6 columns × 7 agent slots] |
#   TOTAL_IMAGES_GENERATED
# Totals sit up front so the sheet reads left-to-right: identity → headline
# numbers → per-agent breakdown. TRACE_ID intentionally dropped from this
# CSV (still present in api_cost_ledger.csv for engineering forensics).
def _build_agent_csv_header() -> list[str]:
    cols = [
        "S.NO",
        "DATE_TIME_UTC",
        "ENVIRONMENT",
        "USER_EMAIL",
        "BUSINESS_DNA",
        "POST_TYPE",
        "TOTAL_PROCESSING_TIME_SEC",
        "TOTAL_COST_USD",
    ]
    for slot in AGENT_SLOTS:
        cols.extend([
            f"{slot}_CALLS",
            f"{slot}_MODEL",
            f"{slot}_INPUT_TOKENS",
            f"{slot}_OUTPUT_TOKENS",
            f"{slot}_TIME_SEC",
            f"{slot}_COST_USD",
        ])
    # Image count at the very end — separate from the primary totals since
    # it's an image-only metric (zero for text/video posts).
    cols.append("TOTAL_IMAGES_GENERATED")
    return cols


AGENT_CSV_HEADER = _build_agent_csv_header()

_WRITE_LOCK = threading.Lock()


def _next_serial() -> int:
    """Peek the last S.NO in the agent CSV and increment. Cheap because
    we only read the last few bytes."""
    if not AGENT_CSV_PATH.exists() or AGENT_CSV_PATH.stat().st_size == 0:
        return 1
    try:
        with open(AGENT_CSV_PATH, "rb") as f:
            f.seek(0, 2)  # end
            size = f.tell()
            f.seek(max(0, size - 2048), 0)
            tail = f.read().decode("utf-8", errors="ignore")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        for line in reversed(lines):
            first = line.split(",", 1)[0].strip()
            if first.isdigit():
                return int(first) + 1
    except Exception as exc:
        logger.warning(f"[cost_ledger] _next_serial fallback due to: {exc}")
    return 1


def _write_agent_csv_row(ledger: CostLedger, serial: int) -> None:
    """Append one row to the per-agent detailed CSV.

    One column group per agent slot (calls / model / input_tokens /
    output_tokens / time_sec / cost_usd). Plus totals.

    All *_TIME_SEC values are WALL-CLOCK (what the user waited),
    not cumulative compute time. For agents that fire in parallel
    (Agent 1 + Agent 2 across variants), we take the max of the
    parallel batch. For sequential agents, this equals the single
    call's elapsed. TOTAL_PROCESSING_TIME_SEC is computed from the
    real request start-to-finish span (finalized_at - started_at)
    so it exactly matches what the caller waited.
    """
    # Schema-drift check — if the on-disk CSV was written with an older
    # header (e.g. before COPYWRITER → TEXT_GENERATION rename, or before
    # the totals block moved up front), archive it and start a fresh
    # file. Appending would silently misalign new rows against stale
    # columns and corrupt every row until the next manual rotation.
    if AGENT_CSV_PATH.exists() and AGENT_CSV_PATH.stat().st_size > 0:
        try:
            with open(AGENT_CSV_PATH, "r", encoding="utf-8", newline="") as f:
                first_line = f.readline().strip()
            existing_header = [c.strip() for c in first_line.split(",")]
            if existing_header != AGENT_CSV_HEADER:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                archive_path = AGENT_CSV_PATH.with_suffix(f".{ts}.bak.csv")
                AGENT_CSV_PATH.rename(archive_path)
                logger.info(
                    f"[cost_ledger] agent CSV schema changed — archived "
                    f"previous file to {archive_path.name} and starting fresh"
                )
        except Exception as exc:
            logger.warning(f"[cost_ledger] header check failed (proceeding as append-only): {exc}")

    new_file = not AGENT_CSV_PATH.exists() or AGENT_CSV_PATH.stat().st_size == 0
    with open(AGENT_CSV_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(AGENT_CSV_HEADER)

        # TOTAL_PROCESSING_TIME_SEC = real request wall-clock, from ledger
        # start until finalize is called. This matches the TRACE SUMMARY
        # total the user sees in the log. Computed up front so it lands
        # in the totals block near the top of the row.
        _now = datetime.now(timezone.utc)
        total_wall_clock = (_now - ledger.started_at).total_seconds()

        row: list[Any] = [
            serial,
            ledger.started_at.isoformat(timespec="seconds"),
            _ENV_LABEL,
            ledger.user_email,
            ledger.business_dna,
            ledger.post_type,
            f"{total_wall_clock:.2f}",
            f"{ledger.total_cost_usd:.6f}",
        ]
        for slot in AGENT_SLOTS:
            s = ledger.agent_stats[slot]
            row.extend([
                s["calls"],
                s["model"],
                s["input_tokens"],
                s["output_tokens"],
                f"{s['time_sec']:.2f}",   # wall-clock (max) for this slot
                f"{s['cost']:.6f}",
            ])

        row.append(ledger.images_generated)
        w.writerow(row)


def _sync_local_from_s3() -> None:
    """Download the current unified CSV from S3 to the local working
    path so the next serial + append pick up rows written by other
    environments (or by earlier invocations on ephemeral Lambda disks).

    Non-fatal on any error — if S3 is unreachable or the object doesn't
    exist yet, we fall through and the write will create a fresh local
    file (which will then be uploaded and become the new S3 baseline).
    """
    try:
        from core.s3_utils import get_s3_client, S3_BUCKET_NAME
        s3 = get_s3_client()
        if not s3 or not S3_BUCKET_NAME:
            return
        # HEAD first to see whether the object even exists (avoids a
        # confusing "download failed" log line on first-ever write).
        try:
            s3.head_object(Bucket=S3_BUCKET_NAME, Key=AGENT_S3_CSV_KEY)
        except Exception:
            return  # object doesn't exist yet — nothing to sync
        AGENT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_CSV_PATH, "wb") as f:
            s3.download_fileobj(S3_BUCKET_NAME, AGENT_S3_CSV_KEY, f)
    except Exception as exc:
        logger.warning(f"[cost_ledger] S3 pre-write sync failed (proceeding with local file as-is): {exc}")


def finalize_ledger(ledger: Optional[CostLedger] = None) -> Optional[str]:
    """Write the ledger's per-agent breakdown to the unified CSV and
    mirror it to S3. Non-fatal on any error. Returns the S3 URL of the
    uploaded CSV, or None on any failure.

    ONE unified file `pipelyt_post_generation_time_and_cost.csv` is
    shared across local, staging, and prod. Each row carries an
    ENVIRONMENT column so the sheet can be filtered per-env in Excel.
    Before appending, the current S3 file is downloaded to the local
    working path (see `_sync_local_from_s3`) — this is what keeps
    Lambda cold-starts from clobbering earlier history when they
    upload with a fresh single-row file.
    """
    if ledger is None:
        ledger = get_current_ledger()
    if ledger is None:
        logger.warning("[cost_ledger] finalize called with no active ledger")
        return None

    with _WRITE_LOCK:
        # Sync the current S3 file down before we compute the next serial.
        # On Lambda this is what makes append-safe: without it, each cold
        # start would begin with an empty local file, and the subsequent
        # upload would overwrite the S3 object with a single-row history.
        _sync_local_from_s3()

        # Compute the next S.NO from the agent CSV, then append the row
        # (schema-drift rotation lives inside `_write_agent_csv_row`).
        serial = _next_serial()
        try:
            _write_agent_csv_row(ledger, serial)
        except Exception as exc:
            logger.warning(f"[cost_ledger] agent CSV write failed: {exc}")
            return None

        logger.info(
            f"[cost_ledger] row #{serial} written env={_ENV_LABEL} "
            f"user={ledger.user_email or ledger.user_id} "
            f"dna={ledger.business_dna!r} post_type={ledger.post_type!r} "
            f"style={ledger.image_style!r} total=${ledger.total_cost_usd:.4f}"
        )

        # Mirror to S3 at the env-appropriate key.
        try:
            from core.s3_utils import get_s3_client, S3_BUCKET_NAME, get_s3_url

            s3 = get_s3_client()
            if not s3 or not S3_BUCKET_NAME:
                logger.warning("[cost_ledger] S3 not configured — skipping upload")
                return None

            if AGENT_CSV_PATH.exists():
                with open(AGENT_CSV_PATH, "rb") as f:
                    s3.upload_fileobj(
                        f, S3_BUCKET_NAME, AGENT_S3_CSV_KEY,
                        ExtraArgs={"ContentType": "text/csv"},
                    )
                agent_s3_url = get_s3_url(AGENT_S3_CSV_KEY)
                logger.info(f"[cost_ledger] CSV uploaded to {agent_s3_url} (env={_ENV_LABEL})")
                return agent_s3_url
            return None
        except Exception as exc:
            logger.warning(f"[cost_ledger] S3 upload failed (row still saved locally): {exc}")
            return None
