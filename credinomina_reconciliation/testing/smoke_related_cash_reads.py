"""Exercise literal cash searches against MariaDB; test site and rollback only."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_cancellation import _related_cash


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "CASH-READ-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            other = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-OTHER",
                                       employer_code=marker + "-OTHER")).insert()
            application = marker + "-A[1].*|%_'--"
            period = marker + "-Período (1)"
            item = marker + "-COMP+1"
            journals = [
                ("APP", 1, other.name, [{"aplicacion_id": application, "importe_usd": 40}]),
                ("PERIOD", 1, company.name, [{"periodo": period, "importe_usd": 20}]),
                ("ITEM", 1, other.name, [{"partida": item, "importe_usd": -10}]),
                ("PREFIX", 1, company.name, [{"aplicacion_id": application + "-OTHER"}]),
                ("NOTE", 1, company.name, [{"observacion": application}]),
                ("REGEX", 1, company.name, [{"aplicacion_id": marker + "-A1anything"}]),
                ("DRAFT", 0, company.name, [{"aplicacion_id": application}]),
                ("CANCELLED", 2, company.name, [{"aplicacion_id": application}]),
                ("SELECTED", 1, company.name, [{"aplicacion_id": application}]),
                ("EMPTY", 1, company.name, []),
            ]
            names = {}
            for label, status, employer, journal in journals:
                doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer,
                    deposit_date="2025-08-01", deposit_reference=marker + label,
                    deposit_currency="USD", deposit_amount=100)).insert()
                names[label] = doc.name
                # Synthetic evidence for discovery only; never reconcile these
                # deliberately incomplete fixtures or persist them beyond rollback.
                frappe.db.set_value(doc.doctype, doc.name, dict(docstatus=status,
                    allocation_detail=json.dumps(journal, ensure_ascii=False)), update_modified=False)
            before = frappe.get_all("CN Remittance Allocation", filters={"name": ["in", list(names.values())]},
                                    fields=["*"], order_by="name asc", limit_page_length=0)
            rows = [frappe._dict(name=application, event_type="Aplicacion")]
            periods = [frappe._dict(name=period)]
            items = [frappe._dict(name=item)]
            selected = [frappe._dict(name=names["SELECTED"])]
            short = _related_cash(selected, periods, rows, items)
            # Force the paged implementation with references that have no cash.
            long = _related_cash(selected, periods, rows + [frappe._dict(name=marker + str(i),
                event_type="Aplicacion") for i in range(21)], items)
            expected = {names[key] for key in ("APP", "PERIOD", "ITEM")}
            assert {doc.name for doc in short} == expected
            assert {doc.name: dict(doc) for doc in short} == {doc.name: dict(doc) for doc in long}
            after = frappe.get_all("CN Remittance Allocation", filters={"name": ["in", list(names.values())]},
                                   fields=["*"], order_by="name asc", limit_page_length=0)
            assert after == before
            return dict(literal_identifiers_matched=True, cross_company_evidence_preserved=True,
                        drafts_cancelled_notes_prefixes_excluded=True, small_and_large_reads_equal=True,
                        financial_documents_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
