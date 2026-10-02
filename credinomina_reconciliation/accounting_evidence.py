"""Exact original ledger evidence; a shared key alone is never a relationship."""
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.accounting_types import accounting_code
from credinomina_reconciliation.rounding import money


def evidence_signature(row):
    """Exclude client enrichment, converted amounts and bank/deposit amounts."""
    return (
        str(row.get("event_date") or row.get("source_date") or "")[:10],
        clean_text(row.get("source_account")),
        clean_text(row.get("source_currency")),
        clean_text(row.get("source_voucher") or row.get("voucher")),
        money(row.get("source_debit")), money(row.get("source_credit")),
        clean_text(row.get("source_description") or row.get("description")).casefold(),
        clean_text(row.get("accounting_reference")),
        accounting_code(row.get("tmov")), accounting_code(row.get("tdoc")),
    )


def same_ledger_evidence(first, second):
    """Match money/date/account/voucher/description and available provenance.

    Equal lines at different physical positions remain independent. Missing
    evidence is not proof of a mirror, even when a stale key happens to match.
    """
    signature = evidence_signature(first)
    if not all(signature[:3]) or not any(signature[4:6]):
        return False
    if signature != evidence_signature(second):
        return False
    for field in ("source_file_hash", "source_row"):
        left, right = first.get(field), second.get(field)
        if left and right and str(left) != str(right):
            return False
    return True


def is_deposit(row):
    if row.get("event_type") and row["event_type"] != "Deposito":
        return False
    classification = row.get("accounting_classification")
    return classification == "Depósito" if classification else row.get("event_type") == "Deposito"
