"""Live review harness for the RAG reply agent (rag_reply.generate).

Auto-picks a workspace that has a product, then generates a suggested reply for
three sample inbound emails (QUESTION / NOT_INTERESTED / OUT_OF_OFFICE) so we can
eyeball tone, grounding, and the guardrails.

Run from apps/backend with the backend venv (needs GEMINI_API_KEY + PINECONE_*):

  ./venv/Scripts/python.exe scripts/test_reply_agent.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402
from nexus.services import rag_reply  # noqa: E402


SAMPLES = [
    (
        "QUESTION",
        "Hi there,\n\nThanks for reaching out. Before I take this to my team, can "
        "you tell me how your platform actually handles lead enrichment and whether "
        "it integrates with the CRM we already use? Trying to understand what makes "
        "you different.\n\nRegards,\nPriya",
    ),
    (
        "NOT_INTERESTED",
        "Hi,\n\nAppreciate the note but we're all set on this front and not looking "
        "to change anything right now. Please take me off your list for this one.\n\n"
        "Thanks,\nMarcus",
    ),
    (
        "OUT_OF_OFFICE",
        "Thank you for your email. I am currently out of the office on leave with "
        "limited access to email and will return on Monday, 14 July. For anything "
        "urgent please contact my colleague. I will respond on my return.\n\n"
        "Best regards,\nDana",
    ),
]


def _hr(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def _pick_workspace(db):
    """Prefer a workspace whose most-recent active campaign has a product;
    fall back to any workspace that owns a product."""
    row = db.execute(
        text(
            """
            SELECT c.workspace_id, c.product_id
            FROM nexus_campaigns c
            WHERE c.product_id IS NOT NULL
            ORDER BY (c.status = 'active') DESC, c.created_at DESC NULLS LAST
            LIMIT 1
            """
        )
    ).first()
    if row:
        return int(row[0]), int(row[1])
    row = db.execute(
        text(
            "SELECT workspace_id, id FROM nexus_products "
            "WHERE workspace_id IS NOT NULL "
            "ORDER BY created_at DESC NULLS LAST LIMIT 1"
        )
    ).first()
    if row:
        return int(row[0]), int(row[1])
    return None, None


def main() -> None:
    db = SessionLocal()
    try:
        ws, pid = _pick_workspace(db)
        if ws is None:
            print("No workspace with a product found — cannot test.")
            return

        prod = db.execute(
            text("SELECT name, value_proposition FROM nexus_products WHERE id = :p"),
            {"p": pid},
        ).first()
        print(f"Workspace: {ws}   Product: {pid}   Name: {prod[0] if prod else '?'}")
        print(f"GEMINI_API_KEY set: {bool(os.getenv('GEMINI_API_KEY'))}")

        try:
            from nexus.services import pinecone_kb
            print(f"Pinecone configured: {pinecone_kb.is_configured()}")
        except Exception as e:
            print(f"Pinecone import failed: {e}")

        for intent, body in SAMPLES:
            _hr(f"INTENT: {intent}")
            # Show what grounding the agent actually retrieves.
            chunks = rag_reply._retrieve_chunks(db, pid, ws, query_text=body, top_k=5)
            src = "pinecone"
            if not chunks:
                chunks = rag_reply._fallback_grounding(db, pid)
                src = "fallback(product desc)" if chunks else "none"
            print(f"[grounding source: {src}, {len(chunks)} chunk(s)]")
            for i, c in enumerate(chunks, 1):
                print(f"  chunk{i}: {c[:160].strip()}{'...' if len(c) > 160 else ''}")

            print("\n--- INBOUND ---")
            print(body)
            print("\n--- REPLY ---")
            reply = rag_reply.generate(
                inbound_text=body,
                intent=intent,
                workspace_id=ws,
                db=db,
                product_id=pid,
            )
            print(reply)
    finally:
        db.close()


if __name__ == "__main__":
    main()
