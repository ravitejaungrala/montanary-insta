"""One-off probe: fetch one message from contact@neuzenai.com via MS Graph
and print it. Used to verify the API + credentials are wired correctly.

Run from `apps/backend/`:
    venv/Scripts/python.exe scripts/_probe_ms_graph.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Force UTF-8 stdout so Outlook bodies with non-cp1252 chars don't crash print().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Make `nexus.*` importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

MAILBOX = "contact@neuzenai.com"


def _short(s: str, n: int = 500) -> str:
    if not s:
        return ""
    s = " ".join(str(s).split())
    return s[:n] + ("..." if len(s) > n else "")


async def main() -> int:
    from nexus.services import ms_graph_client

    print(f"Probing MS Graph for {MAILBOX} ...\n")

    # 1. Token
    try:
        token = await ms_graph_client.get_access_token()
        print(f"[ok] token acquired ({len(token)} chars)\n")
    except Exception as e:
        print(f"[FAIL] token acquire: {e}")
        return 1

    # 2. Try unread first
    messages = await ms_graph_client.list_messages(
        MAILBOX, only_unread=True, top=1
    )
    label = "unread"
    if not messages:
        # Fall back so we still show *something* if inbox is empty/all-read
        messages = await ms_graph_client.list_messages(
            MAILBOX, only_unread=False, top=1
        )
        label = "any (read or unread)"

    if not messages:
        print(f"[ok] list_messages returned 0 rows for {MAILBOX} "
              f"({label}). Mailbox is empty or filtered out.")
        return 0

    msg = messages[0]
    from_addr = (
        ((msg.get("from") or {}).get("emailAddress") or {}).get("address")
        or ""
    )
    from_name = (
        ((msg.get("from") or {}).get("emailAddress") or {}).get("name")
        or ""
    )
    to_recips = msg.get("toRecipients") or []
    to_addrs = [
        ((r or {}).get("emailAddress") or {}).get("address") or ""
        for r in to_recips
    ]
    body_obj = msg.get("body") or {}
    body_ct = (body_obj.get("contentType") or "").lower()
    body_preview = msg.get("bodyPreview") or ""
    body_content = body_obj.get("content") or ""

    print(f"[ok] fetched 1 message ({label})")
    print("-" * 60)
    print(f"  Graph id        : {msg.get('id')}")
    print(f"  InternetMsgID   : {msg.get('internetMessageId')}")
    print(f"  conversationId  : {msg.get('conversationId')}")
    print(f"  Received        : {msg.get('receivedDateTime')}")
    print(f"  isRead          : {msg.get('isRead')}")
    print(f"  From            : {from_name} <{from_addr}>")
    print(f"  To              : {', '.join(to_addrs)}")
    print(f"  Subject         : {msg.get('subject')}")
    print(f"  Body type       : {body_ct}")
    print("-" * 60)
    print("  Body preview (plaintext, first 500 chars):")
    print()
    print(f"  {_short(body_preview, 500)}")
    print()
    if body_content and body_content != body_preview:
        print("  Body content (first 500 chars):")
        print()
        print(f"  {_short(body_content, 500)}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
