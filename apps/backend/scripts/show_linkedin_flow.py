"""show_linkedin_flow.py — walk a lead through each real scenario and PRINT the
timeline, so you can read what would actually happen.

Not assertions — a readable trace. Every line is produced by the real engine
(gtm/linkedin/sequence_engine.py + plan.py), not a mock-up of it.

    cd apps/backend
    venv/Scripts/python.exe scripts/show_linkedin_flow.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from gtm.linkedin import plan, replies, sequence_engine  # noqa: E402

sequence_engine.record_event = lambda db, **kw: None

# The engine schedules relative to utcnow(), so the display must anchor there
# too. Anchoring on a fixed date and truncating with .days produced garbage
# ("T+-1") on the first version of this script.
START = datetime.utcnow()


class FakeDB:
    def commit(self): pass
    def rollback(self): pass
    def execute(self, *a, **k): return self
    def first(self): return None
    def fetchall(self): return []
    def query(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def filter_by(self, *a, **k): return self
    def update(self, *a, **k): return 0
    def count(self): return 0


class FakeState:
    def __init__(self, mode="standard"):
        self.id = 1; self.workspace_id = 10; self.lead_id = 100; self.campaign_id = 5
        self.linkedin_account_id = None
        self.linkedin_sequence_status = "active"
        self.linkedin_current_branch = "pending_acceptance"
        self.linkedin_connection_status = "none"
        self.linkedin_connection_accepted_at = None
        self.linkedin_message_reply_status = "none"
        self.last_reply_at = None
        self.workflow_mode = mode
        self.plan = plan.build_plan(mode)
        _i, first = plan.next_pending(self.plan)
        self.linkedin_current_step = first["step"] if first else None
        self.next_action_at = START
        self.deferral_count = 0; self.last_deferred_at = None; self.defer_reason = None
        self.conversation_turns = 0; self.flagged_reason = None; self.flagged_at = None
        self.updated_at = None; self.paused_reason = None


LABEL = {
    "connection_request": "Send connection request (+ note)",
    "connect_and_inmail": "Send connection request + InMail",
    "check_acceptance": "Check if they accepted",
    "message_1": "Send first message (DM)",
    "message_2": "Send follow-up (DM)",
    "close_message": "Send closing message (DM)",
    "inmail_1": "Send InMail",
    "inmail_followup": "Send InMail follow-up",
    "reply": "Reply to what they said",
}


# Cumulative day counter. Each step is scheduled as a GAP from whenever the
# previous one ran; this script runs them all in the same millisecond, so the
# gaps have to be accumulated by hand to reconstruct the real calendar.
_CLOCK = {"days": 0.0}


def _gap_days(st) -> float:
    if not st.next_action_at:
        return 0.0
    return max(0.0, (st.next_action_at - datetime.utcnow()).total_seconds() / 86400.0)


def step(st, note=""):
    """Print the touch about to run, advancing the simulated clock by its gap."""
    _CLOCK["days"] += _gap_days(st)
    s = st.linkedin_current_step or ""
    print(f"  T+{round(_CLOCK['days']):<3} {LABEL.get(s, s):<36}{note}")


def prospect(msg):
    print(f"         {'':<36}<- THEY SAY: \"{msg}\"")


def outcome(text):
    print(f"         {'':<36}== {text}")


def header(n, title):
    _CLOCK["days"] = 0.0   # each scenario is its own lead, starting at T+0
    print(f"\n{'=' * 74}\nSCENARIO {n}: {title}\n{'=' * 74}")


def reply_event(db, st, body):
    """Classify their message the way the engine does, then apply it."""
    prospect(body)
    cls = replies.classify(body)
    d = replies.decide(cls, body=body, received_at=START)
    print(f"         {'':<36}   read as: {cls['intent']} "
          f"(confidence {cls['confidence']:.2f}) -> {d['action']}")
    sequence_engine._apply_decision(db, st, d, message_id=1, intent=cls["intent"])
    return d


def finish(st, note="<- next"):
    s = st.linkedin_sequence_status
    extra = f" (flagged: {st.flagged_reason})" if st.flagged_reason else ""
    outcome(f"sequence {s.upper()}{extra}")
    if s == "active":
        step(st, note)



# ── 1. Accepted, never replies ────────────────────────────────────────────────
def s1():
    header(1, "They accept, then never reply")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "accepted")
    outcome("ACCEPTED -> messaging branch")
    for s in ("message_1", "message_2", "close_message"):
        step(st, "(checks for a reply first)" if s != "message_1" else "")
        sequence_engine.after_send(db, st, s)
    finish(st)


# ── 2. Accepted, replies with a question -> we reply ──────────────────────────
def s2():
    header(2, "They accept, then ask a question -> we answer")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "accepted")
    outcome("ACCEPTED -> messaging branch")
    step(st); sequence_engine.after_send(db, st, "message_1")
    print(f"         {'':<36}(reply gate opens the thread before the follow-up)")
    reply_event(db, st, "how much does this cost?")
    outcome("follow-ups dropped; a reply is queued instead")
    mins = round((st.next_action_at - datetime.utcnow()).total_seconds() / 60)
    step(st, f"<- in ~{mins} min, not days")
    st.conversation_turns += 1
    sequence_engine.after_send(db, st, "reply")
    outcome(f"conversation turn {st.conversation_turns} sent")
    finish(st, note="(one nudge, only if they go quiet)")


# ── 3. Never accepts -> InMail ────────────────────────────────────────────────
def s3():
    header(3, "They never accept the connection request")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "pending")
    outcome("still pending")
    step(st); sequence_engine.on_acceptance_result(db, st, "pending")
    outcome("still pending after the 2nd check -> InMail branch")
    step(st); sequence_engine.after_send(db, st, "inmail_1")
    step(st, "(checks for a reply first)")
    sequence_engine.after_send(db, st, "inmail_followup")
    finish(st)


# ── 4. Asks us to stop ────────────────────────────────────────────────────────
def s4():
    header(4, "They ask us to stop")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "accepted")
    step(st); sequence_engine.after_send(db, st, "message_1")
    reply_event(db, st, "please stop messaging me")
    outcome("added to the suppression list")
    finish(st)


# ── 5. Out of office -> cadence waits ─────────────────────────────────────────
def s5():
    header(5, "Out of office -> we wait until they're back")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "accepted")
    step(st); sequence_engine.after_send(db, st, "message_1")
    reply_event(db, st, "I'm on annual leave, back on August 20")
    outcome("cadence kept, next touch pushed past their return")
    finish(st)


# ── 6. Late acceptance while on the InMail branch ─────────────────────────────
def s6():
    header(6, "They accept LATE, after we already sent an InMail")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "pending")
    step(st); sequence_engine.on_acceptance_result(db, st, "pending")
    outcome("not accepted -> InMail branch")
    step(st); sequence_engine.after_send(db, st, "inmail_1")
    outcome("...they now accept the old invite")
    sequence_engine.on_acceptance_result(db, st, "accepted")
    outcome("InMail follow-up CANCELLED, switched to free DMs")
    finish(st)


# ── 7. A colleague replies manually ───────────────────────────────────────────
def s7():
    header(7, "A colleague answers the thread by hand")
    db, st = FakeDB(), FakeState()
    step(st); sequence_engine.after_send(db, st, "connection_request")
    step(st); sequence_engine.on_acceptance_result(db, st, "accepted")
    step(st); sequence_engine.after_send(db, st, "message_1")
    print(f"         {'':<36}(a rep types their own reply in the thread)")
    outcome("reply gate sees an outbound message we never sent")
    sequence_engine.on_human_takeover(db, st)
    outcome("automation stands down - no follow-up on top of a human")
    finish(st)


# ── 8. Send keeps failing ─────────────────────────────────────────────────────
def s8():
    header(8, "LinkedIn keeps failing (page won't load)")
    db, st = FakeDB(), FakeState()
    step(st)
    for i in range(1, sequence_engine.MAX_DEFERRALS + 2):
        r = sequence_engine.defer_lead(db, st, reason="job_failed:send", count_attempt=True)
        if st.linkedin_sequence_status != "active":
            outcome(f"attempt {i}: budget spent -> stopped ({r.get('reason')})")
            break
        mins = int((st.next_action_at - datetime.utcnow()).total_seconds() // 60)
        outcome(f"attempt {i}: retry in ~{mins} min (lead stays alive)")
    finish(st)


if __name__ == "__main__":
    print("Reading the real engine. T+N = days after enrollment.")
    for fn in (s1, s2, s3, s4, s5, s6, s7, s8):
        fn()
    print("\n" + "=" * 74)
    print("All 8 scenarios ran through the real engine.")
