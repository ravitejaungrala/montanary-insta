"""Phase 17 — persist Gemini's grounded product description.

`analyze_product` returns a 3-section description (what the company is / what
they do + key capabilities / who they serve) that is far richer than the
`value_proposition` + `key_benefits` columns it sits beside. It used to be
discarded on the floor of `/nexus/analyze`, so the email writer had to
re-synthesize a thinner version from those two columns at send time.

Storing it costs one nullable column and puts the real capability prose in
front of every writer. The original objection to persisting it was staleness;
that does not apply because the column is written in the SAME upsert as
value_proposition / key_benefits / icp, so it is exactly as fresh as the
fields the sequencer already trusts. NULL rows (legacy, and the pasted-content
path) still fall back to the synthesized description.

Purely additive. See product_knowledge_persistence_plan.md.
"""

from __future__ import annotations


MIGRATIONS = [
    "ALTER TABLE nexus_products ADD COLUMN IF NOT EXISTS product_description JSONB",
]
