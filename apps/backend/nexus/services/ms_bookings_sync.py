"""MS Bookings polling sync.

Local dev (and small prod deployments) can't easily receive Microsoft Graph
change-notification webhooks — they require a public HTTPS endpoint that
Microsoft can hit. This polling sync is the always-available alternative: every
N minutes the local sequencer loop calls `sync_ms_bookings(db)` which lists
appointments via the Graph API and upserts any new ones into
`nexus_demo_bookings`. The Bookings tab + calendar view then surface them
automatically via the existing `GET /nexus/bookings` route.

Targets are resolved per-workspace:
  - If the workspace has its own `ms_booking_business_id` in `nexus_settings`,
    use that.
  - Otherwise fall back to `AZURE_BOOKING_BUSINESS_ID_GTM` / `MS_BOOKING_BUSINESS_ID`
    env vars (operator-wide default — local dev / single-tenant).

Idempotent: dedup on `(workspace_id, booking_id_external)`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from nexus.models_phase6 import DemoBooking, DemoBriefing
from nexus.services import ms_graph_client


# ─── Lead-status reflection (booking captured → propagate to all tables) ──────


def record_calendar_response(
    db: Session, *, workspace_id: Optional[int], attendee_email: str, response: str
) -> Optional[int]:
    """Record a calendar RSVP on the prospect's latest booking + reflect it in the
    journey. `response` is 'accepted' | 'declined' | 'tentative' | 'cancelled'.

      - accepted            → booking status 'confirmed'
      - declined / cancelled → booking status 'cancelled' + REOPEN the lead's
                               follow-ups (they backed out → outreach resumes)
      - tentative           → keep 'scheduled', just note the response

    Idempotent (won't re-apply the same response). Best-effort — never raises.
    Returns the booking id it updated, or None. The RSVP itself is NOT ingested
    as a reply (the caller still drops the message) — this only records the
    OUTCOME so the acceptance/decline shows on the journey as a clean signal.
    """
    if not attendee_email or response not in ("accepted", "declined", "tentative", "cancelled"):
        return None
    try:
        q = db.query(DemoBooking).filter(
            DemoBooking.attendee_email.ilike(attendee_email.strip()),
            DemoBooking.status.in_(["scheduled", "confirmed", "pending_rep_confirm"]),
        )
        if workspace_id:
            q = q.filter(DemoBooking.workspace_id == workspace_id)
        booking = q.order_by(DemoBooking.id.desc()).first()
        if booking is None:
            return None
        meta = dict(booking.meta or {})
        if meta.get("rsvp") == response:
            return booking.id  # already recorded — idempotent
        meta["rsvp"] = response
        meta["rsvp_at"] = datetime.utcnow().isoformat() + "Z"
        booking.meta = meta
        if response == "accepted":
            booking.status = "confirmed"
        elif response in ("declined", "cancelled"):
            booking.status = "cancelled"
        db.commit()
        if response in ("declined", "cancelled"):
            _reopen_after_decline(db, lead_id=booking.lead_id, workspace_id=workspace_id)
        logger.info(
            "calendar RSVP '%s' recorded for booking=%s (%s)", response, booking.id, attendee_email
        )
        return booking.id
    except Exception:  # noqa: BLE001
        logger.exception("record_calendar_response failed for %s", attendee_email)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _reopen_after_decline(
    db: Session, *, lead_id: Optional[int], workspace_id: Optional[int]
) -> None:
    """A prospect who booked then DECLINED is back in play — un-halt the sequences
    that were halted for the (now-cancelled) demo and drop the lead badge from
    demo_scheduled, so the cadence resumes. Best-effort."""
    if not lead_id:
        return
    try:
        nxt = datetime.utcnow() + timedelta(days=1)
        db.execute(
            text(
                """UPDATE nexus_lead_sequences
                      SET status='active', halt_reason=NULL, next_action_at=:nxt, updated_at=NOW()
                    WHERE lead_id=:lid AND status='halted' AND halt_reason='demo_scheduled'"""
            ),
            {"lid": int(lead_id), "nxt": nxt},
        )
        db.execute(
            text(
                """UPDATE nexus_global_leads SET status='replied', updated_at=NOW()
                    WHERE id=:lid AND status='demo_scheduled'"""
            ),
            {"lid": int(lead_id)},
        )
        if workspace_id:
            db.execute(
                text(
                    """UPDATE nexus_leads SET status='replied', updated_at=NOW()
                        WHERE global_lead_id=:lid AND workspace_id=:ws AND status='demo_scheduled'"""
                ),
                {"lid": int(lead_id), "ws": int(workspace_id)},
            )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("_reopen_after_decline failed for lead=%s", lead_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def mark_lead_demo_scheduled(
    db: Session,
    *,
    lead_id: Optional[int],
    workspace_id: Optional[int],
    campaign_id: Optional[int],
) -> dict:
    """Reflect a captured demo booking across every table the GTM Journey,
    sequencer, and analytics views read from. Called once per new DemoBooking
    that successfully matched a global lead.

    Updates (each idempotent):
      - nexus_global_leads.status     → 'demo_scheduled' (terminal; only if
                                        not already demo_scheduled / unsub /
                                        bounced — so we never regress).
      - nexus_lead_sequences          → status='halted', halt_reason=
                                        'demo_scheduled' for any still-active
                                        sequences for this lead (so the
                                        sequencer stops scheduling further
                                        outbound for someone who booked).
      - nexus_outreach.status         → 'demo_scheduled' for the lead's
                                        outreach row(s). This is the column
                                        performance.py reads for the demo-
                                        conversion metric.

    Returns a small dict for logging. Failures are swallowed per-table so a
    schema mismatch on one doesn't block the others.
    """
    summary = {"global_lead": 0, "lead_sequences": 0, "outreach": 0}
    if not lead_id:
        return summary

    # 1. Promote the GTM Journey badge to demo_scheduled (top of the ladder).
    try:
        result = db.execute(
            text(
                """UPDATE nexus_global_leads
                      SET status = 'demo_scheduled', updated_at = NOW()
                    WHERE id = :lid
                      AND COALESCE(status, '') NOT IN
                          ('demo_scheduled', 'unsubscribed', 'bounced')
                """
            ),
            {"lid": int(lead_id)},
        )
        summary["global_lead"] = result.rowcount or 0
        # Per-workspace mirror (authoritative for display) so a booking in this
        # workspace doesn't flip a shared lead to demo_scheduled for another.
        if workspace_id:
            db.execute(
                text(
                    """UPDATE nexus_leads
                          SET status = 'demo_scheduled', updated_at = NOW()
                        WHERE global_lead_id = :lid
                          AND workspace_id = :ws
                          AND COALESCE(status, '') NOT IN
                              ('demo_scheduled', 'unsubscribed', 'bounced')
                    """
                ),
                {"lid": int(lead_id), "ws": int(workspace_id)},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("global_lead demo_scheduled update failed: %s", e)
        db.rollback()

    # 2. Halt any still-active sequences for this lead so we don't keep
    #    emailing someone who already booked. halt_reason matches the value
    #    used by the inbound DEMO_SCHEDULED intent path for consistency.
    try:
        result = db.execute(
            text(
                """UPDATE nexus_lead_sequences
                      SET status = 'halted',
                          halt_reason = 'demo_scheduled',
                          updated_at = NOW(),
                          next_action_at = NULL
                    WHERE lead_id = :lid
                      AND (:ws IS NULL OR workspace_id = :ws)
                      AND status IN ('active', 'paused', 'replied')
                """
            ),
            {"lid": int(lead_id), "ws": int(workspace_id) if workspace_id else None},
        )
        summary["lead_sequences"] = result.rowcount or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("lead_sequences halt(demo) failed: %s", e)
        db.rollback()

    # 3. Flip outreach.status so performance.py's demo-conversion count
    #    picks it up. Filter by campaign when we have one (most precise);
    #    otherwise touch all of the lead's outreach rows in the workspace.
    try:
        if campaign_id:
            result = db.execute(
                text(
                    """UPDATE nexus_outreach
                          SET status = 'demo_scheduled', updated_at = NOW()
                        WHERE lead_id = :lid
                          AND campaign_id = :cid
                          AND status NOT IN
                              ('demo_scheduled', 'unsubscribed', 'bounced')
                    """
                ),
                {"lid": int(lead_id), "cid": int(campaign_id)},
            )
        elif workspace_id:
            result = db.execute(
                text(
                    """UPDATE nexus_outreach
                          SET status = 'demo_scheduled', updated_at = NOW()
                        WHERE lead_id = :lid
                          AND workspace_id = :ws
                          AND status NOT IN
                              ('demo_scheduled', 'unsubscribed', 'bounced')
                    """
                ),
                {"lid": int(lead_id), "ws": int(workspace_id)},
            )
        else:
            result = None
        if result is not None:
            summary["outreach"] = result.rowcount or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("outreach demo_scheduled update failed: %s", e)
        db.rollback()

    return summary

logger = logging.getLogger("pipelyt.nexus.ms_bookings_sync")


def _parse_iso(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _resolve_lead_and_workspace(db: Session, email: str):
    """Resolve an email to (global_lead_id, workspace_id, campaign_id).

    Workspace association lives in `nexus_leads` (one row per workspace
    attachment), not on `nexus_global_leads` itself. Pick the most recent
    attachment by `nexus_leads.id` so an active lead's latest campaign
    wins. Multi-workspace edge case: if a lead is in two workspaces, the
    most-recent attachment wins — same behaviour the sequencer/journey
    use.
    """
    if not email:
        return None, None, None
    try:
        row = db.execute(
            text(
                """SELECT gl.id, nl.workspace_id, nl.campaign_id
                     FROM nexus_global_leads gl
                     LEFT JOIN nexus_leads nl ON nl.global_lead_id = gl.id
                    WHERE LOWER(gl.email) = :em
                    ORDER BY nl.id DESC NULLS LAST
                    LIMIT 1
                """
            ),
            {"em": email.lower()},
        ).first()
    except Exception as e:  # noqa: BLE001
        logger.warning("_resolve_lead_and_workspace query failed: %s", e)
        db.rollback()
        return None, None, None
    if not row:
        return None, None, None
    return (
        int(row[0]),
        int(row[1]) if row[1] is not None else None,
        int(row[2]) if row[2] is not None else None,
    )


def _enumerate_targets(db: Session) -> list[tuple[int, str]]:
    """Return [(workspace_id, business_id), ...]. Per-workspace setting wins;
    env default is applied to a single designated workspace.

    Multi-tenant policy: every workspace owns its own MS Bookings business
    (configured via POST /nexus/ms-bookings/setup which writes to
    nexus_settings). The operator's own env-level business
    (AZURE_BOOKING_BUSINESS_ID_GTM) is NOT broadcast to every workspace —
    that would copy the same appointments into 5 different tenants. It
    applies only to MS_BOOKING_DEFAULT_WORKSPACE_ID (defaults to the
    lowest-id workspace owned by SEED_USER_EMAIL if unset, else first
    workspace overall).
    """
    import os
    env_default = ms_graph_client.resolve_business_id()
    configured: dict[int, str] = {}

    try:
        rows = db.execute(
            text(
                "SELECT workspace_id, COALESCE(settings->>'ms_booking_business_id', '') "
                "FROM nexus_settings"
            )
        ).fetchall()
    except Exception as e:  # table missing on a fresh deploy — soft fail
        logger.debug("nexus_settings query failed: %s", e)
        db.rollback()
        rows = []

    for ws_id, biz in rows:
        biz = (biz or "").strip()
        if biz:
            configured[int(ws_id)] = biz

    targets: list[tuple[int, str]] = list(configured.items())

    if env_default:
        # Resolve which single workspace should consume the env-default.
        default_ws = (os.getenv("MS_BOOKING_DEFAULT_WORKSPACE_ID") or "").strip()
        ws_id = None
        if default_ws.isdigit():
            ws_id = int(default_ws)
        else:
            seed_email = (os.getenv("SEED_USER_EMAIL") or "").strip().lower()
            if seed_email:
                try:
                    row = db.execute(
                        text(
                            "SELECT w.id FROM nexus_workspaces w "
                            "JOIN users u ON u.id = w.owner_id "
                            "WHERE LOWER(u.email) = :em "
                            "ORDER BY w.id LIMIT 1"
                        ),
                        {"em": seed_email},
                    ).first()
                    if row:
                        ws_id = int(row[0])
                except Exception:
                    db.rollback()
            if ws_id is None:
                try:
                    row = db.execute(
                        text("SELECT id FROM nexus_workspaces ORDER BY id LIMIT 1")
                    ).first()
                    if row:
                        ws_id = int(row[0])
                except Exception:
                    db.rollback()

        if ws_id is not None and ws_id not in configured:
            targets.append((ws_id, env_default))

    return targets


async def sync_ms_bookings(db: Session) -> dict:
    """List appointments for each configured (workspace, business_id) target
    and upsert any new ones as `nexus_demo_bookings` rows. Returns a summary
    dict for logging.
    """
    targets = _enumerate_targets(db)
    summary = {
        "targets": len(targets),
        "appointments_seen": 0,
        "created": 0,
        "errors": 0,
    }
    if not targets:
        return summary

    for workspace_id, business_id in targets:
        try:
            appts = await ms_graph_client.list_appointments(business_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MS Bookings list_appointments failed for ws=%s biz=%s: %s",
                workspace_id,
                business_id,
                e,
            )
            summary["errors"] += 1
            continue

        for appt in appts or []:
            summary["appointments_seen"] += 1
            appt_id = appt.get("id")
            if not appt_id:
                continue

            attendee_email = ""
            attendee_name = ""
            for c in appt.get("customers") or []:
                attendee_email = (c.get("emailAddress") or "").lower().strip()
                attendee_name = c.get("name") or ""
                break

            # ISOLATION: resolve the lead → the workspace that actually OWNS it
            # (via its campaign in nexus_leads). The booking is stamped under
            # THAT workspace, not the sync target (which for a shared booking
            # business is an env-default like ws 1). Previously the target
            # stamping + per-(workspace,external_id) dedup fanned the same
            # appointment out as duplicate rows under the wrong workspace.
            lead_id, resolved_ws, campaign_id = _resolve_lead_and_workspace(db, attendee_email)
            effective_ws = resolved_ws or workspace_id

            # Dedup GLOBALLY by the MS appointment id so one appointment = one
            # row (under the owning workspace), never one-per-target.
            try:
                existing = db.execute(
                    text(
                        "SELECT id, lead_id, campaign_id "
                        "FROM nexus_demo_bookings "
                        "WHERE booking_id_external = :a "
                        "ORDER BY id LIMIT 1"
                    ),
                    {"a": appt_id},
                ).first()
            except Exception:
                db.rollback()
                existing = None

            scheduled_at = _parse_iso((appt.get("startDateTime") or {}).get("dateTime"))
            end_time_v = _parse_iso((appt.get("endDateTime") or {}).get("dateTime"))
            status = "scheduled"
            cancelled = appt.get("isCancelled") or appt.get("cancellationReason")
            if cancelled:
                status = "cancelled"

            # If the booking already exists, UPDATE rather than skip so a
            # rescheduled/cancelled appointment in Outlook is reflected on
            # the calendar UI. This is what stops "I don't see the latest"
            # bugs caused by Graph mutating the appointment after we first
            # captured it.
            if existing:
                existing_id = int(existing[0])
                existing_lead_id = existing[1]
                existing_campaign_id = existing[2]
                # Self-heal: if the row has NULL lead_id/campaign_id (which
                # happened for every booking captured while
                # _resolve_lead_and_workspace was broken), try to resolve
                # them now via the corrected lookup. Lets briefings reflect
                # the actual product the lead was contacted about without
                # needing manual DB surgery.
                # lead/campaign already resolved above; fill in any that the
                # existing row is missing.
                healed_lead_id = existing_lead_id if existing_lead_id is not None else lead_id
                healed_campaign_id = existing_campaign_id if existing_campaign_id is not None else campaign_id
                try:
                    db.execute(
                        text(
                            """UPDATE nexus_demo_bookings
                                  SET scheduled_at = :s,
                                      end_time = :e,
                                      status = CASE
                                        WHEN status IN ('confirmed','completed','no_show')
                                          THEN status
                                        ELSE :st
                                      END,
                                      attendee_email = COALESCE(NULLIF(:em, ''), attendee_email),
                                      attendee_name  = COALESCE(NULLIF(:nm, ''), attendee_name),
                                      lead_id = COALESCE(:lid, lead_id),
                                      campaign_id = COALESCE(:cid, campaign_id),
                                      -- re-home the row onto the workspace that owns the lead
                                      workspace_id = COALESCE(:eff_ws, workspace_id),
                                      updated_at = NOW()
                                WHERE id = :id
                            """
                        ),
                        {
                            "s": scheduled_at,
                            "e": end_time_v,
                            "st": status,
                            "em": attendee_email,
                            "nm": attendee_name,
                            "lid": healed_lead_id,
                            "cid": healed_campaign_id,
                            "eff_ws": effective_ws,
                            "id": existing_id,
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("DemoBooking upsert update failed (id=%s): %s", existing_id, e)
                    db.rollback()
                # Reflect demo_scheduled across the lead-status surface
                # whenever the booking has a matched lead — covers BOTH
                # the heal-from-NULL path AND legacy bookings that were
                # captured with a valid lead_id but predate the status
                # reflection wiring. The helper's UPDATEs are idempotent
                # (filter out already-terminal statuses), so firing on
                # every tick can't regress good state.
                if healed_lead_id:
                    try:
                        mark_lead_demo_scheduled(
                            db,
                            lead_id=healed_lead_id,
                            workspace_id=effective_ws,
                            campaign_id=healed_campaign_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.debug("lead-status reflect on upsert skipped: %s", e)
                        db.rollback()
                continue

            # (lead_id / resolved_ws / campaign_id already resolved above)

            # If the MS Bookings form didn't capture a name (rare but happens
            # for "email-only" Bookings configs), fall back to the matched
            # lead's first/last name from nexus_global_leads. This is what
            # makes the calendar pill read "Alice Chen" instead of just an
            # email address.
            if lead_id and not attendee_name.strip():
                try:
                    lead_row = db.execute(
                        text(
                            "SELECT first_name, last_name FROM nexus_global_leads "
                            "WHERE id = :id"
                        ),
                        {"id": lead_id},
                    ).first()
                    if lead_row:
                        fn = (lead_row[0] or "").strip()
                        ln = (lead_row[1] or "").strip()
                        composed = (fn + " " + ln).strip()
                        if composed:
                            attendee_name = composed
                except Exception:
                    db.rollback()

            status = "scheduled"
            cancelled = appt.get("isCancelled") or appt.get("cancellationReason")
            if cancelled:
                status = "cancelled"

            booking = DemoBooking(
                workspace_id=effective_ws,
                lead_id=lead_id,
                campaign_id=campaign_id,
                source="ms_bookings",
                booking_id_external=appt_id,
                ms_booking_business_id=business_id,
                attendee_email=attendee_email,
                attendee_name=attendee_name,
                scheduled_at=_parse_iso((appt.get("startDateTime") or {}).get("dateTime")),
                end_time=_parse_iso((appt.get("endDateTime") or {}).get("dateTime")),
                status=status,
                meta={"synced_via": "polling", "appt_keys": list(appt.keys())[:20]},
            )
            db.add(booking)
            try:
                db.flush()
                summary["created"] += 1
                # Booking landed — now reflect it across the lead-status
                # surface (GTM Journey badge, sequencer halt, outreach
                # conversion). Skipped when no lead matched.
                if lead_id:
                    status_result = mark_lead_demo_scheduled(
                        db,
                        lead_id=lead_id,
                        workspace_id=effective_ws,
                        campaign_id=campaign_id,
                    )
                    if any(status_result.values()):
                        logger.info(
                            "lead %s marked demo_scheduled (counts=%s)",
                            lead_id,
                            status_result,
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("DemoBooking insert failed (ws=%s appt=%s): %s", workspace_id, appt_id, e)
                db.rollback()
                summary["errors"] += 1

    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("ms_bookings sync commit failed: %s", e)
        db.rollback()
        summary["errors"] += 1

    return summary


# ─── Pre-call briefing auto-generation ────────────────────────────────────────


def resolve_booking_attribution(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int],
    lead_id: Optional[int],
    attendee_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a booking's product + company attribution.

    Returns a dict with `product_name`, `product_value_proposition`,
    `company_hint`, plus the `product_id` / `lead_id` the resolution landed on.
    Those two ids are what the briefing needs to retrieve real KB chunks and the
    lead's company background — `_resolve_product_and_company` (the 3-tuple
    wrapper below) drops them, which is why the briefing had nothing to ground on.

    Priority for product (each step proves the previous was unavailable):
      1. The product on the booking's campaign (most accurate — explicit attribution).
      2. The lead_id's most-recent nexus_leads attachment → that row's
         campaign → that campaign's product.
      3. EMAIL-based recovery: even when lead_id is NULL on the booking
         (e.g. the original capture ran before email resolution worked),
         look up the global lead by email, then its most-recent
         nexus_leads attachment → campaign → product. This is the path
         that catches the "Jardee booked but landed with NULL lead_id"
         class of bugs.
      4. The workspace's first product (last-resort fallback, only used
         when the email matches no lead at all — e.g. a stranger booking
         via the public link).

    Company hint precedence: matched lead's `company_name` first, else
    `company` (legacy column), else the email's domain stripped of TLD
    so Gemini still has something to ground on.
    """
    product_name = ""
    product_value_proposition = ""
    product_id: Optional[int] = None
    resolved_lead_id = lead_id  # may get filled in via email lookup

    # 1. Booking's campaign → product
    if campaign_id:
        try:
            r = db.execute(
                text(
                    """SELECT p.id, p.name, p.value_proposition
                         FROM nexus_campaigns c
                         JOIN nexus_products p ON p.id = c.product_id
                        WHERE c.id = :cid
                        LIMIT 1"""
                ),
                {"cid": int(campaign_id)},
            ).first()
            if r:
                product_id = int(r[0]) if r[0] is not None else None
                product_name = (r[1] or "").strip()
                product_value_proposition = (r[2] or "").strip()
        except Exception:
            db.rollback()

    # 2. lead_id → most-recent nexus_leads → campaign → product
    if not product_name and resolved_lead_id:
        try:
            r = db.execute(
                text(
                    """SELECT p.id, p.name, p.value_proposition
                         FROM nexus_leads nl
                         JOIN nexus_campaigns c ON c.id = nl.campaign_id
                         JOIN nexus_products p ON p.id = c.product_id
                        WHERE nl.global_lead_id = :gid
                          AND nl.workspace_id = :w
                        ORDER BY nl.id DESC
                        LIMIT 1"""
                ),
                {"gid": int(resolved_lead_id), "w": int(workspace_id)},
            ).first()
            if r:
                product_id = int(r[0]) if r[0] is not None else None
                product_name = (r[1] or "").strip()
                product_value_proposition = (r[2] or "").strip()
        except Exception:
            db.rollback()

    # 3. Email-based recovery — catches legacy bookings with NULL lead_id
    if not product_name and attendee_email:
        try:
            r = db.execute(
                text(
                    """SELECT gl.id, p.id, p.name, p.value_proposition
                         FROM nexus_global_leads gl
                         JOIN nexus_leads nl ON nl.global_lead_id = gl.id
                         JOIN nexus_campaigns c ON c.id = nl.campaign_id
                         JOIN nexus_products p ON p.id = c.product_id
                        WHERE LOWER(gl.email) = :em
                          AND nl.workspace_id = :w
                        ORDER BY nl.id DESC
                        LIMIT 1"""
                ),
                {"em": attendee_email.lower(), "w": int(workspace_id)},
            ).first()
            if r:
                resolved_lead_id = int(r[0])  # so the company lookup below benefits too
                product_id = int(r[1]) if r[1] is not None else None
                product_name = (r[2] or "").strip()
                product_value_proposition = (r[3] or "").strip()
        except Exception:
            db.rollback()

    # 4. No silent workspace-first-product fallback. If steps 1-3 all
    #    failed (booking had no campaign_id, no lead_id, and the attendee
    #    email matches no lead), we'd rather show "our product" in the
    #    briefing than silently misattribute to the workspace's first
    #    product alphabetically. The original fallback caused a typo'd
    #    booking email (cmclaim vs cmclain) to be confidently labeled
    #    with the wrong product. Log a warning so this is visible in
    #    CloudWatch — typically means the attendee email needs to be
    #    corrected on the booking, or a new lead row needs to be created.
    if not product_name:
        logger.warning(
            "demo briefing: no product attribution for booking "
            "(workspace_id=%s, campaign_id=%s, lead_id=%s, attendee_email=%s) — "
            "briefing will render with generic 'our product'. Likely a typo'd "
            "attendee email or a stranger booking with no matching lead.",
            workspace_id,
            campaign_id,
            lead_id,
            attendee_email,
        )

    # Company hint
    company_hint = ""
    if resolved_lead_id:
        try:
            r = db.execute(
                text(
                    "SELECT COALESCE(NULLIF(company_name,''), NULLIF(company,'')) "
                    "FROM nexus_global_leads WHERE id = :id"
                ),
                {"id": int(resolved_lead_id)},
            ).first()
            if r and r[0]:
                company_hint = r[0].strip()
        except Exception:
            db.rollback()
    if not company_hint and attendee_email:
        try:
            r = db.execute(
                text(
                    "SELECT COALESCE(NULLIF(company_name,''), NULLIF(company,'')) "
                    "FROM nexus_global_leads WHERE LOWER(email) = :em LIMIT 1"
                ),
                {"em": attendee_email.lower()},
            ).first()
            if r and r[0]:
                company_hint = r[0].strip()
        except Exception:
            db.rollback()
    if not company_hint and attendee_email and "@" in attendee_email:
        # Last-ditch: pull domain off the email so Gemini at least knows
        # which company. "jsandall@lewiscabinet.com" -> "lewiscabinet".
        domain = attendee_email.split("@", 1)[1].strip()
        if domain:
            host = domain.split(".")[0]
            if host:
                company_hint = host

    return {
        "product_id": product_id,
        "product_name": product_name,
        "product_value_proposition": product_value_proposition,
        "company_hint": company_hint,
        "lead_id": resolved_lead_id,
    }


def _resolve_product_and_company(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int],
    lead_id: Optional[int],
    attendee_email: Optional[str] = None,
) -> tuple[str, str, str]:
    """Back-compat 3-tuple wrapper over `resolve_booking_attribution` for callers
    that only need the display strings."""
    a = resolve_booking_attribution(
        db,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        attendee_email=attendee_email,
    )
    return a["product_name"], a["product_value_proposition"], a["company_hint"]


def process_demo_briefings(db: Session, max_rows: int = 3) -> dict:
    """For any DemoBooking that doesn't yet have a DemoBriefing, generate one.

    Mirrors `process_linkedin_drafts` in `sequencer.py` — separate per-tick
    pass with a small cap so the synchronous Gemini call (~5-10s each) can
    never blow the Lambda budget. Newest bookings are briefed first.

    The actual prompt + Gemini call lives in `routers/bookings.py` (where the
    on-demand `POST /nexus/bookings/{id}/briefing` endpoint also calls it) —
    imported lazily so importing this module doesn't pull in google-genai.
    """
    try:
        rows = db.execute(
            text(
                """SELECT b.id, b.workspace_id, b.lead_id, b.campaign_id,
                          b.attendee_email, b.attendee_name
                   FROM nexus_demo_bookings b
                   LEFT JOIN nexus_demo_briefings br
                     ON br.demo_booking_id = b.id
                   WHERE br.id IS NULL
                     AND COALESCE(b.attendee_email, '') <> ''
                     AND b.status IN ('scheduled','confirmed','pending_rep_confirm')
                   ORDER BY b.id DESC
                   LIMIT :n
                """
            ),
            {"n": max_rows},
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("briefing candidate query failed: %s", e)
        db.rollback()
        return {"generated": 0, "errors": 1}

    if not rows:
        return {"generated": 0}

    # Lazy import — keeps the import graph clean and avoids loading google-genai
    # in environments that never trigger a briefing.
    from nexus.routers.bookings import (
        _briefing_provenance,
        _generate_briefing_markdown,
        build_briefing_grounding,
    )

    generated = 0
    errors = 0
    for booking_id, workspace_id, lead_id, campaign_id, attendee_email, attendee_name in rows:
        attribution = resolve_booking_attribution(
            db,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            attendee_email=attendee_email,
        )
        product_name = attribution["product_name"]
        product_value_prop = attribution["product_value_proposition"]
        company_hint = attribution["company_hint"]

        # Ground the briefing in the product KB + the lead's real company
        # background before generating — without this the model has only names
        # to work from and fills the gaps with plausible invention.
        grounding = build_briefing_grounding(
            db,
            workspace_id=workspace_id,
            product_id=attribution["product_id"],
            lead_id=lead_id or attribution["lead_id"],
            product_name=product_name,
            product_value_prop=product_value_prop,
            company_hint=company_hint,
        )

        briefing_md = _generate_briefing_markdown(
            attendee_name=attendee_name or "",
            attendee_email=attendee_email or "",
            product_name=product_name or "our product",
            product_value_prop=product_value_prop,
            company_hint=company_hint,
            grounding=grounding,
        )

        try:
            # Re-check inside the write — guards against a second tick
            # racing in the same window.
            existing = db.execute(
                text(
                    "SELECT 1 FROM nexus_demo_briefings "
                    "WHERE demo_booking_id = :b LIMIT 1"
                ),
                {"b": int(booking_id)},
            ).first()
            if existing:
                continue
            briefing = DemoBriefing(
                workspace_id=int(workspace_id),
                demo_booking_id=int(booking_id),
                lead_id=int(lead_id) if lead_id else None,
                briefing_md=briefing_md,
                status="ready",
                generated_at=datetime.utcnow(),
                raw_research=_briefing_provenance(grounding),
            )
            db.add(briefing)
            db.flush()
            generated += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("briefing insert failed (booking=%s): %s", booking_id, e)
            db.rollback()
            errors += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        errors += 1

    return {"generated": generated, "errors": errors}
