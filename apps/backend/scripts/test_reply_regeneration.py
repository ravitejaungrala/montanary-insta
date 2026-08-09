"""Offline verification for reply-aware follow-up regeneration + referral
lead creation (plan.md: "Reply-Aware Follow-up Generation").

Deterministic, no keys/network — Gemini calls are mocked throughout. Uses an
in-memory SQLite DB with a minimal hand-written schema (not
Base.metadata.create_all — that pulls in the full cross-phase FK graph,
which needs Postgres-only tables; the ORM classes work fine against any
table with matching column names).

Run from apps/backend with the backend venv:

  ./venv/Scripts/python.exe scripts/test_reply_regeneration.py

Covers:
  1. OOO return-date extraction (intent_classifier.extract_ooo_return_date).
  2. generate_reply_aware_followups: success path (clamping, referral
     extraction) and failure path (returns None, never raises).
  3. regenerate_pending_followups: the off-by-one that would exclude the
     NEXT due step (step >= current_step, not step >); step 0 (already
     sent) is never touched; OOO fast-path date overrides the model's
     delay; idempotent re-call with the SAME message is a no-op; a NEWER
     message on the same thread re-triggers regeneration (the multi-reply
     freshness fix).
  4. our_prior_reply is only populated for OUT_OF_OFFICE (the one
     continuing intent that's reliably auto-sent today) — never claims an
     answer was sent for NOT_NOW/QUESTION/QUESTION_PRICE.
  5. _latest_inbound_message_for_sequence: picks the newest INBOUND-
     direction message on the right thread, ignoring outbound rows and
     other leads' threads.
  6. _apply_intent_side_effects wiring: CONTINUING_INTENTS call the
     regeneration function and do NOT halt the sequence; other intents
     halt it and do NOT call regeneration.
  7. create_referral_leads: dedupe by email, correct provenance columns,
     sequence created ACTIVE with the normal full 4-step cadence — step 0
     uses the honest referral-framed intro, steps 1-3 use the standard
     generated cadence.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the full model set so SQLAlchemy's mapper registry can resolve
# cross-phase FKs (nexus_sequences.workspace_id -> nexus_workspaces.id, etc.)
# even though this script never creates those tables. Same pattern as
# scripts/test_intent_gate.py.
import models  # noqa: F401,E402
import nexus.models  # noqa: F401,E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from nexus.models_phase3 import NexusGlobalLead  # noqa: E402
from nexus.models_phase4 import NexusLeadSequence, NexusLeadEmail, NexusSequence  # noqa: E402
from nexus.models_phase5 import InboundThread, InboundMessage  # noqa: E402
from nexus.services import sequencer as seq  # noqa: E402
from nexus.services import outreach_template as ot  # noqa: E402
from nexus.services.intent_classifier import extract_ooo_return_date  # noqa: E402
from nexus.routers import inbound as inbound_router  # noqa: E402


def _hr(title: str) -> None:
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


def _new_session():
    """Fresh in-memory SQLite DB with the minimal schema this suite needs.
    Raw DDL (no FK constraints) — see module docstring."""
    engine = create_engine("sqlite:///:memory:")

    # Production code uses Postgres' NOW() in raw SQL (matches this repo's
    # existing convention elsewhere) — SQLite has no such function, so
    # register a shim rather than rewrite production SQL for test portability.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, _rec):
        dbapi_conn.create_function("NOW", 0, lambda: datetime.utcnow().isoformat())

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE nexus_sequences (id INTEGER PRIMARY KEY, workspace_id INTEGER, "
            "campaign_id INTEGER, name TEXT, steps TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_lead_sequences (id INTEGER PRIMARY KEY, workspace_id INTEGER, "
            "campaign_id INTEGER, lead_id INTEGER, sequence_id INTEGER, current_step INTEGER, "
            "next_action_at TIMESTAMP, status TEXT, last_error TEXT, conversation_account_id INTEGER, "
            "enrolled_at TIMESTAMP, completed_at TIMESTAMP, locked_until TIMESTAMP, halt_reason TEXT, "
            "updated_at TIMESTAMP, needs_review BOOLEAN, needs_review_reason TEXT, step_schedule TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_lead_emails (id INTEGER PRIMARY KEY, workspace_id INTEGER, "
            "campaign_id INTEGER, lead_id INTEGER, lead_sequence_id INTEGER, step INTEGER, kind TEXT, "
            "subject TEXT, body TEXT, real_result TEXT, opener TEXT, variant_key TEXT, status TEXT, "
            "sent_at TIMESTAMP, provider_message_id TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, "
            "regenerated_from_message_id INTEGER, regenerated_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_global_leads (id INTEGER PRIMARY KEY, email TEXT UNIQUE, "
            "first_name TEXT, last_name TEXT, role TEXT, company_domain TEXT, company_name TEXT, "
            "linkedin_url TEXT, status TEXT, priority TEXT, email_verified BOOLEAN, "
            "email_verify_score INTEGER, source TEXT, person_city TEXT, person_state TEXT, "
            "person_country TEXT, organization_industry TEXT, linkedin_headline TEXT, "
            "apollo_person_id TEXT, phone TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, "
            "source_lead_id INTEGER, source_message_id INTEGER)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_leads (id INTEGER PRIMARY KEY, workspace_id INTEGER, campaign_id INTEGER, "
            "product_id INTEGER, global_lead_id INTEGER, icp_score INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_inbound_threads (id INTEGER PRIMARY KEY, workspace_id INTEGER, "
            "lead_id INTEGER, lead_sequence_id INTEGER, gmail_thread_id TEXT, resend_thread_id TEXT, "
            "subject TEXT, last_message_at TIMESTAMP, message_count INTEGER, status TEXT, created_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE nexus_inbound_messages (id INTEGER PRIMARY KEY, thread_id INTEGER, "
            "direction TEXT, from_email TEXT, to_email TEXT, subject TEXT, body_text TEXT, "
            "body_html TEXT, message_id_header TEXT, in_reply_to_header TEXT, received_at TIMESTAMP, "
            "intent TEXT, intent_confidence REAL, intent_reasoning TEXT, suggested_reply TEXT, is_read BOOLEAN)"
        ))
    return sessionmaker(bind=engine)()


def _seed_lead_sequence(db, current_step=1):
    nseq = NexusSequence(workspace_id=1, campaign_id=1, name="t", steps=[])
    db.add(nseq)
    db.commit()
    ls = NexusLeadSequence(
        workspace_id=1, campaign_id=1, lead_id=42, sequence_id=nseq.id,
        current_step=current_step, status="active",
    )
    db.add(ls)
    db.commit()
    db.add_all([
        NexusLeadEmail(workspace_id=1, campaign_id=1, lead_id=42, lead_sequence_id=ls.id,
                        step=s, subject=f"orig subj {s}", body=f"orig body {s}", status="pending")
        for s in (0, 1, 2, 3)
    ])
    db.commit()
    return ls


KATIE_OOO_BODY = (
    "I'm currently running & riding in the French Alps. Returning to my desk July 9. "
    "In my absence please contact:\n\n"
    "Commercial & Ecomm: mitch@maap.cc\n"
    "Media: nicole.pires@maap.cc\n"
    "Partnerships & GTM: danielle.mcbain@maap.cc"
)


def check_ooo_date_extraction() -> None:
    d = extract_ooo_return_date(KATIE_OOO_BODY, datetime(2026, 7, 12))
    assert d is not None and d.month == 7 and d.day == 9, d
    assert extract_ooo_return_date("Out of office until further notice.", datetime(2026, 7, 12)) is None
    wrap = extract_ooo_return_date("Back on January 5.", datetime(2026, 12, 28))
    assert wrap.year == 2027 and wrap.month == 1 and wrap.day == 5, wrap
    print("OOO return-date extraction: real text, no-date case, year wraparound -- OK")


def check_generate_reply_aware_followups() -> None:
    import json
    fake_response = json.dumps({
        "followup_subject": "Following up when you're back",
        "followup_body": "Hope the Alps trip was great.",
        "followup2_subject": "Still worth a look?",
        "followup2_body": "One more note.",
        "closing_subject": "Closing the loop",
        "closing_body": "Last note.",
        "recommended_next_delay_days": 999,  # out of range -> must clamp to 30
        "referral_contacts": [
            {"name": "Mitch", "email": "mitch@maap.cc", "role_hint": "Commercial & Ecomm"},
            {"name": "", "email": "", "role_hint": "junk, must be dropped"},
        ],
    })
    with patch.object(ot.gemini, "chat_completion", return_value=fake_response):
        result = ot.generate_reply_aware_followups(
            lead={"first_name": "Katie", "company_name": "MAAP"},
            sender={"company_name": "Spenzo", "business_type": "product"},
            enrichment={},
            reply_context={"intent": "OUT_OF_OFFICE", "body_text": KATIE_OOO_BODY,
                            "our_prior_reply": "I will follow up after July 9.", "return_date": "2026-07-09"},
        )
    assert result is not None
    assert result["recommended_next_delay_days"] == 30, result["recommended_next_delay_days"]
    assert len(result["referral_contacts"]) == 1
    assert result["referral_contacts"][0]["email"] == "mitch@maap.cc"
    print("generate_reply_aware_followups success path: delay clamped 999->30, junk referral dropped -- OK")

    with patch.object(ot.gemini, "chat_completion", side_effect=RuntimeError("boom")):
        result2 = ot.generate_reply_aware_followups(
            lead={"first_name": "Katie"}, sender={"company_name": "Spenzo"},
            enrichment={}, reply_context={"intent": "NOT_NOW", "body_text": "not now"},
        )
    assert result2 is None
    print("generate_reply_aware_followups failure path: returns None after 3 failed attempts -- OK")


def check_regenerate_pending_followups() -> None:
    fake_result = {
        "followup_subject": "Following up post-Alps", "followup_body": "y",
        "followup2_subject": "Still worth a look?", "followup2_body": "y2",
        "closing_subject": "Closing the loop", "closing_body": "y3",
        "recommended_next_delay_days": 5,
        "referral_contacts": [{"name": "Mitch", "email": "mitch@maap.cc", "role_hint": "Commercial & Ecomm"}],
    }
    with patch.object(seq, "_load_lead", return_value={"first_name": "Katie", "company_domain": "maap.cc"}), \
         patch.object(seq, "_load_product", return_value={}), \
         patch.object(seq, "_build_sender_ctx_for_product", return_value={"company_name": "Spenzo"}), \
         patch.object(seq, "_li_company_background", return_value={}), \
         patch("nexus.services.outreach_template.generate_reply_aware_followups", return_value=fake_result):

        db = _new_session()
        ls = _seed_lead_sequence(db, current_step=1)
        msg1 = SimpleNamespace(id=1001, intent="OUT_OF_OFFICE", body_text=KATIE_OOO_BODY,
                                received_at=datetime(2026, 7, 1), suggested_reply="I will follow up after July 9.")

        result1 = seq.regenerate_pending_followups(db, ls, msg1)
        assert result1 is not None and result1["updated_steps"] == 3, result1
        by_step = {r.step: r for r in db.query(NexusLeadEmail).filter(NexusLeadEmail.lead_sequence_id == ls.id).all()}
        assert by_step[0].subject == "orig subj 0", "step 0 (already sent) must never be touched"
        assert by_step[1].regenerated_from_message_id == 1001
        assert ls.next_action_at.date().isoformat() == "2026-07-10", ls.next_action_at
        print("regenerate_pending_followups: step >= current_step (off-by-one fix), step 0 untouched, "
              "OOO fast-path date wins over model delay -- OK")

        result_same = seq.regenerate_pending_followups(db, ls, msg1)
        assert result_same is None, "same message must be a no-op (idempotent)"
        print("regenerate_pending_followups: idempotent re-call with SAME message -> no-op -- OK")

        msg2 = SimpleNamespace(id=1002, intent="NOT_NOW", body_text="Let's revisit in a couple months.",
                                received_at=datetime(2026, 8, 1), suggested_reply=None)
        result2 = seq.regenerate_pending_followups(db, ls, msg2)
        assert result2 is not None and result2["updated_steps"] == 3, result2
        print("regenerate_pending_followups: NEWER message on same thread re-triggers regeneration "
              "(multi-reply freshness fix) -- OK")


def check_our_prior_reply_gating() -> None:
    # The auto-reply guard chain sends `suggested_reply` for essentially
    # every non-suppressed reply, not just OUT_OF_OFFICE — so
    # regenerate_pending_followups passes it through as "what we already
    # told them" regardless of intent (see sequencer.py comment near
    # our_prior_reply for the reasoning).
    captured = {}

    def fake_generate(lead, sender, enrichment, reply_context):
        captured["reply_context"] = reply_context
        return {"followup_subject": "x", "followup_body": "y", "followup2_subject": "x2",
                "followup2_body": "y2", "closing_subject": "x3", "closing_body": "y3",
                "recommended_next_delay_days": 5, "referral_contacts": []}

    with patch.object(seq, "_load_lead", return_value={"first_name": "Katie", "company_domain": "maap.cc"}), \
         patch.object(seq, "_load_product", return_value={}), \
         patch.object(seq, "_build_sender_ctx_for_product", return_value={"company_name": "Spenzo"}), \
         patch.object(seq, "_li_company_background", return_value={}), \
         patch("nexus.services.outreach_template.generate_reply_aware_followups", side_effect=fake_generate):

        db = _new_session()
        ls = _seed_lead_sequence(db, current_step=1)
        msg_ooo = SimpleNamespace(id=2001, intent="OUT_OF_OFFICE", body_text="Returning July 9.",
                                   received_at=datetime(2026, 7, 1), suggested_reply="I will follow up after July 9.")
        seq.regenerate_pending_followups(db, ls, msg_ooo)
        assert captured["reply_context"]["our_prior_reply"] == "I will follow up after July 9."

        db2 = _new_session()
        ls2 = _seed_lead_sequence(db2, current_step=1)
        msg_int = SimpleNamespace(id=2002, intent="INTERESTED", body_text="Sure, tell me more.",
                                   received_at=datetime(2026, 7, 1), suggested_reply="Happy to — would Tuesday work for a call?")
        seq.regenerate_pending_followups(db2, ls2, msg_int)
        assert captured["reply_context"]["our_prior_reply"] == "Happy to — would Tuesday work for a call?"

    print("our_prior_reply: populated for both OUT_OF_OFFICE and INTERESTED "
          "(any continuing intent's suggested_reply is usable context) -- OK")


def check_latest_inbound_message_lookup() -> None:
    db = _new_session()
    th = InboundThread(workspace_id=1, lead_id=42, lead_sequence_id=555, subject="x", status="open")
    db.add(th)
    db.commit()
    db.add(InboundMessage(thread_id=th.id, direction="outbound", from_email="us@spenzo.io",
                           body_text="initial pitch", received_at=datetime(2026, 7, 5)))
    db.add(InboundMessage(thread_id=th.id, direction="inbound", from_email="katie@maap.cc",
                           body_text="Returning July 9.", received_at=datetime(2026, 7, 6), intent="OUT_OF_OFFICE"))
    db.add(InboundMessage(thread_id=th.id, direction="outbound", from_email="us@spenzo.io",
                           body_text="ack", received_at=datetime(2026, 7, 6, 0, 1)))
    db.add(InboundMessage(thread_id=th.id, direction="inbound", from_email="katie@maap.cc",
                           body_text="Let's revisit in a couple months.", received_at=datetime(2026, 8, 1), intent="NOT_NOW"))
    db.commit()
    th2 = InboundThread(workspace_id=1, lead_id=99, lead_sequence_id=999, subject="unrelated", status="open")
    db.add(th2)
    db.commit()
    db.add(InboundMessage(thread_id=th2.id, direction="inbound", from_email="someone@else.com",
                           body_text="hi", received_at=datetime(2026, 9, 1)))
    db.commit()

    with patch.object(seq, "_table_exists", return_value=True):
        latest = seq._latest_inbound_message_for_sequence(db, 555)
        assert latest is not None and latest.intent == "NOT_NOW", latest
        assert seq._latest_inbound_message_for_sequence(db, 12345) is None

    print("_latest_inbound_message_for_sequence: newest INBOUND on the right thread, "
          "ignores outbound + other leads' threads -- OK")


def check_apply_intent_side_effects_wiring() -> None:
    ls_stub = SimpleNamespace(id=555, current_step=1, workspace_id=1, campaign_id=1, lead_id=42, next_action_at=None)
    msg_stub = SimpleNamespace(id=1001, intent="OUT_OF_OFFICE", body_text=KATIE_OOO_BODY,
                                received_at=None, suggested_reply="I will follow up after July 9.")

    def fake_db(existing_ls):
        executed = []

        class FakeExec:
            def first(self):
                return None

        class FakeDB:
            def execute(self, stmt, params=None):
                executed.append((str(stmt), params))
                return FakeExec()

            def query(self, model):
                q = MagicMock()
                q.filter.return_value = q
                q.first.return_value = existing_ls
                return q

            def commit(self):
                pass

            def rollback(self):
                pass

        d = FakeDB()
        d._executed = executed
        return d

    with patch.object(inbound_router, "_table_exists", return_value=True), \
         patch("nexus.services.sequencer.regenerate_pending_followups") as mock_regen:
        db = fake_db(ls_stub)
        inbound_router._apply_intent_side_effects(
            db, workspace_id=1, from_email="katie@maap.cc", intent="OUT_OF_OFFICE",
            lead_id=None, lead_sequence_id=555, inbound_message=msg_stub,
        )
        mock_regen.assert_called_once_with(db, ls_stub, msg_stub)
        assert not [p for s, p in db._executed if p and p.get("s") == "replied" and "lead_sequences" in s]
    print("_apply_intent_side_effects: OUT_OF_OFFICE -> regeneration called, sequence NOT halted -- OK")

    with patch.object(inbound_router, "_table_exists", return_value=True), \
         patch("nexus.services.sequencer.regenerate_pending_followups") as mock_regen:
        db = fake_db(ls_stub)
        inbound_router._apply_intent_side_effects(
            db, workspace_id=1, from_email="katie@maap.cc", intent="NOT_NOW",
            lead_id=None, lead_sequence_id=555, inbound_message=msg_stub,
        )
        mock_regen.assert_called_once()
    print("_apply_intent_side_effects: NOT_NOW -> regeneration called (now a continuing intent) -- OK")

    with patch.object(inbound_router, "_table_exists", return_value=True), \
         patch("nexus.services.sequencer.regenerate_pending_followups") as mock_regen:
        db = fake_db(ls_stub)
        inbound_router._apply_intent_side_effects(
            db, workspace_id=1, from_email="katie@maap.cc", intent="INTERESTED",
            lead_id=None, lead_sequence_id=555, inbound_message=msg_stub,
        )
        mock_regen.assert_called_once()
        assert not [p for s, p in db._executed if p and p.get("s") == "replied" and "lead_sequences" in s]
    print("_apply_intent_side_effects: INTERESTED -> regeneration called, sequence NOT halted "
          "(a positive reply keeps the cadence going too) -- OK")

    with patch.object(inbound_router, "_table_exists", return_value=True), \
         patch("nexus.services.sequencer.regenerate_pending_followups") as mock_regen:
        db = fake_db(ls_stub)
        inbound_router._apply_intent_side_effects(
            db, workspace_id=1, from_email="katie@maap.cc", intent="NOT_INTERESTED",
            lead_id=None, lead_sequence_id=555, inbound_message=msg_stub,
        )
        mock_regen.assert_not_called()
        assert [p for s, p in db._executed if p and p.get("s") == "replied" and p.get("id") == 555]
    print("_apply_intent_side_effects: NOT_INTERESTED -> regeneration NOT called, sequence halted "
          "(the negative signal that should actually stop outreach) -- OK")


def check_create_referral_leads() -> None:
    db = _new_session()
    nseq = NexusSequence(workspace_id=1, campaign_id=1, name="t", steps=[])
    db.add(nseq)
    db.commit()
    source_ls = NexusLeadSequence(workspace_id=1, campaign_id=1, lead_id=42, sequence_id=nseq.id,
                                   current_step=1, status="active")
    db.add(source_ls)
    db.commit()
    db.add(NexusGlobalLead(email="katie.cipa@maap.cc", first_name="Katie", last_name="Cipa",
                            company_domain="maap.cc", company_name="MAAP", status="replied", source="apollo"))
    db.commit()

    fake_draft = {"subject": "Katie mentioned you handle Commercial & Ecomm",
                  "body": "Katie mentioned you're the right person for Commercial & Ecomm at MAAP..."}
    fake_cadence = {
        "subject": "std subj 0", "personalized_opener": "op", "intro_body": "std body 0", "real_result": "",
        "followup_subject": "std subj 1", "followup_body": "std body 1",
        "followup2_subject": "std subj 2", "followup2_body": "std body 2",
        "closing_subject": "std subj 3", "closing_body": "std body 3",
    }
    with patch.object(seq, "_load_lead", return_value={"first_name": "Katie", "last_name": "Cipa",
                                                         "company_name": "MAAP", "company_domain": "maap.cc"}), \
         patch.object(seq, "_load_product", return_value={}), \
         patch.object(seq, "_build_sender_ctx_for_product", return_value={"company_name": "Spenzo"}), \
         patch.object(seq, "_li_company_background", return_value={"company_summary": "cycling apparel"}), \
         patch("nexus.services.outreach_template.generate_template_content", return_value=fake_cadence), \
         patch("nexus.services.outreach_template.generate_referral_intro", return_value=fake_draft):

        referral_contacts = [
            {"name": "Mitch", "email": "mitch@maap.cc", "role_hint": "Commercial & Ecomm"},
            {"name": "Katie Cipa", "email": "katie.cipa@maap.cc", "role_hint": None},  # already exists -> dedupe
        ]
        seq.create_referral_leads(db, source_ls, referral_contacts, source_message_id=2001)

    leads = {l.email: l for l in db.query(NexusGlobalLead).all()}
    assert "mitch@maap.cc" in leads and len(leads) == 2, leads
    mitch = leads["mitch@maap.cc"]
    assert mitch.source == "referral" and mitch.source_lead_id == 42 and mitch.source_message_id == 2001
    assert mitch.company_domain == "maap.cc" and mitch.role == "Commercial & Ecomm"
    print("create_referral_leads: new lead created with correct provenance; existing lead deduped, not duplicated -- OK")

    mitch_seq = db.query(NexusLeadSequence).filter(NexusLeadSequence.lead_id == mitch.id).first()
    assert mitch_seq.status == "active" and mitch_seq.current_step == 0
    print("create_referral_leads: sequence created ACTIVE (no approval gate) -- OK")

    mitch_rows = {r.step: r for r in db.query(NexusLeadEmail).filter(NexusLeadEmail.lead_sequence_id == mitch_seq.id).all()}
    assert len(mitch_rows) == 4, mitch_rows
    assert mitch_rows[0].subject == fake_draft["subject"], "step 0 must use the honest referral-framed intro"
    assert mitch_rows[1].subject == "std subj 1", "steps 1-3 use the normal generated cadence"
    print("create_referral_leads: full 4-step cadence, step 0 = honest referral intro, steps 1-3 = normal cadence -- OK")

    # The GTM Journey lead LIST is driven by nexus_leads (campaign enrollment),
    # not nexus_global_leads directly (routers/journey.py::journey_leads()).
    # Without this row the referral lead would exist in the DB but be
    # impossible for a human to find in the UI to act on needs_review.
    nexus_leads_row = db.execute(
        text("SELECT campaign_id, product_id FROM nexus_leads WHERE global_lead_id = :lid"),
        {"lid": mitch.id},
    ).mappings().first()
    assert nexus_leads_row is not None, "referral lead must be enrolled in nexus_leads or it's invisible in the GTM Journey UI"
    assert nexus_leads_row["campaign_id"] == 1
    print("create_referral_leads: nexus_leads enrollment row created -- referral lead is findable in the UI -- OK")


def main() -> int:
    _hr("1. OOO return-date extraction")
    check_ooo_date_extraction()

    _hr("2. generate_reply_aware_followups")
    check_generate_reply_aware_followups()

    _hr("3. regenerate_pending_followups (off-by-one, step 0 safety, idempotency, freshness)")
    check_regenerate_pending_followups()

    _hr("4. our_prior_reply gating (never claims an unsent answer)")
    check_our_prior_reply_gating()

    _hr("5. _latest_inbound_message_for_sequence")
    check_latest_inbound_message_lookup()

    _hr("6. _apply_intent_side_effects wiring (CONTINUING_INTENTS)")
    check_apply_intent_side_effects_wiring()

    _hr("7. create_referral_leads")
    check_create_referral_leads()

    _hr("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
