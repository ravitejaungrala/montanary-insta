"""20-lead reply simulation — exercises the full reply-handling pipeline
across every intent + several edge cases, with NOTHING mocked past the
"lead replied" boundary: real Gemini classification, real
regenerate_pending_followups, real create_referral_leads, real halting
logic. Only the OUTER boundary is simulated — no real emails are sent to
anyone (all addresses use the RFC 2606 reserved .invalid TLD), and the
"initial email sent" step is synthesized (a real touchpoint row + advanced
current_step) rather than actually dispatched, since the send mechanics
were already verified separately with real mailboxes.

Run from apps/backend with the backend venv:
  ./venv/Scripts/python.exe scripts/test_mail_simulation.py

Writes a machine-readable summary to stdout; Test_mail.md is written
separately from the printed results.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: F401,E402
import nexus.models  # noqa: F401,E402
import nexus.models_phase2  # noqa: F401,E402
import nexus.models_phase3  # noqa: F401,E402
import nexus.models_phase4  # noqa: F401,E402
import nexus.models_phase5  # noqa: F401,E402

from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from nexus.models_phase4 import NexusLeadSequence, NexusLeadEmail, NexusTouchpoint  # noqa: E402
from nexus.services import sequencer as seq  # noqa: E402
from nexus.routers.inbound import _ingest_inbound_message  # noqa: E402
from nexus.services.ms_mail_sync import _upsert_inbound_lead  # noqa: E402

WORKSPACE_ID = 7
CAMPAIGN_ID = 82
PRODUCT_ID = 24
DOMAIN = "example-test.invalid"  # RFC 2606 reserved — never a real address

# ── Scenario definitions ──────────────────────────────────────────────────
# Each: (id, first, last, family, reply_subject_suffix, reply_body, notes)
SCENARIOS = [
    dict(
        id=1, first="Ava", last="Bennett", family="OOO_date_no_referral",
        body="I'm on leave until August 3rd. Will follow up once I'm back at my desk.",
        expect="continue", notes="OOO with a parseable return date, no referral named.",
    ),
    dict(
        id=2, first="Ben", last="Ortiz", family="OOO_date_with_referral",
        body=(
            "Out of office until August 5. For anything urgent in the meantime, please "
            f"contact Dana Kim at dana.kim.sim02@{DOMAIN}, she covers this for me."
        ),
        expect="continue+referral", notes="OOO + parseable date + a referral WITH an email.",
    ),
    dict(
        id=3, first="Cara", last="Singh", family="OOO_referral_no_email",
        body="Out of office, back next week. My colleague Sam handles this in the meantime.",
        expect="continue", notes="Referral named but NO email given -> must NOT create a lead.",
    ),
    dict(
        id=4, first="Deshawn", last="Patel", family="OOO_no_date",
        body="I'm currently out of office. Will respond when I'm back.",
        expect="continue", notes="OOO with no parseable date -> falls back to LLM-recommended delay.",
    ),
    dict(
        id=5, first="Elena", last="Wu", family="NOT_NOW",
        body="Not a priority for us right now — maybe check back next quarter.",
        expect="continue", notes="Patient defer, should read lower-pressure and space out.",
    ),
    dict(
        id=6, first="Felix", last="Novak", family="QUESTION",
        body="What exactly does your platform integrate with on the data side?",
        expect="continue", notes="Generic question.",
    ),
    dict(
        id=7, first="Grace", last="Ibrahim", family="QUESTION_PRICE",
        body="What does pricing look like for a team our size, roughly 40 people?",
        expect="continue", notes="Pricing question.",
    ),
    dict(
        id=8, first="Hassan", last="Okafor", family="INTERESTED",
        body="This looks interesting — tell me more about how it would work for us.",
        expect="continue", notes="Positive interest, should keep cadence (post-revision behavior).",
    ),
    dict(
        id=9, first="Ines", last="Larsen", family="NOT_INTERESTED",
        body="Not interested, please don't reach out again.",
        expect="stop", notes="Clear negative, no unsubscribe-specific wording.",
    ),
    dict(
        id=10, first="Jonas", last="Almeida", family="UNSUBSCRIBE",
        body="Please unsubscribe me from this list.",
        expect="stop+suppress", notes="Explicit opt-out -> suppression list + halted.",
    ),
    dict(
        id=11, first="Kavya", last="Reddy", family="DEMO_SCHEDULED",
        body="Confirmed — Tuesday at 2pm works for the demo, talk then.",
        expect="stop", notes="Booking-confirmation language -> halted, handoff to booking flow.",
    ),
    dict(
        id=12, first="Liam", last="Fitzgerald", family="SECOND_REPLY_RETRIGGER",
        body="Not a priority right now, maybe next quarter.",
        body2="Actually, ignore my last note — let's talk sooner than I thought, this week if possible.",
        expect="continue+retrigger", notes="Two replies in the same thread; second must re-regenerate.",
    ),
    dict(
        id=13, first="Maya", last="Kowalski", family="REFERRAL_VIA_NOT_NOW",
        body=(
            "Not the right time for me, but you should talk to my colleague Priya Shah "
            f"(priya.shah.sim13@{DOMAIN}) — she handles procurement."
        ),
        expect="continue+referral", notes="Referral extraction on a NON-OOO continuing intent.",
    ),
    dict(
        id=14, first="Noah", last="Dubois", family="REFERRAL_VIA_INTERESTED",
        body=(
            "Yes, interested — loop in our ops lead Marcus Chen at "
            f"marcus.chen.sim14@{DOMAIN} too, he'll want visibility."
        ),
        expect="continue+referral", notes="Referral extraction alongside a positive intent.",
    ),
    dict(
        id=15, first="Olga", last="Petrova", family="REFERRAL_DUPLICATE",
        body="I'm out of this loop now — {DUP_EMAIL} already knows the full context, go direct.",
        expect="continue+dedupe", notes="Names an email that's ALREADY an existing lead -> must dedupe, not duplicate.",
    ),
    dict(
        id=16, first="Pavel", last="Novotny", family="TERSE_NOT_INTERESTED",
        body="No.",
        expect="stop", notes="Minimal-signal negative reply.",
    ),
    dict(
        id=17, first="Quinn", last="Sullivan", family="MIXED_SIGNALS",
        body="Not sure this is for us right now, but out of curiosity what's the pricing anyway?",
        expect="continue", notes="Ambiguous NOT_NOW vs QUESTION_PRICE — classifier judgment call.",
    ),
    dict(
        id=18, first="Ravi", last="Menon", family="LONG_RAMBLING",
        body=(
            "Thanks for reaching out — sorry for the slow reply, it's been a chaotic few weeks "
            "here with a product launch and two people out sick, so a lot of things slipped. "
            "Anyway, I did finally get a chance to read through what you sent, and honestly it's "
            "more relevant than I expected given some of the data mess we've been dealing with "
            "internally. I'd be up for learning more when things calm down a bit here."
        ),
        expect="continue", notes="Long, rambling text that eventually lands on genuine interest.",
    ),
    dict(
        id=19, first="Sofia", last="Berg", family="TWO_REFERRALS",
        body=(
            "I'm out, but you can reach either Alex Rivera "
            f"(alex.rivera.sim19@{DOMAIN}) or Jamie Lee (jamie.lee.sim19@{DOMAIN}) — "
            "either one can pick this up."
        ),
        expect="continue+referral2", notes="Two distinct referral contacts in one reply.",
    ),
    dict(
        id=20, first="Tobias", last="Nakamura", family="NEGATIVE_WITH_REFERRAL_NAME",
        body="Not interested, but you could try Taylor on our team if you want.",
        expect="stop", notes="Negative intent that ALSO names someone — documents the known scope "
                             "limit (referral extraction only runs for continuing intents today).",
    ),
]


def _hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def setup_test_sequence(db):
    seq_id = db.execute(text(
        "INSERT INTO nexus_sequences (workspace_id, campaign_id, name, steps, created_at, updated_at) "
        "VALUES (:ws, :camp, 'SIMULATION TEST — 20 scenarios', CAST(:steps AS jsonb), NOW(), NOW()) RETURNING id"
    ), {
        "ws": WORKSPACE_ID, "camp": CAMPAIGN_ID,
        "steps": (
            '[{"order":0,"channel":"email","delay_days":0},'
            '{"order":1,"channel":"email","delay_days":3},'
            '{"order":2,"channel":"email","delay_days":3},'
            '{"order":3,"channel":"email","delay_days":3}]'
        ),
    }).scalar()
    db.commit()
    return seq_id


def setup_lead(db, sequence_id, scenario):
    email = f"{scenario['first'].lower()}.{scenario['last'].lower()}.sim{scenario['id']:02d}@{DOMAIN}"
    db.execute(text("DELETE FROM nexus_global_leads WHERE email = :e"), {"e": email})
    db.commit()

    lead_id = db.execute(text(
        "INSERT INTO nexus_global_leads "
        "(email, first_name, last_name, company_name, status, source, priority, "
        " email_verified, email_verify_score, created_at, updated_at) "
        "VALUES (:email, :fn, :ln, 'Simulation Co', 'contacted', 'manual_test', 'active', "
        " false, 0, NOW(), NOW()) RETURNING id"
    ), {"email": email, "fn": scenario["first"], "ln": scenario["last"]}).scalar()

    db.execute(text(
        "INSERT INTO nexus_leads (workspace_id, campaign_id, product_id, global_lead_id, icp_score, created_at, updated_at) "
        "VALUES (:ws, :camp, :prod, :lid, 75, NOW(), NOW())"
    ), {"ws": WORKSPACE_ID, "camp": CAMPAIGN_ID, "prod": PRODUCT_ID, "lid": lead_id})

    lead_sequence_id = db.execute(text(
        "INSERT INTO nexus_lead_sequences "
        "(workspace_id, campaign_id, lead_id, sequence_id, current_step, next_action_at, status, enrolled_at) "
        "VALUES (:ws, :camp, :lead, :seq, 1, NOW() + INTERVAL '3 days', 'active', NOW()) RETURNING id"
    ), {"ws": WORKSPACE_ID, "camp": CAMPAIGN_ID, "lead": lead_id, "seq": sequence_id}).scalar()
    db.commit()

    # Real generation for the 4-step cadence (matches production — this is
    # what a genuinely enrolled lead would have).
    ls = db.query(NexusLeadSequence).filter(NexusLeadSequence.id == lead_sequence_id).first()
    lead_dict = {
        "first_name": scenario["first"], "last_name": scenario["last"],
        "company_name": "Simulation Co", "company_domain": None, "email": email,
    }
    product = seq._load_product(db, WORKSPACE_ID, campaign_id=CAMPAIGN_ID) or {}
    sender_ctx = seq._build_sender_ctx_for_product(db, product)
    seq._ensure_lead_emails(db, ls, lead_dict, sender_ctx, {})

    # Synthesize "initial already sent" — a real touchpoint row, no actual
    # dispatch (send mechanics verified separately with real mailboxes).
    step0 = db.query(NexusLeadEmail).filter(
        NexusLeadEmail.lead_sequence_id == lead_sequence_id, NexusLeadEmail.step == 0
    ).first()
    db.add(NexusTouchpoint(
        lead_sequence_id=lead_sequence_id, step=0, subject=step0.subject if step0 else "",
        body=step0.body if step0 else "", status="sent", workspace_id=WORKSPACE_ID,
        lead_id=lead_id, campaign_id=CAMPAIGN_ID, channel="email", sent_at=datetime.utcnow(),
    ))
    db.commit()
    return lead_id, lead_sequence_id, email


def simulate_reply(db, lead_sequence_id, from_email, subject, body_text, suffix):
    inbound_lead_id = _upsert_inbound_lead(db, WORKSPACE_ID, from_email)
    result = _ingest_inbound_message(
        db,
        workspace_id=WORKSPACE_ID,
        from_email=from_email,
        to_email="sim-sender@neuzenai.com",
        subject=subject,
        body_text=body_text,
        body_html=None,
        message_id_header=f"sim-{suffix}@{DOMAIN}",
        in_reply_to_header=None,
        inbound_lead_id=inbound_lead_id,
        lead_sequence_id=lead_sequence_id,
    )
    return result


def collect_state(db, lead_id, lead_sequence_id):
    ls = db.execute(text(
        "SELECT status, halt_reason, next_action_at FROM nexus_lead_sequences WHERE id=:id"
    ), {"id": lead_sequence_id}).mappings().first()
    steps = db.execute(text(
        "SELECT step, regenerated_from_message_id IS NOT NULL AS regenerated "
        "FROM nexus_lead_emails WHERE lead_sequence_id=:id ORDER BY step"
    ), {"id": lead_sequence_id}).fetchall()
    referrals = db.execute(text(
        "SELECT gl.email, gl.role, nl.icp_score FROM nexus_global_leads gl "
        "JOIN nexus_lead_sequences ls2 ON ls2.lead_id = gl.id "
        "LEFT JOIN nexus_leads nl ON nl.global_lead_id = gl.id "
        "WHERE gl.source_lead_id = :lid"
    ), {"lid": lead_id}).mappings().all()
    suppressed = db.execute(text(
        "SELECT 1 FROM nexus_suppression_list WHERE workspace_id=:ws AND email_lower = "
        "(SELECT lower(email) FROM nexus_global_leads WHERE id=:lid)"
    ), {"ws": WORKSPACE_ID, "lid": lead_id}).first()
    return {
        "sequence_status": ls["status"] if ls else None,
        "halt_reason": ls["halt_reason"] if ls else None,
        "regenerated_steps": [r[0] for r in steps if r[1]],
        "referrals": [dict(r) for r in referrals],
        "suppressed": bool(suppressed),
    }


def main() -> int:
    db = SessionLocal()
    results = []
    try:
        seq_id = setup_test_sequence(db)
        print(f"Created simulation sequence id={seq_id}")

        lead_ids_by_scenario = {}
        # Pass 1: create all 20 leads first (so scenario 15's duplicate
        # referral can reference an already-existing lead's email).
        for sc in SCENARIOS:
            lead_id, lead_sequence_id, email = setup_lead(db, seq_id, sc)
            lead_ids_by_scenario[sc["id"]] = (lead_id, lead_sequence_id, email)
            print(f"  [{sc['id']:2d}] {sc['first']} {sc['last']} <{email}> -> lead={lead_id} seq={lead_sequence_id}")

        dup_email = lead_ids_by_scenario[1][2]  # scenario 15 refers to scenario 1's email

        _hr("Simulating replies")
        for sc in SCENARIOS:
            lead_id, lead_sequence_id, email = lead_ids_by_scenario[sc["id"]]
            body = sc["body"].replace("{DUP_EMAIL}", dup_email)
            ingest_result = simulate_reply(
                db, lead_sequence_id, email,
                f"Re: {sc['family']} test", body, f"{sc['id']:02d}a",
            )
            print(f"  [{sc['id']:2d}] {sc['family']}: intent={ingest_result.get('intent')}")

            if "body2" in sc:
                ingest_result2 = simulate_reply(
                    db, lead_sequence_id, email,
                    f"Re: {sc['family']} test", sc["body2"], f"{sc['id']:02d}b",
                )
                print(f"       -> 2nd reply: intent={ingest_result2.get('intent')}")

            state = collect_state(db, lead_id, lead_sequence_id)
            results.append({"scenario": sc, "lead_id": lead_id, "lead_sequence_id": lead_sequence_id,
                             "email": email, "intent": ingest_result.get("intent"), "state": state})

        _hr("RESULTS")
        for r in results:
            print(f"\n[{r['scenario']['id']:2d}] {r['scenario']['family']}")
            print(f"     intent={r['intent']}  seq_status={r['state']['sequence_status']}  "
                  f"halt_reason={r['state']['halt_reason']}  regenerated_steps={r['state']['regenerated_steps']}")
            if r["state"]["referrals"]:
                print(f"     referrals created: {r['state']['referrals']}")
            if r["state"]["suppressed"]:
                print("     suppressed: True")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
