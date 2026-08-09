"""test_linkedin_defer.py — offline tests for deferral (the retry mechanism
that replaced SQS redelivery).

Context: since commit b9c5cbb the Fargate worker DELETES a failed job's SQS
message instead of letting it redeliver (one poisoned job was blocking the whole
per-account FIFO group). `dispatch_due` clears `next_action_at` when it enqueues,
so any worker path that returns without rescheduling leaves the lead
`active` + `next_action_at IS NULL` — stranded forever, and invisible because it
still reads as active.

THE INVARIANT under test: every non-terminal outcome re-arms next_action_at.

No DB, no network, no browser — pure logic with fakes. Run:
    cd apps/backend
    python scripts/test_linkedin_defer.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from gtm.linkedin import scheduler, sequence_engine  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────────────
class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    # stop_lead cancels pending jobs through a query chain; make it a no-op.
    def query(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return 0


class FakeState:
    def __init__(self, **kw):
        self.id = 1
        self.workspace_id = 10
        self.lead_id = 100
        self.campaign_id = 5
        self.linkedin_account_id = None      # → scheduler._settings not consulted
        self.linkedin_sequence_status = "active"
        self.linkedin_current_step = "message_1"
        self.linkedin_current_branch = "accepted"
        self.next_action_at = None           # as dispatch_due leaves it
        self.deferral_count = 0
        self.last_deferred_at = None
        self.defer_reason = None
        self.updated_at = None
        self.plan = None
        self.__dict__.update(kw)


_EVENTS = []


def _fake_record_event(db, **kw):
    _EVENTS.append(kw)
    return None


# Patch the event writer in both modules that reference it.
sequence_engine.record_event = _fake_record_event


# ── Assertions ────────────────────────────────────────────────────────────────
FAILURES = []


def check(label: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
        FAILURES.append(label)


def main() -> int:
    print("=" * 72)
    print("LinkedIn deferral — offline tests")
    print("=" * 72)

    # 1) The core invariant: a defer always produces a future due-key.
    print("\n[1] defer_lead re-arms next_action_at")
    db, st = FakeDB(), FakeState()
    before = datetime.utcnow()
    res = sequence_engine.defer_lead(db, st, reason="job_failed:send_message", count_attempt=True)
    check("next_action_at is set", st.next_action_at is not None)
    check("next_action_at is in the future", bool(st.next_action_at and st.next_action_at > before))
    check("sequence stays active", st.linkedin_sequence_status == "active")
    check("reason recorded", st.defer_reason == "job_failed:send_message")
    check("attempt counted", st.deferral_count == 1, f"got {st.deferral_count}")
    check("returns deferred marker", res.get("deferred") == "job_failed:send_message")

    # 2) Account-level reasons must not consume the lead's retry budget —
    #    otherwise someone else's rate limit would stop this lead.
    print("\n[2] account-level defers do not count against the lead")
    db, st = FakeDB(), FakeState()
    for _ in range(10):
        sequence_engine.defer_lead(db, st, reason="daily_cap", delay_hours=14, count_attempt=False)
    check("deferral_count still 0 after 10 skips", st.deferral_count == 0, f"got {st.deferral_count}")
    check("still active", st.linkedin_sequence_status == "active")
    check("still has a due-key", st.next_action_at is not None)

    # 3) Backoff grows, then the lead is stopped rather than retried forever.
    print("\n[3] backoff escalates, then stops at MAX_DEFERRALS")
    db, st = FakeDB(), FakeState()
    gaps = []
    for _ in range(sequence_engine.MAX_DEFERRALS):
        sequence_engine.defer_lead(db, st, reason="job_failed:x", count_attempt=True)
        gaps.append((st.next_action_at - datetime.utcnow()).total_seconds() / 3600.0)
    check("escalating backoff", all(b >= a - 0.5 for a, b in zip(gaps, gaps[1:])),
          f"gaps(h)={[round(g, 1) for g in gaps]}")
    check("still active at the cap", st.linkedin_sequence_status == "active")
    res = sequence_engine.defer_lead(db, st, reason="job_failed:x", count_attempt=True)
    check("stopped past the cap", st.linkedin_sequence_status == "stopped",
          f"got {st.linkedin_sequence_status}")
    check("stop reason names the cause", "max_deferrals" in (res.get("reason") or ""),
          f"got {res.get('reason')}")
    check("no dangling due-key after stop", st.next_action_at is None)

    # 4) A successful advance resets the budget, so unrelated failures much
    #    later in a healthy sequence don't inherit early transient ones.
    print("\n[4] a successful step clears the retry budget")
    db, st = FakeDB(), FakeState(deferral_count=4, defer_reason="job_failed:x",
                                linkedin_account_id=None)
    scheduler.schedule_step(db, st, current_step="message_2", delay_days=4)
    check("deferral_count reset", st.deferral_count == 0, f"got {st.deferral_count}")
    check("defer_reason cleared", st.defer_reason is None)
    check("step advanced", st.linkedin_current_step == "message_2")
    check("due-key set ~4d out",
          st.next_action_at is not None
          and timedelta(days=3, hours=20) < (st.next_action_at - datetime.utcnow()) < timedelta(days=5))

    # 5) An already-stopped lead must not be resurrected by a late failure.
    print("\n[5] defer never resurrects a stopped lead")
    db, st = FakeDB(), FakeState(linkedin_sequence_status="stopped", next_action_at=None)
    sequence_engine.defer_lead(db, st, reason="job_failed:late", count_attempt=True)
    check("still stopped", st.linkedin_sequence_status == "stopped")
    # defer_lead itself does not gate on status (worker._defer does), so this
    # documents the contract boundary rather than asserting a no-op here.

    # 6) Explicit delay override wins (used for cooldown-until timing).
    print("\n[6] explicit delay_hours overrides the backoff table")
    db, st = FakeDB(), FakeState()
    sequence_engine.defer_lead(db, st, reason="cooldown", delay_hours=0.5, count_attempt=False)
    gap = (st.next_action_at - datetime.utcnow()).total_seconds() / 3600.0
    check("~0.5h out", 0.4 < gap < 1.0, f"got {gap:.2f}h")

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print("All deferral tests passed.")
    print(f"({len(_EVENTS)} events emitted — step_deferred is the audit trail)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
