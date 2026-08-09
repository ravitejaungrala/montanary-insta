"""NEXUS port — staging area.

This package is the destination for the ongoing NEXUS → PIPELYT port.
Nothing here is imported by `main.py` while `core.config.NEXUS_ENABLED`
is False (its default), so this package has zero runtime impact on
PIPELYT until features land slice-by-slice.

See `docs/nexus-migration/` for the migration plan and status.
"""
