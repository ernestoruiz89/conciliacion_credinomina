"""Index adjustment discovery without rewriting documents or reconciliations."""
from credinomina_reconciliation.application_adjustment_indexes import ensure_indexes


def execute():
    ensure_indexes()
