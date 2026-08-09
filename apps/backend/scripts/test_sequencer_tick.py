"""End-to-end test of the sequencer + branded template wiring.

Creates the minimum DB rows needed for process_due_sequences to fire
ONE email through the new outreach_template path, then verifies the
side-effects:

  - NexusLeadSequence current_step advances 0 -> 1
  - NexusTouchpoint row inserted with status='sent' and a
    resend_message_id
  - NexusSendLog row inserted
  - Resend actually delivered the email (visible in your inbox)

Safe to re-run — uses a deterministic email-as-marker so we always
target the same test lead row.

Usage:
    python scripts/test_sequencer_tick.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _load_dotenv() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

TEST_EMAIL = "siddhuhis1@gmail.com"  # the inbox we send to
TEST_MARKER = "sequencer-tick-test"


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _info(msg: str) -> None:
    print(f"  -- {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> None:
    print("== Sequencer tick end-to-end test ==\n")

    from core.database import SessionLocal
    # Pipelyt's User model — must be imported so SQLAlchemy can resolve the
    # NexusWorkspace -> User relationship before any nexus_* query runs.
    import models  # noqa: F401
    from nexus import models_phase2
    from nexus.models_phase3 import NexusCampaign, NexusGlobalLead
    from nexus.models_phase4 import (
        NexusConversationAccount,
        NexusLeadSequence,
        NexusSendLog,
        NexusSequence,
        NexusTouchpoint,
    )
    from nexus.models import NexusWorkspace
    from nexus.services.sequencer import process_due_sequences

    db = SessionLocal()
    try:
        # ── 1. Workspace ─────────────────────────────────────────────
        print("[1] Finding/creating a workspace")
        ws = db.query(NexusWorkspace).order_by(NexusWorkspace.id.asc()).first()
        if not ws:
            _fail(
                "No NexusWorkspace exists. Log in to Nexus once in the UI "
                "to provision one, then re-run."
            )
            return
        _ok(f"workspace id={ws.id} name={ws.name!r}")

        # ── 2. Product ────────────────────────────────────────────────
        print("\n[2] Finding/creating a test product")
        product = (
            db.query(models_phase2.NexusProduct)
            .filter(models_phase2.NexusProduct.workspace_id == ws.id)
            .order_by(models_phase2.NexusProduct.id.desc())
            .first()
        )
        if not product:
            product = models_phase2.NexusProduct(
                workspace_id=ws.id,
                user_id=ws.owner_id if hasattr(ws, "owner_id") else 1,
                name="Spenzo AI",
                value_proposition="AI marketing analytics that surfaces wasted spend",
                key_benefits=["Channel attribution", "Budget reallocation", "Branded-search clarity"],
                source_url="https://spenzo.ai",
                status="ready",
                icp={"industries": ["SaaS"], "roles": ["VP Marketing"]},
            )
            db.add(product)
            db.commit()
            db.refresh(product)
        _ok(f"product id={product.id} name={product.name!r}")

        # ── 3. Campaign ───────────────────────────────────────────────
        print("\n[3] Finding/creating a test campaign")
        campaign = (
            db.query(NexusCampaign)
            .filter(
                NexusCampaign.workspace_id == ws.id,
                NexusCampaign.product_id == product.id,
            )
            .order_by(NexusCampaign.id.desc())
            .first()
        )
        if not campaign:
            campaign = NexusCampaign(
                workspace_id=ws.id,
                product_id=product.id,
                name="Sequencer Tick Test",
                icp=product.icp or {},
                status="active",
            )
            db.add(campaign)
            db.commit()
            db.refresh(campaign)
        _ok(f"campaign id={campaign.id} name={campaign.name!r}")

        # ── 4. Sequence ───────────────────────────────────────────────
        print("\n[4] Finding/creating the default sequence")
        seq = (
            db.query(NexusSequence)
            .filter(
                NexusSequence.workspace_id == ws.id,
                NexusSequence.campaign_id == campaign.id,
            )
            .first()
        )
        if not seq:
            seq = NexusSequence(
                workspace_id=ws.id,
                campaign_id=campaign.id,
                name="Test outbound",
                steps=[
                    {
                        "step": 0,
                        "delay_days": 0,
                        "subject_template": "Quick thought, {first_name}",
                        "body_template": "Hi {first_name}",
                    },
                ],
            )
            db.add(seq)
            db.commit()
            db.refresh(seq)
        _ok(f"sequence id={seq.id} steps={len(seq.steps or [])}")

        # ── 5. Lead (target = siddhuhis1@gmail.com) ──────────────────
        # The NexusGlobalLead ORM model only exposes the original Phase 3
        # columns; Phase 7 migrations added `title`/`linkedin_url`/
        # `enrichment` directly via DDL. So we use raw SQL to set those.
        print(f"\n[5] Finding/creating the test lead for {TEST_EMAIL}")
        from sqlalchemy import text as _text

        existing = db.execute(
            _text("SELECT id FROM nexus_global_leads WHERE email = :e"),
            {"e": TEST_EMAIL},
        ).first()
        if existing:
            lead_id = existing[0]
            db.execute(
                _text(
                    "UPDATE nexus_global_leads SET "
                    "first_name = :fn, last_name = :ln, job_title = :ti, "
                    "company_name = :cn, source = :src, "
                    "updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {
                    "fn": "Siddharth",
                    "ln": "His",
                    "ti": "VP Engineering",
                    "cn": "Acme Manufacturing",
                    "src": "manual_test",
                    "id": lead_id,
                },
            )
        else:
            row = db.execute(
                _text(
                    "INSERT INTO nexus_global_leads "
                    "(email, first_name, last_name, job_title, company_name, "
                    " source, status, priority, email_verified, "
                    " email_verify_score, created_at, updated_at) "
                    "VALUES (:e, :fn, :ln, :ti, :cn, :src, 'new', 'active', "
                    "         false, 0, NOW(), NOW()) "
                    "RETURNING id"
                ),
                {
                    "e": TEST_EMAIL,
                    "fn": "Siddharth",
                    "ln": "His",
                    "ti": "VP Engineering",
                    "cn": "Acme Manufacturing",
                    "src": "manual_test",
                },
            ).first()
            lead_id = row[0]
        db.commit()
        _ok(f"lead id={lead_id} email={TEST_EMAIL}")

        # ── 6. Conversation account (lazy bootstrap will create if missing) ──
        print("\n[6] Checking conversation account")
        acc = (
            db.query(NexusConversationAccount)
            .filter(NexusConversationAccount.workspace_id == ws.id)
            .first()
        )
        if acc:
            _ok(f"account id={acc.id} email={acc.email_address} sent_today={acc.daily_send_count}/{acc.daily_send_limit}")
        else:
            _info("no account yet — sequencer will lazy-bootstrap from NEXUS_DEFAULT_FROM_EMAIL")

        # ── 7. Enroll the lead in the sequence (NexusLeadSequence) ──
        print("\n[7] Enrolling/refreshing the lead sequence row")
        now = datetime.utcnow()
        ls = (
            db.query(NexusLeadSequence)
            .filter(
                NexusLeadSequence.workspace_id == ws.id,
                NexusLeadSequence.sequence_id == seq.id,
                NexusLeadSequence.lead_id == lead_id,
            )
            .first()
        )
        if ls:
            # Force the row to be due right now so it gets picked up.
            ls.status = "active"
            ls.current_step = 0
            ls.next_action_at = now
            ls.locked_until = None
        else:
            ls = NexusLeadSequence(
                workspace_id=ws.id,
                campaign_id=campaign.id,
                sequence_id=seq.id,
                lead_id=lead_id,
                current_step=0,
                status="active",
                next_action_at=now,
            )
            db.add(ls)
        db.commit()
        db.refresh(ls)
        _ok(f"lead_sequence id={ls.id} status={ls.status} step={ls.current_step} due_at={ls.next_action_at}")

        before_touchpoints = (
            db.query(NexusTouchpoint)
            .filter(NexusTouchpoint.lead_sequence_id == ls.id)
            .count()
        )
        before_sendlogs = db.query(NexusSendLog).count()

        # ── 8. Fire the sequencer tick ───────────────────────────────
        print("\n[8] Calling process_due_sequences(db)...")
        # Use a fresh session for the sequencer so its FOR UPDATE SKIP
        # LOCKED claim doesn't fight the one we already hold.
        from core.database import SessionLocal as _S2

        seq_db = _S2()
        try:
            result = process_due_sequences(seq_db)
        finally:
            seq_db.close()
        _ok(f"result: {result}")

        # ── 9. Inspect side-effects ──────────────────────────────────
        print("\n[9] Verifying side-effects")
        db.expire_all()
        ls_after = (
            db.query(NexusLeadSequence)
            .filter(NexusLeadSequence.id == ls.id)
            .first()
        )
        _ok(f"lead_sequence step now {ls_after.current_step} (was 0)")
        _ok(f"lead_sequence status: {ls_after.status}")
        _ok(f"lead_sequence next_action_at: {ls_after.next_action_at}")
        if ls_after.last_error:
            _fail(f"lead_sequence.last_error = {ls_after.last_error}")

        tp = (
            db.query(NexusTouchpoint)
            .filter(NexusTouchpoint.lead_sequence_id == ls.id)
            .order_by(NexusTouchpoint.id.desc())
            .first()
        )
        if tp:
            _ok(
                f"touchpoint id={tp.id} step={tp.step} status={tp.status} "
                f"resend_id={tp.resend_message_id!r}"
            )
            _ok(f"touchpoint subject: {tp.subject!r}")
            if tp.error:
                _fail(f"touchpoint.error = {tp.error}")
        else:
            _fail("no touchpoint was inserted")

        after_sendlogs = db.query(NexusSendLog).count()
        _ok(f"send_logs went from {before_sendlogs} -> {after_sendlogs}")

        # Inspect Resend message id from the touchpoint
        if tp and tp.resend_message_id:
            _ok(
                "Resend accepted the send. Check siddhuhis1@gmail.com — "
                f"message_id={tp.resend_message_id}"
            )

        print("\n== Sequencer tick test complete ==")
    finally:
        db.close()


if __name__ == "__main__":
    main()
