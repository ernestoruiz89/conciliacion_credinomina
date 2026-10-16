"""Backwards-compatible entry point for sites where this patch is still pending."""


def execute():
    from credinomina_reconciliation.credit_effect_migration import execute as migrate
    migrate()
