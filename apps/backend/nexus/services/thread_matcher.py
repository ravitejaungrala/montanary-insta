"""
Match an inbound reply to an existing InboundThread / LeadSequence /
GlobalLead.

Strategy (in order):
  1. RFC-5322 header chain — look up `In-Reply-To` and any `References`
     message-IDs against `nexus_inbound_messages.message_id_header`.
     This is the most reliable signal.
  2. Provider thread IDs — if the inbound payload carries a Gmail thread ID
     or Resend thread ID, match `nexus_inbound_threads.gmail_thread_id` /
     `.resend_thread_id`.
  3. From-email + subject heuristic — strip `Re:` / `Fwd:` prefixes, match
     workspace + sender + normalized subject in the last 90 days.

On total miss, persist an `UnmatchedReply` row and return None.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from nexus.models_phase5 import (
    InboundMessage,
    InboundThread,
    UnmatchedReply,
)

logger = logging.getLogger("nexus.services.thread_matcher")

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fwd|fw|aw)\s*:\s*", re.IGNORECASE)


def _normalize_subject(subject: Optional[str]) -> str:
    if not subject:
        return ""
    s = subject.strip()
    # Strip repeated "Re:" / "Fwd:" prefixes.
    while True:
        new = _SUBJECT_PREFIX_RE.sub("", s)
        if new == s:
            break
        s = new
    return s.strip().lower()


def match(
    db: Session,
    *,
    workspace_id: Optional[int],
    from_email: Optional[str],
    subject: Optional[str],
    in_reply_to: Optional[str],
    references: Optional[Iterable[str]] = None,
    gmail_thread_id: Optional[str] = None,
    resend_thread_id: Optional[str] = None,
) -> Optional[InboundThread]:
    """Return the matched InboundThread, or None if no match found.

    Note: the caller is responsible for logging UnmatchedReply on None —
    `log_unmatched()` below is the helper for that.
    """
    # 1. RFC-5322 header chain
    candidate_ids = []
    if in_reply_to:
        candidate_ids.append(in_reply_to.strip())
    if references:
        candidate_ids.extend([r.strip() for r in references if r and r.strip()])
    # Dedup while preserving order.
    seen = set()
    candidate_ids = [c for c in candidate_ids if not (c in seen or seen.add(c))]

    if candidate_ids:
        msg = (
            db.query(InboundMessage)
            .filter(InboundMessage.message_id_header.in_(candidate_ids))
            .order_by(InboundMessage.received_at.desc())
            .first()
        )
        if msg:
            thread = db.query(InboundThread).filter(InboundThread.id == msg.thread_id).first()
            if thread and (workspace_id is None or thread.workspace_id == workspace_id):
                return thread

    # 2. Provider thread IDs — scope to the reply's workspace, mirroring the
    #    header-chain path above, so a provider thread id collision can't return
    #    another tenant's thread.
    if gmail_thread_id:
        thread = (
            db.query(InboundThread)
            .filter(InboundThread.gmail_thread_id == gmail_thread_id)
            .first()
        )
        if thread and (workspace_id is None or thread.workspace_id == workspace_id):
            return thread

    if resend_thread_id:
        thread = (
            db.query(InboundThread)
            .filter(InboundThread.resend_thread_id == resend_thread_id)
            .first()
        )
        if thread and (workspace_id is None or thread.workspace_id == workspace_id):
            return thread

    # 3. From-email + normalized-subject heuristic in last 90 days
    norm_subj = _normalize_subject(subject)
    if from_email and norm_subj and workspace_id is not None:
        cutoff = datetime.utcnow() - timedelta(days=90)
        # Subselect threads in workspace that have any message from this sender
        # with a matching normalized subject. SQLAlchemy can't easily express
        # `lower(strip-prefix)` portably so we filter in two steps.
        msgs = (
            db.query(InboundMessage)
            .join(InboundThread, InboundThread.id == InboundMessage.thread_id)
            .filter(
                InboundThread.workspace_id == workspace_id,
                InboundMessage.from_email == from_email,
                InboundMessage.received_at >= cutoff,
            )
            .order_by(InboundMessage.received_at.desc())
            .limit(50)
            .all()
        )
        for m in msgs:
            if _normalize_subject(m.subject) == norm_subj:
                thread = db.query(InboundThread).filter(InboundThread.id == m.thread_id).first()
                if thread:
                    return thread

    return None


def log_unmatched(
    db: Session,
    *,
    workspace_id: Optional[int],
    from_email: Optional[str],
    subject: Optional[str],
    body_text: Optional[str],
    body_html: Optional[str],
    message_id_header: Optional[str],
    in_reply_to_header: Optional[str],
    raw_payload: Optional[str] = None,
) -> UnmatchedReply:
    """Persist an UnmatchedReply row for admin triage."""
    row = UnmatchedReply(
        workspace_id=workspace_id,
        from_email=from_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id_header=message_id_header,
        in_reply_to_header=in_reply_to_header,
        raw_payload=raw_payload,
    )
    db.add(row)
    db.flush()  # populate id without committing — caller controls tx
    return row


def create_thread(
    db: Session,
    *,
    workspace_id: int,
    subject: Optional[str],
    gmail_thread_id: Optional[str] = None,
    resend_thread_id: Optional[str] = None,
    lead_id: Optional[int] = None,
    lead_sequence_id: Optional[int] = None,
) -> InboundThread:
    """Create a fresh InboundThread when no match is found but we still
    want to capture the reply (e.g. inbound from a known lead but a new
    subject line)."""
    thread = InboundThread(
        workspace_id=workspace_id,
        subject=(subject or "")[:998] or None,
        gmail_thread_id=gmail_thread_id,
        resend_thread_id=resend_thread_id,
        lead_id=lead_id,
        lead_sequence_id=lead_sequence_id,
        status="open",
        message_count=0,
        last_message_at=datetime.utcnow(),
    )
    db.add(thread)
    db.flush()
    return thread


__all__ = ["match", "log_unmatched", "create_thread"]
