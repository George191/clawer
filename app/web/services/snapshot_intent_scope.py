"""Protect sender-only immutable snapshot intent from generic catalog/data surfaces."""

SNAPSHOT_INTENT_PRIVATE_SCHEMA = "ontology_snapshot_intent_private"
# Independently guard all three if marker or another relation is missing.
SNAPSHOT_INTENT_PROTECTED_REGCLASSES = tuple(
    f"{SNAPSHOT_INTENT_PRIVATE_SCHEMA}.{name}" for name in ("identity", "intents", "records")
)
