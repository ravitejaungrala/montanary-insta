"""Prospect-facing meeting-agenda agent (Phase 1 — meeting_agenda_agent_plan.md).

For a booked demo, generate a short, shareable agenda grounded in the lead, their
company, and the product (with RAG grounding over the product KB). Stored 1:1 per
booking in `nexus_meeting_agendas`. Distinct from the internal rep briefing.

Phase 1 STORES ONLY — the invite is deliberately NOT modified here. The generated
agendas surface in the rep UI for validation before Phase 2 wires them into the
prospect's invite.

The three sections map to the requirement:
  ## Overview                    — the company/product
  ## How It Addresses Your Needs — how the product meets the prospect's needs
  ## Value & Business Benefits   — the value proposition + business benefits

Design guarantees:
  - deterministic ASSEMBLER decides what the model sees (only real fields),
  - one Gemini call for prose, with a full deterministic FALLBACK so the agenda
    is never empty (no API key / failure still yields a valid 3-section doc),
  - hard NO-FABRICATION: the prospect sees this, so the model is told to use only
    the supplied context and to keep a section short rather than invent facts.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.services.meeting_agenda_agent")

# Fixed H2 headings — map 1:1 to the three required sections. Kept literal (not
# interpolated with product/company names) so the output contract is stable and
# testable; the actual names appear in the body.
SECTIONS = ("Overview", "How It Addresses Your Needs", "Value & Business Benefits")

_MAX_VOICED = 5          # most recent prospect replies to consider as "needs"
_RAG_TOP_K = 5

# A reply is not a "need" when it's just booking logistics or contact info.
_LOGISTICS_RE = re.compile(
    r"(book the demo|send the invite|works (on my end|for me)|let'?s do it|"
    r"reachable on either line|reach me on|talk soon|see you (at|on)|calendar invite)",
    re.I,
)
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")


def _need_snippet(body: Optional[str]) -> Optional[str]:
    """Return a clean 'voiced need' snippet, or None if the reply is really just
    contact info or booking logistics (not a product need)."""
    b = (body or "").strip().replace("\n", " ")
    if not b:
        return None
    if not re.sub(r"[^A-Za-z]", "", _PHONE_RE.sub(" ", b)):  # nothing but a number
        return None
    if _LOGISTICS_RE.search(b) and "?" not in b and len(b) < 160:
        return None
    return b[:400]


# ─── Context assembler (deterministic — no LLM) ───────────────────────────────

def build_agenda_context(db: Session, *, booking_id: int) -> Optional[Dict[str, Any]]:
    """Gather every real input for the agenda. Returns None if the booking is
    gone. Missing sub-fields degrade to None/[] — never fabricated."""
    b = db.execute(
        text(
            "SELECT workspace_id, lead_id, campaign_id, product_id, scheduled_at, "
            "duration_min, attendee_name, attendee_email "
            "FROM nexus_demo_bookings WHERE id = :b"
        ),
        {"b": booking_id},
    ).first()
    if not b:
        return None
    m = b._mapping
    product_id = m["product_id"]
    lead_id = m["lead_id"]

    product = {}
    if product_id:
        pr = db.execute(
            text(
                "SELECT name, category, value_proposition, key_benefits, icp, pricing_tier "
                "FROM nexus_products WHERE id = :p"
            ),
            {"p": product_id},
        ).first()
        if pr:
            pm = pr._mapping
            icp = pm["icp"] if isinstance(pm["icp"], dict) else {}
            product = {
                "name": pm["name"],
                "category": pm["category"],
                "value_proposition": pm["value_proposition"],
                "key_benefits": [x for x in (pm["key_benefits"] or []) if isinstance(x, str)],
                "pain_points": [x for x in (icp.get("pain_points") or []) if isinstance(x, str)],
                "pricing_tier": pm["pricing_tier"],
            }

    company = {}
    attendee = {"name": m["attendee_name"], "email": m["attendee_email"], "role": None}
    if lead_id:
        ld = db.execute(
            text(
                "SELECT first_name, last_name, role, company_name, organization_industry, "
                "person_country, linkedin_headline, email "
                "FROM nexus_global_leads WHERE id = :l"
            ),
            {"l": lead_id},
        ).first()
        if ld:
            lm = ld._mapping
            company = {"name": lm["company_name"], "industry": lm["organization_industry"]}
            attendee = {
                "name": (attendee["name"]
                         or " ".join(x for x in (lm["first_name"], lm["last_name"]) if x).strip()
                         or None),
                "email": attendee["email"] or lm["email"],
                "role": lm["role"],
                "headline": lm["linkedin_headline"],
            }

    # The prospect's own words — the most honest source of "their needs".
    voiced: List[str] = []
    if lead_id:
        rows = db.execute(
            text(
                "SELECT im.body_text FROM nexus_inbound_messages im "
                "JOIN nexus_inbound_threads it ON im.thread_id = it.id "
                "JOIN nexus_lead_sequences ls ON ls.id = it.lead_sequence_id "
                "WHERE ls.lead_id = :l "
                # Only replies that carry a real product NEED — a question or
                # stated interest. Booking-logistics (DEMO_SCHEDULED), OOO,
                # unsubscribe and bare contact-info replies are not 'needs'.
                "AND COALESCE(im.intent,'') IN ('QUESTION','INTERESTED','NOT_NOW') "
                "AND COALESCE(TRIM(im.body_text),'') <> '' "
                "ORDER BY im.received_at DESC LIMIT :k"
            ),
            {"l": lead_id, "k": _MAX_VOICED},
        ).fetchall()
        for r in rows:
            snippet = _need_snippet(r[0])
            if snippet:
                voiced.append(snippet)

    # The lead's OWN company — what they do, size, HQ, focus areas, stack, news.
    # Without this the agenda knows only a company name and an industry label,
    # so "How It Addresses Your Needs" has nothing account-specific to attach to.
    # `ensure_fresh` is safe here: agenda generation always runs in a background
    # path (inbound poller / booking handler), never a user-blocking request.
    try:
        from nexus.services.company_context import load_company_context

        company = load_company_context(
            db,
            lead_id=lead_id,
            company_name=(company or {}).get("name"),
            ensure_fresh=True,
        ) or company
    except Exception as exc:  # noqa: BLE001
        log.debug("meeting_agenda: company context unavailable: %s", exc)

    return {
        "booking_id": booking_id,
        "workspace_id": m["workspace_id"],
        "lead_id": lead_id,
        "product_id": product_id,
        "meeting": {
            "start": m["scheduled_at"],
            "duration_min": m["duration_min"],
            "when_label": _when_label(m["scheduled_at"], m["duration_min"]),
        },
        "attendee": attendee,
        "company": company,
        "product": product,
        "voiced_needs": voiced,
        "kb_snippets": [],   # filled by ground_product_facts
    }


def _when_label(dt: Any, duration_min: Any = None) -> Optional[str]:
    """"Tue 28 Jul, 4:00–4:30 PM" — a START AND END time.

    A lone start time makes the reader work out how long they are committing
    to; a range answers it in the same glance. The meridiem is printed once
    when both ends share it ("4:00–4:30 PM") and twice when they don't
    ("11:30 AM–12:15 PM"). Falls back to the start alone if the duration is
    missing or unusable — never guesses a length.
    """
    if not isinstance(dt, datetime):
        return None

    def _clock(d: datetime, with_ampm: bool = True) -> str:
        s = d.strftime("%I:%M %p") if with_ampm else d.strftime("%I:%M")
        return s.lstrip("0")

    try:
        day = dt.strftime("%a %d %b").replace(" 0", " ")
        try:
            mins = int(duration_min or 0)
        except (TypeError, ValueError):
            mins = 0
        if mins <= 0:
            return f"{day}, {_clock(dt)}"
        end = dt + timedelta(minutes=mins)
        same_meridiem = dt.strftime("%p") == end.strftime("%p")
        start_txt = _clock(dt, with_ampm=not same_meridiem)
        return f"{day}, {start_txt}–{_clock(end)}"
    except Exception:  # noqa: BLE001
        return None


# ─── RAG grounding over the product KB ────────────────────────────────────────

def _clean_chunk(raw: str) -> str:
    """Strip markdown image/link noise + bare URLs from a KB chunk so grounding
    carries real prose, not '![Image 24: HubSpot](…)'. Drops trivial chunks."""
    t = raw or ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)   # ![alt](url) images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # [text](url) → text
    t = re.sub(r"https?://\S+", " ", t)            # bare URLs
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 40 else ""


def ground_product_facts(db: Session, ctx: Dict[str, Any]) -> None:
    """Populate ctx['kb_snippets'] with DIVERSE, deduped product-KB chunks.

    A product site is often ingested as the homepage repeated many times, so a
    single generic query returns near-identical taglines. Instead we fire a few
    angled queries (capabilities / integrations / benefits / the prospect's own
    need / their industry) and merge, deduping near-identical chunks — this
    surfaces the substantive feature content that a generic query buries. Reuses
    rag_reply's retrieval (Pinecone + graceful empty). Never raises."""
    product_id, workspace_id = ctx.get("product_id"), ctx.get("workspace_id")
    if not product_id or not workspace_id:
        return
    p = ctx.get("product") or {}
    c = ctx.get("company") or {}
    name = p.get("name") or ""
    vp = p.get("value_proposition") or ""
    queries = [
        f"{vp} {name} what it does core features and capabilities how it works",
        f"{name} integrations connectors supported platforms",
        f"{name} business benefits outcomes results use cases",
    ]
    need = (ctx.get("voiced_needs") or [None])[0]
    if need:
        queries.append(need)
    if c.get("industry"):
        queries.append(f"{c['industry']} use case")
    # Retrieve against what THIS company actually does and runs, so the chunks
    # that come back are relevant to the account rather than generic.
    focus = (c.get("keywords") or [])[:5]
    if focus:
        queries.append(f"{name} for {', '.join(focus)}")
    stack = (c.get("technologies") or [])[:5]
    if stack:
        queries.append(f"{name} integration with {', '.join(stack)}")

    try:
        from nexus.services.rag_reply import _retrieve_chunks
    except Exception as exc:  # noqa: BLE001
        log.debug("meeting_agenda: RAG import failed: %s", exc)
        return

    seen: set = set()
    picked: List[str] = []
    for q in queries:
        q = (q or "").strip()
        if not q or len(picked) >= 10:
            continue
        try:
            chunks = _retrieve_chunks(db, product_id, workspace_id, q, top_k=4) or []
        except Exception as exc:  # noqa: BLE001
            log.debug("meeting_agenda: retrieval failed for %r: %s", q[:40], exc)
            continue
        for ch in chunks:
            clean = _clean_chunk(ch if isinstance(ch, str) else "")
            if not clean:
                continue
            key = clean.lower()[:120]  # near-duplicate signature
            if key in seen:
                continue
            seen.add(key)
            picked.append(clean)
            if len(picked) >= 10:
                break
    ctx["kb_snippets"] = picked
    ctx["kb_queries"] = [q for q in queries if (q or "").strip()]


# ─── Generation (Gemini + deterministic fallback) ─────────────────────────────

_SYSTEM = (
    "You write a short, prospect-facing meeting agenda for an upcoming product "
    "demo. It will be shared with the prospect, so it is professional, specific, "
    "and written in the second person ('your team'). "
    "Use ONLY the context and product-knowledge snippets provided. NEVER invent "
    "company facts, customer names, or product features that are not in the "
    "context — if a section's inputs are thin, keep it short and general rather "
    "than making something up. "
    "CRITICAL: never state ANY number, percentage, statistic, metric, price, or "
    "quantified claim (e.g. accuracy %, ROI %, time saved, customer counts, "
    "'2x faster') unless that exact figure appears VERBATIM in the provided "
    "context. If you don't have a specific figure, describe the benefit "
    "qualitatively instead. "
    "Do NOT include any internal sales notes: no objection-handling, no talking "
    "points, no pricing tactics. No greeting, no sign-off, no preamble, never say "
    "you are an AI, and no filler adjectives."
)


def _context_block(ctx: Dict[str, Any]) -> str:
    p = ctx.get("product") or {}
    c = ctx.get("company") or {}
    a = ctx.get("attendee") or {}
    lines = [
        "PRODUCT (what the demo is about):",
        f"  name: {p.get('name') or '(unknown)'}",
        f"  category: {p.get('category') or '(none)'}",
        f"  value_proposition: {p.get('value_proposition') or '(none on file)'}",
        f"  common_pain_points_it_solves: {p.get('pain_points') or '(none on file)'}",
        "",
        # The capabilities are the raw material of the agenda's selling points.
        # They used to be dumped as a bare Python list next to five other
        # fields, so the model treated them as background rather than as the
        # thing it is meant to sell. Given their own headed block with an
        # explicit instruction, each one becomes a bullet.
        "CAPABILITIES / FEATURES — the raw material for your selling points.",
        "Each one below should become a bullet: name the capability, then what",
        "it gets THIS prospect. Do not list them as features; sell them.",
    ]
    caps = [x for x in (p.get("key_benefits") or []) if str(x).strip()]
    lines += [f"  - {c}" for c in caps[:8]] or ["  (none on file)"]
    lines += [
        "",
        "PROSPECT:",
        f"  attendee: {a.get('name') or a.get('email') or '(unknown)'}"
        + (f" — {a.get('role')}" if a.get('role') else ""),
        f"  headline: {a.get('headline') or '(nothing on file)'}",
        "",
    ]
    try:
        from nexus.services.company_context import format_company_block

        lines.append(format_company_block(c))
    except Exception:  # noqa: BLE001
        lines += [
            "THE PROSPECT'S COMPANY:",
            f"  name: {c.get('name') or '(nothing on file)'}",
            f"  industry: {c.get('industry') or '(nothing on file)'}",
        ]
    lines += [
        "",
        "WHAT THE PROSPECT ACTUALLY SAID (their voiced needs — lead with these):",
    ]
    voiced = ctx.get("voiced_needs") or []
    lines += [f"  - {v}" for v in voiced] or ["  (no replies yet)"]
    kb = ctx.get("kb_snippets") or []
    if kb:
        lines += ["", "PRODUCT KNOWLEDGE (grounding — use for accurate product facts):"]
        lines += [f"  <chunk>{k}</chunk>" for k in kb]
    return "\n".join(lines)


# ─── Brevity: the agenda sits in a calendar invite, so it must skim in ~10s ───
#
# A prompt alone does not hold a length — the model drifts, and the only prior
# post-check was "do the three headings exist". These caps are enforced in code
# after generation so the shape is guaranteed, not requested.
_MAX_BULLETS = 3            # per bullet section
_MAX_BULLET_WORDS = 13      # one punchy line — hook + payoff, nothing more
_MAX_OVERVIEW_WORDS = 34    # one full sentence, room to actually say something
# Words we will run PAST the cap to reach a clean break. A slightly long
# complete thought always beats a short fragment.
_TRIM_OVERFLOW = 6
_MAX_TOKENS = 420           # generation budget (was 1200 — room to ramble)

# What the model is ASKED to hit. Deliberately below the ceiling the per-item
# caps imply (~110 words: purpose + overview + 6 bullets + headings) so the
# prompt biases short and the trimmer is a backstop, not the primary control.
# The caps above are what is actually enforced.
_TARGET_TOTAL_WORDS = 90

# Any bullet marker a model actually emits. Gemini favours "*   item"; matching
# only "- " sent those lines down the paragraph branch, so the calendar invite
# rendered a literal asterisk instead of a list item. Shared by _tighten (which
# normalises every bullet to "- ") and _md_to_html (which must still cope with
# older stored markdown written before that normalisation existed).
_BULLET_RE = re.compile(r"^\s*[-*•]\s+")

# Filler that survives most prompts. Stripped from the START of a bullet only,
# so a legitimate mid-sentence use is left alone.
_FILLER_OPENER_RE = re.compile(
    r"^(?:we(?:'ll| will)\s+(?:explore|discuss|cover|walk through|dive into|look at|review)"
    r"|let'?s\s+\w+|a\s+deep\s+dive\s+into|an?\s+overview\s+of)\s+",
    re.I,
)


def _purpose_line(
    product_name: str,
    company_name: Optional[str],
    when: Optional[str],
    duration_min: Optional[int] = None,
) -> str:
    """The title block: a bold line, then one line of framing.

    Naming three things and joining them with dots ("Your X demo · Acme") tells
    the reader nothing they didn't already know. What a prospect opening an
    invite actually wants is: when is it, how long will it take, and what is
    this document for. So the bold line carries the commitment (what + when +
    how long) and the line under it says who it was put together for and what
    it is — which is the part that makes it read as prepared rather than
    generated.

    No possessive pronoun: "Your X demo" reads as a mail-merge template, and
    the recipient already knows whose meeting it is. `when` now carries its own
    start–end range, so a separate "· 30 min" would just repeat it.

    Every field here is real or omitted. Built in one place so the model prompt
    and the deterministic fallback can never drift apart.
    """
    head = f"{product_name} demo"
    if when:
        head += f" · {when}"

    who = _display_company(company_name)
    sub = (
        f"Prepared for {who} — here's what we'll cover."
        if who else "Here's what we'll cover."
    )
    return f"**{head}**\n{sub}"


def _display_company(name: Optional[str]) -> Optional[str]:
    """Tidy a company name for display.

    Lead records carry whatever the source gave us — "neuzenai" arrives
    lower-case, which reads as a typo in a document the prospect sees. Only
    fixes obviously-unpresented casing (all-lower / all-upper single tokens);
    a name the user already cased deliberately ("NeuZenAI", "rater8") is left
    exactly as it is.
    """
    n = (name or "").strip()
    if not n or n.lower() in {"your team", "their company"}:
        return None
    if n.islower() or (n.isupper() and " " not in n and len(n) > 4):
        return n.title()
    return n


def _balance_bold(s: str) -> str:
    """Re-close a '**' left dangling by a trim, so the invite never renders raw
    asterisks."""
    return s + "**" if s.count("**") % 2 else s


# Words that must never end a trimmed line — cutting on one leaves the bullet
# visibly truncated ("…before overspend happens rather").
_DANGLING = {
    # connectives / prepositions
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of",
    "on", "or", "over", "rather", "than", "that", "the", "their", "then", "these",
    "this", "to", "up", "via", "which", "while", "with", "without", "your",
    # determiners / quantifiers — "…spend across every" reads just as truncated
    "all", "any", "each", "every", "its", "most", "much", "our", "some", "such",
}


def _trim_words(s: str, cap: int) -> str:
    """Shorten to about `cap` words WITHOUT ever leaving a fragment.

    The rule is completeness first, brevity second. A word-count cut produces
    lines like "sync ad platforms and data warehouses without manual" — the
    reader stops on a half-thought, which reads worse in front of a prospect
    than one extra clause would. So we only ever cut where the text gives us a
    natural seam:

      1. keep just the first sentence, if that alone gets us near the cap;
      2. otherwise cut at a clause boundary, allowing a few words of overflow
         to reach one;
      3. otherwise leave the line ALONE — long but whole.

    Length is then really the prompt's job; this is the backstop that keeps the
    shape sane without mangling meaning.
    """
    words = s.split()
    limit = cap + _TRIM_OVERFLOW
    # Anything already within tolerance is FINAL. Re-testing against the bare
    # cap would let a second pass cut a line we deliberately allowed to run long
    # to stay whole — so repeated trimming would erode bullets a clause at a
    # time. `cap` is the target we ask the model for; `limit` is what we accept.
    if len(words) <= limit:
        return s

    # 1. First sentence — the cleanest possible cut, already complete.
    sentences = re.split(r"(?<=[.!?])\s+", s.strip())
    if len(sentences) > 1:
        first = sentences[0].strip()
        if first and len(first.split()) <= limit:
            return _balance_bold(first)

    # 2. Clause boundary at or just past the cap.
    window = " ".join(words[:limit])
    min_len = max(4, cap // 2)
    for sep in ("—", " - ", ";", ",", ":"):
        i = window.rfind(sep)
        if i <= 0:
            continue
        cut = window[:i].rstrip(" ,;:—-")
        # Must still be a substantial line, and must not end mid-construction.
        if len(cut.split()) >= min_len and cut.split()[-1].strip("*.,;:").lower() not in _DANGLING:
            return _balance_bold(cut)

    # 3. No honest place to cut — keep the whole thought.
    return s


def _tighten(md: str) -> str:
    """Enforce the punchy shape on generated markdown.

    Caps bullets per section and words per bullet, trims the Overview to one
    sentence, and drops filler openers. Structure-preserving: the purpose line
    and the three H2s always survive, so the output contract still holds.
    """
    out: List[str] = []
    in_bullet_section = False
    seen_heading = False
    bullets_kept = 0
    prose_buf: List[str] = []

    def _flush_prose() -> None:
        """Emit the buffered Overview as ONE sentence. Buffered rather than
        handled line-by-line because a model may hard-wrap the paragraph, and
        cutting at the first newline would truncate mid-thought."""
        if not prose_buf:
            return
        joined = " ".join(prose_buf).strip()
        prose_buf.clear()
        if joined:
            first = re.split(r"(?<=[.!?])\s+", joined)[0]
            out.append(_trim_words(first, _MAX_OVERVIEW_WORDS))

    for raw in (md or "").split("\n"):
        stripped = raw.strip()
        if not stripped:
            _flush_prose()
            out.append("")
            continue

        if stripped.startswith("## "):
            _flush_prose()
            heading = stripped[3:].strip()
            in_bullet_section = heading.lower() != "overview"
            seen_heading = True
            bullets_kept = 0
            out.append(stripped)
            continue

        if _BULLET_RE.match(stripped):
            _flush_prose()
            if not in_bullet_section:
                in_bullet_section = True   # model bulleted the Overview — allow it
            if bullets_kept >= _MAX_BULLETS:
                continue                   # drop the tail
            body = _FILLER_OPENER_RE.sub("", _BULLET_RE.sub("", stripped).strip())
            if not body:
                continue
            out.append("- " + _trim_words(body, _MAX_BULLET_WORDS))
            bullets_kept += 1
            continue

        # Prose before any heading is the bold purpose line — pass it through.
        if not seen_heading:
            out.append(stripped)
            continue
        if not in_bullet_section:
            prose_buf.append(stripped)     # Overview body — collected, then clipped
            continue
        out.append(stripped)
    _flush_prose()

    # Collapse blank runs left by dropped lines.
    tight: List[str] = []
    for ln in out:
        if ln == "" and (not tight or tight[-1] == ""):
            continue
        tight.append(ln)
    return "\n".join(tight).strip() + "\n"


def _generate_markdown(ctx: Dict[str, Any]) -> Optional[str]:
    """One Gemini call. Returns None on any failure so the caller uses the
    deterministic fallback."""
    import os
    if not os.getenv("GEMINI_API_KEY"):
        return None
    product_name = (ctx.get("product") or {}).get("name") or "the product"
    company_name = (ctx.get("company") or {}).get("name") or "your team"
    when = (ctx.get("meeting") or {}).get("when_label")
    duration_min = (ctx.get("meeting") or {}).get("duration_min")
    user = (
        _context_block(ctx)
        + "\n\n---\n"
        + "Write the agenda as Markdown. Start with EXACTLY these two title lines, "
        + "copied verbatim (bold line, then the framing line under it):\n"
        + f"{_purpose_line(product_name, company_name, when, duration_min)}\n"
        + "Then output EXACTLY these three H2 sections, in this order, with these "
        + "exact headings (no others):\n"
        + "## Overview\n"
        + "## How It Addresses Your Needs\n"
        + "## Value & Business Benefits\n\n"
        + "THIS GOES IN A CALENDAR INVITE — it must be skimmable in 10 seconds. "
        + f"Hard limit: whole agenda under {_TARGET_TOTAL_WORDS} words.\n\n"
        + "Guidance:\n"
        + f"- Overview: ONE complete sentence, up to {_MAX_OVERVIEW_WORDS} words — "
        + f"what {product_name} is, what it does, and who it is for. Use the room; "
        + "this is the only prose in the agenda. No preamble, no history.\n"
        + f"- How It Addresses Your Needs: EXACTLY {_MAX_BULLETS} bullets. Each one "
        + "PAIRS A REAL CAPABILITY with what it solves for them — name the "
        + "capability in the bold hook, put their problem in the payoff. LEAD with "
        + f"whatever {company_name} actually raised (voiced needs), matched to the "
        + "capability that answers it; then relevant patterns for their industry, "
        + "phrased as 'teams like yours often…' when it's a general pattern rather "
        + "than a stated fact about them.\n"
        + f"- Value & Business Benefits: EXACTLY {_MAX_BULLETS} bullets. Take the "
        + "capabilities you have NOT already used above and sell the OUTCOME each "
        + "one produces — time saved, revenue won, risk removed. Same rule: name "
        + "the capability, then the business result. Never repeat a capability "
        + "already used in the section above.\n\n"
        + "SELLING POINTS, NOT A FEATURE LIST. Every bullet in both sections must "
        + "come from a real capability in the CAPABILITIES block or the product "
        + "knowledge, and must say what that capability GETS them. "
        + "Weak: '**Conversational AI** — ask questions about the data.' "
        + "Strong: '**Ask in plain English** — answers without waiting on analysts.' "
        + "The first names a feature; the second sells the payoff.\n\n"
        + "SHOW THE PRODUCT'S POWER. This is a pitch, not a summary — lead with "
        + "the most impressive thing it genuinely does. Where the context "
        + "actually supports it, use the current technical vocabulary the "
        + "product really earns: AI, agents, agentic, automated, real-time, "
        + "predictive, models, orchestration. These are only allowed when the "
        + "capability list or product knowledge genuinely shows that capability "
        + "— calling something an AI agent when it is a form is a lie the "
        + "prospect will catch on the call. Prefer the strongest ACCURATE word.\n\n"
        + "PRONOUNS. Do not use 'your', 'yours', 'our', 'ours', 'mine' or 'we' "
        + "anywhere in the bullets or the Overview. Name the thing instead: "
        + "'the marketing team', 'existing ad platforms', "
        + f"'{company_name}'. Weak: 'connect your data sources'. "
        + "Strong: 'connects existing data sources'. (The three H2 headings are "
        + "fixed and are the one exception — copy them exactly as given.)\n\n"
        + "BULLET FORM — every bullet, both sections:\n"
        + f"- ONE line, HARD MAX {_MAX_BULLET_WORDS} words. No second sentence, no "
        + "sub-clauses, no trailing explanation. Punchy, not descriptive.\n"
        + "- Start with a bolded 2-3 word hook, then an em dash, then the payoff in "
        + "a handful of words. Follow these shapes exactly:\n"
        + "    **Unified spend view** — every channel in one place.\n"
        + "    **One-click connectors** — no more manual CSV exports.\n"
        + "    **Budget guardrails** — catch overspend before it happens.\n"
        + "- A bullet that needs a comma to breathe is too long. Cut it down.\n"
        + "- CRITICAL: every bullet must be a COMPLETE thought that ends properly. "
        + "Never stop mid-phrase. If an idea will not fit in the word budget, say "
        + "a SMALLER idea completely rather than a bigger one halfway. "
        + "Bad: 'sync ad platforms and data warehouses without manual'. "
        + "Good: 'sync ad platforms automatically — no manual exports'.\n"
        + "- Say the thing itself. Cut every filler verb: no 'we'll explore', "
        + "'we will discuss', 'walk through', 'dive into', 'leverage', 'seamlessly', "
        + "'robust', 'cutting-edge', 'empower'.\n"
        + f"- Do not repeat '{company_name}' or '{product_name}' in every bullet — "
        + "the reader already knows whose meeting this is.\n\n"
        + "Output ONLY the Markdown, starting with the bold purpose line."
    )
    try:
        from nexus.services import gemini
        out = gemini.chat_completion(
            system=_SYSTEM, user=user, temperature=0.2, max_tokens=_MAX_TOKENS
        )
        out = (out or "").strip()
        # Sanity: must contain all three required headings, else fall back.
        if out and all(("## " + s) in out for s in SECTIONS):
            # Prompts drift; the shape is enforced deterministically after the fact.
            return _tighten(out)
        log.warning("meeting_agenda: model output missing required sections — using fallback")
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("meeting_agenda: Gemini call failed: %s", exc)
        return None


def _fallback_markdown(ctx: Dict[str, Any]) -> str:
    """A valid 3-section agenda built only from real fields — used when the LLM
    isn't available. Honest about missing data; never invents."""
    p = ctx.get("product") or {}
    c = ctx.get("company") or {}
    when = (ctx.get("meeting") or {}).get("when_label")
    product_name = p.get("name") or "the product"
    company_name = c.get("name")

    purpose = _purpose_line(
        product_name, company_name, when, (ctx.get("meeting") or {}).get("duration_min")
    )

    # Overview — one clipped sentence, same budget the model is held to.
    overview = product_name
    if p.get("category"):
        overview += f" ({p['category']})"
    if p.get("value_proposition"):
        overview += f" — {p['value_proposition']}"
    else:
        overview += " — product overview not yet configured."
    overview = _trim_words(overview, _MAX_OVERVIEW_WORDS)

    # How it addresses your needs — quote the prospect, but only a short clip
    # (raw replies run to 400 chars and swamped the invite).
    needs_lines: List[str] = []
    _hook = "**Raised earlier** —"
    _quote_cap = _MAX_BULLET_WORDS - len(_hook.split())   # hook counts toward the cap
    for v in (ctx.get("voiced_needs") or [])[:_MAX_BULLETS]:
        needs_lines.append(f"- {_hook} {_trim_words(v, _quote_cap)}")
    if not needs_lines:
        for pain in (p.get("pain_points") or [])[:_MAX_BULLETS]:
            needs_lines.append(f"- {_trim_words(pain, _MAX_BULLET_WORDS)}")
    if not needs_lines:
        needs_lines.append("- Tailored to the priorities raised on the call.")

    # Value & benefits
    val_lines: List[str] = [
        f"- {_trim_words(b, _MAX_BULLET_WORDS)}"
        for b in (p.get("key_benefits") or [])[:_MAX_BULLETS]
    ]
    if not val_lines and p.get("value_proposition"):
        val_lines.append(f"- {_trim_words(p['value_proposition'], _MAX_BULLET_WORDS)}")
    if not val_lines:
        val_lines.append("- Covered live on the call.")

    return (
        f"{purpose}\n\n"
        f"## Overview\n{overview}\n\n"
        f"## How It Addresses Your Needs\n" + "\n".join(needs_lines) + "\n\n"
        f"## Value & Business Benefits\n" + "\n".join(val_lines) + "\n"
    )


# Minimal, dependency-free Markdown→HTML for the stored agenda_html (used by the
# Phase-2 invite). Handles the shapes this agent emits: bold line, ## headings,
# bullets (-, * or •), and paragraphs.
def _md_to_html(md: str) -> str:
    html: List[str] = []
    in_list = False

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def inline(s: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(s))

    for raw in (md or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                html.append("</ul>"); in_list = False
            continue
        if line.startswith("## "):
            if in_list:
                html.append("</ul>"); in_list = False
            html.append(f"<h3>{inline(line[3:].strip())}</h3>")
        elif _BULLET_RE.match(line):
            if not in_list:
                html.append("<ul>"); in_list = True
            html.append(f"<li>{inline(_BULLET_RE.sub('', line).strip())}</li>")
        else:
            if in_list:
                html.append("</ul>"); in_list = False
            html.append(f"<p>{inline(line.strip())}</p>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def generate_agenda(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Produce {markdown, html, model, grounding}. Never raises — always returns
    a valid agenda (fallback when the LLM is unavailable).

    `grounding.grounded` is True ONLY when this run actually retrieved product-KB
    chunks. With zero chunks the model has nothing but the bare product fields to
    work from, so the result is generic-at-best and must not reach a prospect —
    the delivery gate keys off this flag, not off "an asset exists".
    """
    from nexus.services import gemini
    md = _generate_markdown(ctx)
    model = gemini.CHAT_MODEL if md else "fallback"
    if not md:
        md = _fallback_markdown(ctx)
    chunks = len(ctx.get("kb_snippets") or [])
    return {
        "markdown": md,
        "html": _md_to_html(md),
        "model": model,
        "grounding": {
            "grounded": chunks > 0,
            "kb_chunks": chunks,
            "queries": (ctx.get("kb_queries") or [])[:6],
            "voiced_needs": len(ctx.get("voiced_needs") or []),
        },
    }


# ─── Orchestrator: assemble → ground → generate → persist (idempotent) ────────

def agenda_is_grounded(db: Session, booking_id: int) -> bool:
    """THE prospect-delivery gate: True only when the stored agenda for this
    booking was actually generated from retrieved product-KB chunks.

    `product_has_kb` below answers a weaker question — "does this product own an
    indexed asset?" — which stays True even when retrieval returned nothing for
    this booking (Pinecone unconfigured/unreachable, namespace mismatch, every
    chunk filtered as boilerplate). In that case the agenda is model-prior prose,
    and gating on asset-existence would push it into the prospect's calendar
    invite. So delivery keys off real per-agenda provenance instead.

    Legacy rows written before the `grounding` column existed have no provenance
    to trust, so they read as NOT grounded and fall back to the one-liner; the
    next regenerate re-grounds them. Never raises."""
    try:
        row = db.execute(
            text("SELECT grounding FROM nexus_meeting_agendas WHERE demo_booking_id = :b"),
            {"b": booking_id},
        ).first()
    except Exception:  # noqa: BLE001
        return False
    g = row[0] if row else None
    if not isinstance(g, dict):
        return False
    return bool(g.get("grounded")) and int(g.get("kb_chunks") or 0) > 0


def product_has_kb(db: Session, product_id: Optional[int]) -> bool:
    """Cheap pre-check: does the product own at least one indexed asset?

    NOT sufficient on its own as a delivery gate (see `agenda_is_grounded`) —
    use it only to decide whether generation is worth attempting. Never raises."""
    if not product_id:
        return False
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM nexus_product_assets "
                "WHERE product_id = :p AND status = 'indexed'"
            ),
            {"p": product_id},
        ).scalar()
        return bool(n and int(n) > 0)
    except Exception:  # noqa: BLE001
        return False


def generate_for_booking(
    db: Session, *, booking_id: int, force: bool = False, source: str = "auto"
) -> Dict[str, Any]:
    """Create/refresh the stored agenda for a booking. Idempotent (no-op if one
    exists unless `force`). Best-effort: never raises, returns a status dict so
    booking/invite flows are never broken by agenda generation."""
    from nexus.models_phase6 import MeetingAgenda
    try:
        existing = (
            db.query(MeetingAgenda)
            .filter(MeetingAgenda.demo_booking_id == booking_id)
            .first()
        )
        if existing and not force:
            return {"ok": True, "created": False, "cached": True, "agenda_id": existing.id}

        ctx = build_agenda_context(db, booking_id=booking_id)
        if ctx is None:
            return {"ok": False, "reason": "no_booking"}
        ground_product_facts(db, ctx)
        out = generate_agenda(ctx)

        row = existing or MeetingAgenda(demo_booking_id=booking_id)
        row.workspace_id = ctx["workspace_id"]
        row.lead_id = ctx["lead_id"]
        row.product_id = ctx["product_id"]
        row.agenda_markdown = out["markdown"]
        row.agenda_html = out["html"]
        row.model = out["model"]
        row.grounding = out["grounding"]
        row.source = source
        row.generated_at = datetime.utcnow()
        if existing is None:
            db.add(row)
        db.commit()
        if not out["grounding"]["grounded"]:
            log.info(
                "meeting_agenda: booking=%s generated WITHOUT KB grounding "
                "(0 chunks retrieved) — stays rep-facing, invite keeps the one-liner",
                booking_id,
            )
        return {
            "ok": True,
            "created": existing is None,
            "cached": False,
            "agenda_id": row.id,
            "model": out["model"],
            "grounded": out["grounding"]["grounded"],
            "kb_chunks": out["grounding"]["kb_chunks"],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("meeting_agenda.generate_for_booking failed for booking=%s: %s", booking_id, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": "error"}
