"""GTM LinkedIn — plain-text logging setup (ON BY DEFAULT, no env var needed).

Every GTM-LinkedIn module calls `setup()` at import. It pins the
`pipelyt.gtm_linkedin` logger to INFO with a single readable stdout handler so
the logs show up in CloudWatch for BOTH the app Lambda and the worker Lambda,
regardless of how the surrounding runtime configured the root logger.

Format (greppable by the `[GTM-LI]` tag):
    2026-06-24 10:11:12 [GTM-LI] INFO pipelyt.gtm_linkedin.worker: worker: received job 41 ...
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("pipelyt.gtm_linkedin")
    logger.setLevel(logging.INFO)
    # Add exactly one of OUR handlers (idempotent across warm-Lambda re-imports).
    if not any(getattr(h, "_gtm_li", False) for h in logger.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [GTM-LI] %(levelname)s %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        ))
        h._gtm_li = True  # type: ignore[attr-defined]
        logger.addHandler(h)
    # Don't also bubble to the root handler → no duplicate lines.
    logger.propagate = False
    _CONFIGURED = True
