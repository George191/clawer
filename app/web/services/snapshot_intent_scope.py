"""Protect sender-only immutable snapshot intent from generic catalog/data surfaces."""

SNAPSHOT_INTENT_PRIVATE_SCHEMA = "ontology_snapshot_intent_private"
SNAPSHOT_INTENT_FROZEN_REGCLASSES = tuple(
    f"{SNAPSHOT_INTENT_PRIVATE_SCHEMA}.{name}" for name in ("identity", "intents", "records")
)
SNAPSHOT_INTENT_DELIVERY_REGCLASS = f"{SNAPSHOT_INTENT_PRIVATE_SCHEMA}.deliveries"
# Each relation is evidence independently, even when its siblings are missing.
SNAPSHOT_INTENT_PROTECTED_REGCLASSES = SNAPSHOT_INTENT_FROZEN_REGCLASSES + (SNAPSHOT_INTENT_DELIVERY_REGCLASS,)
