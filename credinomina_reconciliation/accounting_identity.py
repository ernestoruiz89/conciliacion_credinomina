"""Physical ledger lines are distinct; retries of the same origin are not."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.accounting_evidence import is_deposit, same_ledger_evidence
from credinomina_reconciliation.parsers import SourceFileError, source_key

EVIDENCE_FIELDS = [
    "source_row", "accounting_source_key", "source_date", "source_account",
    "source_currency", "source_debit", "source_credit", "source_voucher",
    "source_description", "accounting_classification", "source_file_hash",
    "tmov", "tdoc", "accounting_reference",
]


def identify_lines(records, file_hash):
    """Keep existing evidence links on retry, including pre-line-identity imports.

    The original hash and row survive individual CSV exports. Equal accounting
    values on different physical lines must never share an evidence document.
    """
    if not records:
        return records
    existing = defaultdict(list)
    positions = sorted({int(row["source_row"]) for row in records})
    for doctype in ("CN Complementary Item", "CN Remittance Allocation"):
        for row in frappe.get_all(doctype, filters={"source_file_hash": file_hash, "source_row": ["in", positions]},
                                 fields=EVIDENCE_FIELDS, limit_page_length=0):
            if row.source_row and row.accounting_source_key:
                existing[int(row.source_row)].append((doctype, row))
    for row in records:
        keys = {
            evidence["accounting_source_key"]
            for doctype, evidence in existing.get(int(row["source_row"]), [])
            if (is_deposit(row) if doctype == "CN Remittance Allocation" else row.get("event_type") == "Ajuste")
            and same_ledger_evidence(row, evidence)
        }
        if len(keys) > 1:
            raise SourceFileError(f"Fila {row['source_row']}: existen varias identidades contables para la misma evidencia; revise los registros de origen.")
        row["accounting_source_key"] = next(iter(keys)) if keys else source_key(
            "accounting-line-v2", file_hash, row["source_row"], row["accounting_source_key"],
        )
    return records
