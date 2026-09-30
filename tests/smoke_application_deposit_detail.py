"""Run only on a disposable/test site; all fixture records are rolled back."""
import json
from uuid import uuid4

import frappe
from credinomina_reconciliation.application_deposit_detail import preview_application_detail, use_application_detail
from credinomina_reconciliation.parsers import parse_collection_file


def run():
    assert "test" in frappe.local.site, "Use a test site only"
    prefix = "APPDETAIL-" + uuid4().hex[:12]
    def insert(doctype, suffix, **values):
        doc = frappe.get_doc(dict(doctype=doctype, name=prefix + suffix, **values))
        doc.db_insert()
        return doc
    try:
        employer = insert("CN Employer", "E", employer_name=prefix, employer_code=prefix, active=1)
        period = insert("CN Reconciliation Period", "P", employer=employer.name,
            payroll_month="2025-04-01", reconciliation_mode="Historica", historical_scope="Mensual",
            status="Parcial", applied_usd=120)
        source = insert("CN Source Import", "I", employer=employer.name,
            source_type="Movimientos contables", status="Importado")
        application = insert("CN Source Row", "A", parent=source.name, parenttype=source.doctype,
            parentfield="rows", idx=1, historical_period=period.name, employer=employer.name,
            event_type="Aplicacion", event_date="2025-04-15", effective=1, match_status="Conciliado",
            currency="USD", amount=100, client_name=prefix + " Ana", client_number=prefix + "1", loan_number="123-1")
        insert("CN Source Row", "B", parent=source.name, parenttype=source.doctype,
            parentfield="rows", idx=2, historical_period=period.name, employer=employer.name,
            event_type="Aplicacion", event_date="2025-04-15", effective=1, match_status="Conciliado",
            currency="USD", amount=20, client_name=prefix + " Bea", client_number=prefix + "2", loan_number="124-1")
        insert("CN Remittance Allocation", "OTHER", docstatus=1, employer=employer.name,
            allocation_detail=json.dumps([{"aplicacion_id": application.name, "importe_usd": 40}]))
        for status in (0, 1):
            deposit = insert("CN Remittance Allocation", "D" + str(status), docstatus=status,
                naming_series="CN-ALLOC-.YYYY.-.#####", usd_currency="USD",
                employer=employer.name, detail_period=period.name, deposit_reference=prefix + str(status),
                deposit_date="2025-05-20", deposit_currency="USD", deposit_amount=60, amount_usd=60,
                result="Pendiente")
            preview = preview_application_detail(deposit.name)
            assert preview["total_usd"] == 80, preview
            result = use_application_detail(deposit.name, preview["fingerprint"], selected_claim_ids=["H:" + application.name])
            deposit.reload()
            assert deposit.docstatus == status
            assert deposit.result == "Pendiente"
            assert deposit.applied_usd == 120
            assert deposit.detail_total_usd == 60
            assert len(deposit.detail_rows) == 1
            assert deposit.detail_rows[0].deducted_usd == 60
            assert not deposit.targets and not deposit.allocation_detail
            file = frappe.get_doc("File", {"file_url": result["file_url"]})
            assert file.is_private
            rows = parse_collection_file(file.file_name, file.get_content(), require_deduction=True, require_name=True)
            assert rows[0]["deducted_usd"] == 60
        return "OK: partial payments, private workbook, real saves in draft and submitted deposits; no auto-reconciliation"
    finally:
        frappe.db.rollback()
        assert not frappe.db.exists("CN Employer", prefix + "E")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    site = sys.argv[1]
    assert "test" in site, "Use a test site only"
    # Invoke from the bench's sites directory, just like bench console.
    frappe.init(site=site, sites_path=str(Path.cwd()))
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        print(run())
    finally:
        frappe.destroy()
