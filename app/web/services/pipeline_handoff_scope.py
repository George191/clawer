"""Fixed relation identities for generic-workspace isolation of Builder custody."""

HANDOFF_PRIVATE_SCHEMA = "ontology_handoff_private"
# Check records independently. Marker removal or an orphaned ledger must not
# restore free-form SQL, and an empty migration alone must not disable it.
HANDOFF_PROTECTED_REGCLASSES = tuple(
    f"{HANDOFF_PRIVATE_SCHEMA}.{table}" for table in ("sink_state", "copies", "copy_rows")
)
