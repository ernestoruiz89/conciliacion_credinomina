"""Physical ledger lines are distinct; retries of the same origin are not."""
import frappe

from credinomina_reconciliation.parsers import source_key


def identify_lines(records, file_hash):
    """Keep existing evidence links on retry, including pre-line-identity imports.

    The original hash and row survive individual CSV exports. Equal accounting
    values on different physical lines must never share an evidence document.
    """
    existing = {}
    for doctype in ("CN Complementary Item", "CN Remittance Allocation"):
        for row in frappe.get_all(doctype, filters={"source_file_hash": file_hash},
                                 fields=["source_row", "accounting_source_key"]):
            if row.source_row and row.accounting_source_key:
                existing[int(row.source_row)] = row.accounting_source_key
    for row in records:
        row["accounting_source_key"] = existing.get(int(row["source_row"])) or source_key(
            "accounting-line-v2", file_hash, row["source_row"], row["accounting_source_key"],
        )
    return records
