"""Rollback-only test of deposit renames and closed-period tolerance links."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.patches.v1_0.rename_deposits_by_date import execute
from credinomina_reconciliation.tolerance_items import CATEGORY


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "dep-name-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "rounding_tolerance_usd": 0.01}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"}).insert()
        source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "status": "Importado",
            "source_file": f"/private/files/{marker}.xlsx", "historical_backfill": 1,
            "historical_period": period.name})
        source.append("rows", {"source_row": 2, "source_key": marker, "event_type": "Aplicacion",
            "event_date": "2025-04-30", "client_name": marker, "client_number": marker,
            "loan_number": marker, "reference": marker, "currency": "USD", "amount": 46.52,
            "amount_usd": 46.52, "processing_route": "Historica", "historical_period": period.name,
            "effective": 1, "match_status": "Pendiente"})
        source.insert()
        _reconcile_sources(employer.name)
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": marker, "deposit_date": "2025-05-10", "deposit_currency": "USD",
            "deposit_amount": 46.53, "detail_periods": [{"period": period.name}],
            "detail_file": f"/private/files/{marker}-detail.xlsx",
            "detail_source_file": f"/private/files/{marker}-detail.xlsx", "detail_hash": marker})
        deposit.append("detail_rows", {"source_row": 2, "client_name": marker,
            "client_number": marker, "loan_number": marker, "application_reference": marker,
            "deducted_usd": 46.53})
        deposit.insert(set_name="CN-ALLOC-TEST-" + marker)
        old = deposit.name
        deposit.submit()
        _reconcile_sources(employer.name)
        item, = frappe.get_all("CN Complementary Item", filters={"employer": marker, "category": CATEGORY}, pluck="name")
        frappe.db.set_value("CN Complementary Item", item, "registered_deposit", old)
        frappe.db.set_value("CN Reconciliation Period", period.name,
                            {"status": "Cerrado", "deduction_recognition_deposit": old})
        file = frappe.get_doc({"doctype": "File", "file_name": marker + ".xlsx",
            "file_url": f"/private/files/{marker}-detail.xlsx", "is_private": 1,
            "attached_to_doctype": deposit.doctype, "attached_to_name": old})
        file.db_insert()
        deposit.reload()
        before = (deposit.amount_usd, deposit.allocated_usd, deposit.result, deposit.docstatus)
        child = deposit.detail_rows[0].name
        execute()
        new = frappe.db.get_value(deposit.doctype, {"deposit_reference": marker}, "name")
        assert new.startswith("DEP-5-2025-") and new != old
        renamed = frappe.get_doc(deposit.doctype, new)
        assert renamed.reconciliation_identity == old
        assert renamed.detail_rows[0].name == child and renamed.detail_rows[0].parent == new
        assert frappe.db.get_value("File", file.name, "attached_to_name") == new
        assert frappe.db.get_value("CN Complementary Item", item, "registered_deposit") == new
        assert frappe.db.get_value("CN Complementary Item", item, "deposit_source_row") == new
        assert frappe.db.get_value("CN Reconciliation Period", period.name, "deduction_recognition_deposit") == new
        _reconcile_sources(employer.name)
        renamed.reload()
        assert (renamed.amount_usd, renamed.allocated_usd, renamed.result, renamed.docstatus) == before
        assert frappe.db.get_value("CN Complementary Item", item, "status") == "Vigente"
        assert frappe.db.count("CN Complementary Item", {"employer": marker, "category": CATEGORY}) == 1
        execute()
        assert frappe.db.exists(deposit.doctype, new)
        draft = frappe.get_doc({"doctype": deposit.doctype, "employer": marker,
            "deposit_date": "2026-09-01", "deposit_currency": "USD", "deposit_amount": 10,
            "deposit_reference": marker + "-new"}).insert()
        assert draft.name.startswith("DEP-9-2026-")
        original_name = draft.name
        draft.deposit_date = "2026-10-01"
        draft.save()
        assert draft.name == original_name
        return {"new_name": new, "links_children_attachments": "OK", "closed_period_tolerance": "OK",
                "idempotent_patch": "OK", "new_deposit": draft.name, "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
