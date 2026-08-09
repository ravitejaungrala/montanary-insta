"""Smoke test for the shared Gemini client (after the no-timeout hang fix).

Sends a single tiny "hello" prompt through nexus.services.gemini.chat_completion
and prints the response + elapsed time + the client's HTTP timeout. One minimal
Gemini call (negligible cost) — NO Apollo, NO database access.

Run from apps/backend:
    python scripts/test_gemini_hello.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# No DB needed — point the lazy engine at a dummy URL so nothing real is touched.
os.environ.setdefault("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")

# Pull GEMINI_API_KEY from apps/backend/.env if it isn't already in the env.
if not os.getenv("GEMINI_API_KEY"):
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
                    break

if not os.getenv("GEMINI_API_KEY"):
    raise SystemExit("GEMINI_API_KEY not found in env or apps/backend/.env")

from nexus.services import gemini  # noqa: E402

client = gemini._get_client()
print(f"client HTTP timeout : {client._api_client._http_options.timeout} ms")
print(f"chat model          : {gemini.CHAT_MODEL}")
print('sending prompt      : "hello" ...')

t0 = time.monotonic()
reply = gemini.chat_completion(
    system="You are a helpful assistant.",
    user="hello",
    max_tokens=50,
)
elapsed = time.monotonic() - t0

print(f"elapsed             : {elapsed:.1f}s")
print(f"response            : {reply!r}")
print("\nRESULT:", "PASS — got a response" if reply.strip() else "FAIL — empty response")
