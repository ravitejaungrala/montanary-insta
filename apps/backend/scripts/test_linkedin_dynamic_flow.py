"""test_linkedin_dynamic_flow.py — offline tests for the dynamic LinkedIn
sequence: plan-as-data, the target cadence, and the reply decision table.

No DB, no network, no browser. Run:
    cd apps/backend
    python scripts/test_linkedin_dynamic_flow.py

Covers:
  1. The target cadence renders exactly as specified (§2 of the plan).
  2. The plan primitives (truncate / retime / insert / switch_branch).
  3. Backfill for leads enrolled before plans existed — including the refusal
     to guess an unplaceable step.
  4. The engine walks the plan and completes only when it is empty.
  5. Acceptance branching, including a LATE acceptance off the InMail branch.
  6. The intent -> operations decision table.
  7. The LinkedIn heuristic classifier on real-shaped short DMs.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from gtm.linkedin import plan, replies, sequence_engine  # noqa: E402

FAILURES = []


def check(label: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
        FAILURES.append(label)


# ── Fakes ─────────────────────────────────────────────────────────────────────
class FakeDB:
    def __init__(self):
        self.rows = []

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, *_a, **_k):
        return self

    def first(self):
        return None

    def fetchall(self):
        return []

    def query(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def filter_by(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return 0

    def count(self):
        return 0


class FakeState:
    def __init__(self, **kw):
        self.id = 1
        self.workspace_id = 10
        self.lead_id = 100
        self.campaign_id = 5
        self.linkedin_account_id = None
        self.linkedin_sequence_status = "active"
        self.linkedin_current_step = None
        self.linkedin_current_branch = "pending_acceptance"
        self.linkedin_connection_status = "none"
        self.linkedin_connection_accepted_at = None
        self.linkedin_message_reply_status = "none"
        self.last_reply_at = None
        self.workflow_mode = "standard"
        self.plan = None
        self.next_action_at = None
        self.deferral_count = 0
        self.defer_reason = None
        self.conversation_turns = 0
        self.flagged_reason = None
        self.flagged_at = None
        self.updated_at = None
        self.paused_reason = None
        self.__dict__.update(kw)


sequence_engine.record_event = lambda db, **kw: None


def _steps(p):
    return [t["step"] for t in p]


def _gaps(p):
    return [t["delay_days"] for t in p]


# ── 1. The target cadence ─────────────────────────────────────────────────────
def test_cadence():
    print("\n[1] Target cadence matches the spec")
    p = plan.build_plan("standard")
    check("connection request then 2 acceptance checks",
          _steps(p) == ["connection_request", "check_acceptance", "check_acceptance"],
          str(_steps(p)))
    check("checks land at T+3 and T+5", _gaps(p) == [0, 3, 2], str(_gaps(p)))

    a = plan.accepted_branch()
    check("accepted branch is opener + 2 follow-ups",
          _steps(a) == ["message_1", "message_2", "close_message"], str(_steps(a)))
    check("4-day gaps between DMs", _gaps(a) == [0, 4, 4], str(_gaps(a)))
    check("opener is NOT reply-gated (nothing to reply to yet)",
          a[0]["check_reply_first"] is False)
    check("every follow-up IS reply-gated",
          all(t["check_reply_first"] for t in a[1:]))

    i = plan.inmail_branch("standard")
    check("inmail branch is opener + ONE follow-up",
          _steps(i) == ["inmail_1", "inmail_followup"], str(_steps(i)))
    check("4-day InMail gap", _gaps(i) == [0, 4], str(_gaps(i)))

    c = plan.inmail_branch("combined")
    check("combined mode never re-sends the first InMail",
          _steps(c) == ["inmail_followup"], str(_steps(c)))

    h = plan.build_plan("hybrid")
    check("hybrid skips the connection phase entirely",
          _steps(h) == ["inmail_1", "inmail_followup"], str(_steps(h)))

    check("content steps are explicit on every send touch",
          all(t["content_step"] is not None for t in a + i))


# ── 2. Plan primitives ────────────────────────────────────────────────────────
def test_primitives():
    print("\n[2] Plan primitives")
    p = plan.accepted_branch()
    plan.mark(p, "a1", "sent")
    idx, nxt = plan.next_pending(p)
    check("next_pending skips sent touches", nxt["id"] == "a2", str(nxt))

    p2 = plan.truncate([dict(t) for t in p])
    check("truncate cancels all pending", plan.next_pending(p2) == (None, None))
    check("truncate preserves history",
          [t["status"] for t in p2] == ["sent", "cancelled", "cancelled"],
          str([t["status"] for t in p2]))

    p3 = plan.retime([dict(t) for t in plan.accepted_branch()], 30, only_next=True)
    check("retime shifts only the next touch", _gaps(p3) == [30, 4, 4], str(_gaps(p3)))

    p4 = [dict(t) for t in plan.accepted_branch()]
    plan.mark(p4, "a1", "sent")
    p4 = plan.insert_after(p4, {"id": "r1", "step": "reply", "kind": "message",
                                "role": "answer", "content_step": None, "delay_days": 0,
                                "check_reply_first": False, "status": "pending"})
    check("insert lands before the next pending touch",
          _steps(p4) == ["message_1", "reply", "message_2", "close_message"], str(_steps(p4)))
    check("inserted touch is what fires next", plan.next_pending(p4)[1]["id"] == "r1")

    p5 = [dict(t) for t in plan.build_plan("standard")]
    plan.mark(p5, "c1", "sent")
    p5 = plan.switch_branch(p5, plan.accepted_branch())
    check("switch_branch keeps history, replaces the tail",
          _steps(p5) == ["connection_request", "message_1", "message_2", "close_message"],
          str(_steps(p5)))

    check("unique ids for inserted touches",
          plan.next_touch_id([{"id": "r1"}, {"id": "r2"}], "r") == "r3")


# ── 3. Backfill ───────────────────────────────────────────────────────────────
def test_backfill():
    print("\n[3] Backfill for pre-plan leads")
    p, ok = plan.backfill_plan("standard", "accepted", "message_2")
    check("resolved mid-accepted-branch", ok)
    check("earlier touches marked sent", p[0]["status"] == "sent", str(_steps(p)))
    check("resumes at message_2", plan.next_pending(p)[1]["step"] == "message_2")

    # A legacy check step maps to the touch it was gating (now folded into it).
    p, ok = plan.backfill_plan("standard", "accepted", "check_reply_1")
    check("legacy check_reply_1 resolves to message_2",
          ok and plan.next_pending(p)[1]["step"] == "message_2")

    # The InMail branch shrank 4 -> 2, so a lead past the new end is complete.
    p, ok = plan.backfill_plan("standard", "inmail", "inmail_followup_2")
    check("lead past the shortened InMail branch completes", ok)
    check("nothing left to send", plan.next_pending(p) == (None, None))

    p, ok = plan.backfill_plan("standard", "accepted", "totally_unknown_step")
    check("unplaceable step is NOT guessed", ok is False)

    p, ok = plan.backfill_plan("standard", "pending_acceptance", None)
    check("never-dispatched lead gets a fresh plan",
          ok and _steps(p)[0] == "connection_request")


# ── 4-5. Engine walk + branching ──────────────────────────────────────────────
def test_engine():
    print("\n[4] Engine walks the plan")
    db = FakeDB()
    st = FakeState(plan=plan.build_plan("standard"), linkedin_current_step="connection_request")

    r = sequence_engine.after_send(db, st, "connection_request")
    check("connection request -> first acceptance check", r.get("next") == "check_acceptance")
    check("scheduled 3 days out", st.next_action_at is not None
          and 2.5 < (st.next_action_at - datetime.utcnow()).days + 1 < 4.5,
          str(st.next_action_at))

    r = sequence_engine.on_acceptance_result(db, st, "pending")
    check("still pending -> second check", r.get("next") == "check_acceptance")
    check("acceptance reported pending", r.get("acceptance") == "pending")

    r = sequence_engine.on_acceptance_result(db, st, "pending")
    check("checks exhausted -> InMail branch", r.get("acceptance") == "pending_timeout")
    check("first InMail is next", r.get("next") == "inmail_1", str(r))
    check("branch recorded", st.linkedin_current_branch == "inmail")

    print("\n[5] Late acceptance drops the InMail tail")
    r = sequence_engine.on_acceptance_result(db, st, "accepted")
    check("switches to the DM branch", r.get("acceptance") == "accepted")
    check("next touch is the DM opener", r.get("next") == "message_1", str(r))
    check("no InMail touches remain",
          not any(t["kind"] == "inmail" for t in plan.remaining(st.plan)),
          plan.summary(st.plan))

    # Walk to the end; completion happens only when the plan empties.
    sequence_engine.after_send(db, st, "message_1")
    sequence_engine.after_send(db, st, "message_2")
    r = sequence_engine.after_send(db, st, "close_message")
    check("completes when the plan is empty", r.get("sequence") == "completed", str(r))
    check("no dangling due-key", st.next_action_at is None)

    print("\n[5b] Declined invite skips straight to InMail")
    db2 = FakeDB()
    st2 = FakeState(plan=plan.build_plan("standard"), linkedin_current_step="check_acceptance")
    r = sequence_engine.on_acceptance_result(db2, st2, "declined")
    check("declined -> InMail immediately (not another check)",
          r.get("next") == "inmail_1", str(r))
    check("remaining acceptance checks dropped",
          not any(t["kind"] == "check_acceptance" for t in plan.remaining(st2.plan)))


# ── 6. Decision table ─────────────────────────────────────────────────────────
def test_decisions():
    print("\n[6] Intent -> plan operations")
    cases = [
        ("UNSUBSCRIBE", 0.9, "stop", True, False),
        ("NOT_INTERESTED", 0.9, "stop", False, False),
        ("INTERESTED", 0.9, "converse", False, False),
        ("QUESTION", 0.9, "converse", False, False),
        ("DEMO_SCHEDULED", 0.9, "stop", False, True),
        ("OUT_OF_OFFICE", 0.9, "continue", False, False),
        ("NOT_NOW", 0.9, "continue", False, False),
        ("LEFT_COMPANY", 0.9, "continue", False, True),
    ]
    for intent, conf, want, want_suppress, want_handoff in cases:
        d = replies.decide({"intent": intent, "confidence": conf, "actionable": True})
        check(f"{intent} -> {want}", d["action"] == want, f"got {d['action']}")
        check(f"{intent} suppress={want_suppress}", d["suppress"] is want_suppress)
        check(f"{intent} handoff={want_handoff}", d["handoff"] is want_handoff)

    # THE safety property: everything uncertain stops.
    d = replies.decide({"intent": "INTERESTED", "confidence": 0.3, "actionable": False})
    check("low confidence stops instead of acting", d["action"] == "stop", str(d))
    check("low confidence hands off to a human", d["handoff"] is True)
    d = replies.decide({"intent": None, "confidence": 0.0, "actionable": False})
    check("unclassifiable stops", d["action"] == "stop")
    d = replies.decide({"intent": "SOMETHING_NEW", "confidence": 0.99, "actionable": True})
    check("unknown intent stops rather than falling through", d["action"] == "stop", str(d))

    d = replies.decide({"intent": "OUT_OF_OFFICE", "confidence": 0.9, "actionable": True},
                       body="I'm on leave, back on September 12", received_at=datetime(2026, 9, 1))
    ops = dict((o[0], o[1]) for o in d["ops"] if isinstance(o, tuple))
    check("OOO retimes to the stated return date", ops.get("retime") == 12, str(d["ops"]))

    d = replies.decide({"intent": "OUT_OF_OFFICE", "confidence": 0.9, "actionable": True},
                       body="away from my desk", received_at=datetime(2026, 9, 1))
    ops = dict((o[0], o[1]) for o in d["ops"] if isinstance(o, tuple))
    check("OOO with no date uses a safe default", ops.get("retime") == 7, str(d["ops"]))


# ── 7. LinkedIn classifier heuristics ─────────────────────────────────────────
def test_classifier():
    print("\n[7] LinkedIn heuristic classifier (short-DM shaped)")
    from nexus.services.intent_classifier import _heuristic_linkedin as h
    cases = [
        ("who is this?", "QUESTION"),
        ("Who are you", "QUESTION"),
        ("do we know each other?", "QUESTION"),
        ("what is this about", "QUESTION"),
        ("how much does it cost?", "QUESTION"),
        ("stop messaging me", "UNSUBSCRIBE"),
        ("please remove me", "UNSUBSCRIBE"),
        ("don't contact me again", "UNSUBSCRIBE"),
        ("not interested", "NOT_INTERESTED"),
        ("no thanks", "NOT_INTERESTED"),
        ("we're all set", "NOT_INTERESTED"),
        ("not right now, ping me later", "NOT_NOW"),
        ("maybe next quarter", "NOT_NOW"),
        ("sure, tell me more", "INTERESTED"),
        ("sounds interesting", "INTERESTED"),
        ("yes please send it over", "INTERESTED"),
        ("I've left the company", "LEFT_COMPANY"),
        ("I no longer work there", "LEFT_COMPANY"),
        ("Tuesday 2pm works for me", "DEMO_SCHEDULED"),
        ("on annual leave until the 20th", "OUT_OF_OFFICE"),
    ]
    for body, want in cases:
        got = h(body).intent
        check(f"{body!r} -> {want}", got == want, f"got {got}")

    # A bare acknowledgement is a polite close, not enthusiasm — treating it as
    # interest produces a pushy follow-up on someone who was being courteous.
    for ack in ("ok", "thanks", "Thank you", "got it", "noted"):
        r = h(ack)
        check(f"bare {ack!r} is not INTERESTED", r.intent != "INTERESTED", f"got {r.intent}")
        check(f"bare {ack!r} is low confidence", r.confidence < 0.5, f"got {r.confidence}")

    # Ambiguous DMs land below the action threshold so the engine stops.
    r = h("hmm")
    check("ambiguous DM is below the action threshold",
          r.confidence < replies.MIN_CONFIDENCE, f"got {r.confidence}")


# ── 8. Conversation mode ──────────────────────────────────────────────────────
def test_conversation():
    print("\n[8] Conversation mode")
    db = FakeDB()
    st = FakeState(plan=plan.accepted_branch(), linkedin_current_step="message_2",
                   linkedin_current_branch="accepted")
    plan.mark(st.plan, "a1", "sent")

    d = {"action": "converse", "reason": "question",
         "ops": ["truncate", "insert_reply"], "suppress": False, "handoff": False}
    r = sequence_engine._apply_decision(db, st, d, message_id=7, intent="QUESTION")

    check("stays active while conversing", st.linkedin_sequence_status == "active",
          st.linkedin_sequence_status)
    check("next touch is the reply", r.get("next") == "reply", str(r))
    pending = plan.remaining(st.plan)
    check("cadence follow-ups were dropped",
          not any(t["id"] in ("a2", "a3") for t in pending), plan.summary(st.plan))
    reply = [t for t in pending if t.get("is_reply")]
    check("exactly one reply touch queued", len(reply) == 1, str(len(reply)))
    check("reply is NOT reply-gated (we're answering, not following up)",
          reply and reply[0]["check_reply_first"] is False)
    check("reply has no pre-generated content row",
          reply and reply[0]["content_step"] is None)
    delay = reply[0].get("delay_minutes") if reply else None
    lo, hi = sequence_engine.REPLY_DELAY_MINUTES
    check(f"reply lands {lo}-{hi} min out, not instantly",
          delay is not None and lo <= delay <= hi, f"got {delay}")
    check("reply scheduled in under a day",
          st.next_action_at is not None
          and 0 < (st.next_action_at - datetime.utcnow()).total_seconds() < 86400)

    nudge = [t for t in pending if t.get("role") == "close" and not t.get("is_reply")]
    check("one nudge queued after the reply", len(nudge) == 1, str(len(nudge)))
    check("nudge waits 4 days", nudge and nudge[0]["delay_days"] == 4)
    check("nudge IS reply-gated so it can't land mid-conversation",
          nudge and nudge[0]["check_reply_first"] is True)

    # The turn cap: an unbounded automated conversation is the worst failure mode.
    print("\n[8b] Conversation turn cap")
    db2 = FakeDB()
    st2 = FakeState(plan=plan.accepted_branch(), linkedin_current_step="message_2",
                    conversation_turns=sequence_engine.MAX_CONVERSATION_TURNS)
    r = sequence_engine._apply_decision(db2, st2, d, message_id=8, intent="QUESTION")
    check("stops at the cap instead of replying again",
          st2.linkedin_sequence_status == "stopped", st2.linkedin_sequence_status)
    check("stop reason names the cap", r.get("reason") == "conversation_cap", str(r))
    check("stops WITHOUT flagging a human (rep picks it up from the inbox)",
          st2.flagged_reason is None, str(st2.flagged_reason))
    check("nothing left queued", plan.next_pending(st2.plan) == (None, None))

    # An ungroundable question must produce silence + a flag, never a guess.
    print("\n[8c] Ungroundable question hands off rather than guessing")
    db3 = FakeDB()
    st3 = FakeState(plan=plan.accepted_branch(), linkedin_current_step="reply")
    r = sequence_engine.on_reply_undeliverable(db3, st3, reason="ungroundable")
    check("sequence stopped", st3.linkedin_sequence_status == "stopped")
    check("flagged for a human", st3.flagged_reason == "reply_ungroundable",
          str(st3.flagged_reason))
    check("no touches left to fire", plan.next_pending(st3.plan) == (None, None))

    # A stop must never leave a due-key behind — that is the stall invariant.
    print("\n[8d] Stop invariant")
    for label, state in (("cap", st2), ("handoff", st3)):
        check(f"{label}: no dangling next_action_at", state.next_action_at is None)


def main() -> int:
    print("=" * 72)
    print("Dynamic LinkedIn sequence — offline tests")
    print("=" * 72)
    test_cadence()
    test_primitives()
    test_backfill()
    test_engine()
    test_decisions()
    test_classifier()
    test_conversation()
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All dynamic-flow tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
