"""Python ports of NEXUS Node services from `apps/nexus-legacy/server/services/`.

Conventions:

- One Python module per Node service; same filename in snake_case.
- External SDK clients (NVIDIA NIM, Anthropic, OpenAI, Resend, Gmail,
  MS Graph, Twilio, Playwright) live here, never inside routers.
- Modules here may import from `nexus.models`, `nexus.config`, and
  `core.*`, but should NOT import from `nexus.routers` (one-way
  dependency to keep things acyclic).
"""
