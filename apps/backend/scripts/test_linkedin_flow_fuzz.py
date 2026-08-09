"""test_linkedin_flow_fuzz.py — randomized state-machine fuzzing for the dynamic
LinkedIn sequence.

The example-based suites check the paths we thought of. This walks thousands of
leads through RANDOM event sequences (accept / decline / pending / reply with any
intent / send failure / no reply) and asserts the invariants after EVERY
transition. It exists to find the transitions nobody wrote a test for.

Invariants (a violation of any one is a real production bug):

  I1  active  => next_action_at IS NOT NULL
      The stall bug. dispatch_due filters on next_action_at, so an active lead
      without one is unreachable forever while still reading as active.
  I2  stopped/completed => next_action_at IS NULL
      A dangling due-key on a dead lead re-dispatches work that should not run.
  I3  A touch is never sent twice.
      Double-sending is the most visible possible failure to a prospect.
  I4  Terminal states are terminal — never back to active.
  I5  conversation_turns never exceeds MAX_CONVERSATION_TURNS.
      An unbounded automated conversation is the worst failure mode in the design.
  I6  Total sends per lead stay bounded (no cadence loop).
  I7  Plan integrity: unique ids, valid statuses, required fields present.
  8   deferral_count never exceeds MAX_DEFERRALS while still active.

Run:
    cd apps/backend
    venv/Scripts/python.exe scripts/test_linkedin_flow_fuzz.py [--leads 3000] [--seed 1]
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from gtm.linkedin import plan, replies, sequence_engine  # noqa: E402

sequence_engine.record_event = lambda db, **kw: None

# Upper bound on touches any single lead should ever receive. The longest legal
# path is: connection + 2 acceptance checks + 3 DMs, plus (per conversation turn)
# a reply and a nudge. Well clear of that, but finite — I6 is really "does this
# terminate at all".
MAX_SENDS_PER_LEAD = 14
MAX_TRANSITIONS = 60

VIOLATIONS = []


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
        self.id = 1
        self.workspace_id = 10
        self.lead_id = 100
        self.campaign_id = 5
        self.linkedin_account_id = None
        self.linkedin_sequence_status = "active"
        self.linkedin_current_branch = "inmail" if mode == "hybrid" else "pending_acceptance"
        self.linkedin_connection_status = "none"
        self.linkedin_connection_accepted_at = None
        self.linkedin_message_reply_status = "none"
        self.last_reply_at = None
        self.workflow_mode = mode
        self.plan = plan.build_plan(mode)
        _i, first = plan.next_pending(self.plan)
        self.linkedin_current_step = first["step"] if first else None
        self.next_action_at = datetime.utcnow()
        self.deferral_count = 0
        self.last_deferred_at = None
        self.defer_reason = None
        self.conversation_turns = 0
        self.flagged_reason = None
        self.flagged_at = None
        self.updated_at = None
        self.paused_reason = None


def violate(msg, ctx):
    key = msg
    VIOLATIONS.append((msg, ctx))
    if len(VIOLATIONS) <= 8:
        print(f"  VIOLATION: {msg}\n             {ctx}")
    return key


def check_invariants(st, sends, where, history):
    ctx = (f"at={where} status={st.linkedin_sequence_status} step={st.linkedin_current_step} "
           f"next_at={st.next_action_at is not None} turns={st.conversation_turns} "
           f"plan={plan.summary(st.plan)} history={history[-6:]}")
    status = st.linkedin_sequence_status

    if status == "active" and st.next_action_at is None:
        violate("I1 active lead has no next_action_at (STRANDED)", ctx)
    if status in ("stopped", "completed") and st.next_action_at is not None:
        violate("I2 terminal lead still has a due-key", ctx)
    dupes = [t for t, n in sends.items() if n > 1]
    if dupes:
        violate(f"I3 touch(es) sent more than once: {dupes}", ctx)
    if int(st.conversation_turns or 0) > sequence_engine.MAX_CONVERSATION_TURNS:
        violate("I5 conversation turns exceeded the cap", ctx)
    if sum(sends.values()) > MAX_SENDS_PER_LEAD:
        violate(f"I6 sent {sum(sends.values())} touches (> {MAX_SENDS_PER_LEAD})", ctx)
    if status == "active" and int(st.deferral_count or 0) > sequence_engine.MAX_DEFERRALS:
        violate("I8 deferral budget exceeded while still active", ctx)

    ids = [t.get("id") for t in (st.plan or [])]
    if len(ids) != len(set(ids)):
        violate("I7 duplicate touch ids in plan", ctx)
    for t in (st.plan or []):
        if t.get("status") not in ("pending", "sent", "skipped", "cancelled"):
            violate(f"I7 invalid touch status {t.get('status')!r}", ctx)
        if not t.get("step") or not t.get("kind"):
            violate(f"I7 touch missing step/kind: {t}", ctx)


INTENTS = ["INTERESTED", "QUESTION", "NOT_NOW", "OUT_OF_OFFICE", "LEFT_COMPANY",
           "NOT_INTERESTED", "UNSUBSCRIBE", "DEMO_SCHEDULED", None]


def run_one(rng, mode, outcomes):
    db = FakeDB()
    st = FakeState(mode)
    sends = Counter()
    history = []
    prev_terminal = False

    for _ in range(MAX_TRANSITIONS):
        if st.linkedin_sequence_status != "active":
            prev_terminal = True
            break

        touch = plan.find_by_step(st.plan, st.linkedin_current_step or "")
        if touch is None:
            # No pending touch matches the current step. Legal only if the plan
            # is empty (engine should have completed it).
            if plan.next_pending(st.plan) != (None, None):
                violate("current_step does not match any pending touch",
                        f"step={st.linkedin_current_step} plan={plan.summary(st.plan)}")
            break

        kind = touch.get("kind")
        roll = rng.random()

        if kind == "check_acceptance":
            r = rng.choices(["accepted", "pending", "declined"], weights=[35, 50, 15])[0]
            history.append(f"acc:{r}")
            outcomes[f"acceptance_{r}"] += 1
            sequence_engine.on_acceptance_result(db, st, r)

        elif roll < 0.12:
            # Send failed (browser error, ambiguous read, cap hit mid-run).
            history.append("fail")
            outcomes["send_failed"] += 1
            sequence_engine.defer_lead(db, st, reason="job_failed:fuzz", count_attempt=True)

        elif touch.get("check_reply_first") and roll < 0.45:
            # A reply was found by the gate before this send.
            intent = rng.choice(INTENTS)
            actionable = intent is not None and rng.random() < 0.8
            history.append(f"reply:{intent}:{'act' if actionable else 'unact'}")
            outcomes[f"reply_{intent}"] += 1
            decision = replies.decide({"intent": intent, "confidence": 0.9 if actionable else 0.2,
                                       "actionable": actionable})
            sequence_engine._apply_decision(db, st, decision, message_id=1, intent=intent)

        else:
            # The send goes out.
            tid = touch["id"]
            sends[tid] += 1
            history.append(f"send:{touch['step']}")
            outcomes["sent"] += 1
            if touch.get("is_reply"):
                # Mirror what the worker does after a reply actually lands.
                st.conversation_turns = int(st.conversation_turns or 0) + 1
            sequence_engine.after_send(db, st, touch["step"])

        check_invariants(st, sends, history[-1], history)

    # ── Post-terminal: late events must NOT resurrect a dead lead ─────────────
    # A real scenario, not a hypothetical: the inbox sweep runs every few hours
    # and can surface a reply on a thread whose sequence already stopped
    # (unsubscribed, handed to a human, out of InMail credit). Re-arming that
    # lead would resume messaging someone we had explicitly stopped messaging.
    if prev_terminal:
        terminal_status = st.linkedin_sequence_status
        for _ in range(4):
            ev = rng.choice(["reply", "advance", "defer"])
            outcomes[f"post_terminal_{ev}"] += 1
            try:
                if ev == "reply":
                    intent = rng.choice(INTENTS)
                    decision = replies.decide({"intent": intent, "confidence": 0.9,
                                               "actionable": intent is not None})
                    sequence_engine._apply_decision(db, st, decision, message_id=2, intent=intent)
                elif ev == "advance":
                    sequence_engine.after_send(db, st, st.linkedin_current_step or "message_1")
                else:
                    sequence_engine.defer_lead(db, st, reason="late_failure", count_attempt=True)
            except Exception as e:
                violate(f"post-terminal event raised: {type(e).__name__}: {e}",
                        f"ev={ev} status={terminal_status}")
                break
            if st.linkedin_sequence_status == "active":
                violate("I4 lead RESURRECTED from a terminal state",
                        f"was={terminal_status} after={ev} history={history[-5:]}")
                break
            if st.next_action_at is not None:
                violate("I2 terminal lead acquired a due-key from a late event",
                        f"was={terminal_status} after={ev}")
                break
            check_invariants(st, sends, f"post-terminal:{ev}", history)

    return sends, history


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)

    print("=" * 72)
    print(f"LinkedIn flow fuzz — {args.leads} leads, seed {seed}")
    print("=" * 72)

    outcomes = Counter()
    terminal = Counter()
    total_sends = Counter()
    for i in range(args.leads):
        mode = rng.choice(["standard", "standard", "combined", "hybrid"])
        try:
            sends, history = run_one(rng, mode, outcomes)
            total_sends[sum(sends.values())] += 1
        except Exception as e:  # an unhandled exception IS a failure
            violate(f"unhandled exception: {type(e).__name__}: {e}", f"lead #{i} mode={mode}")

    print("\nEvent coverage:")
    for k, v in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>7}  {k}")
    print("\nTouches sent per lead:")
    for k in sorted(total_sends):
        print(f"  {k:>3} touches: {total_sends[k]:>6} leads")

    print("\n" + "=" * 72)
    if VIOLATIONS:
        counts = Counter(v[0] for v in VIOLATIONS)
        print(f"{len(VIOLATIONS)} INVARIANT VIOLATION(S) across {args.leads} leads:")
        for k, n in counts.most_common():
            print(f"  {n:>6}x  {k}")
        print(f"\nReproduce with: --seed {seed}")
        return 1
    print(f"No invariant violations across {args.leads} randomized leads (seed {seed}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
