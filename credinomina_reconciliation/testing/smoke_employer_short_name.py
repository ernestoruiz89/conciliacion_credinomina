"""Short-name naming with real Frappe counters and links; rollback-only test site."""
from unittest.mock import patch

import frappe


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "SHORT-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker + "CODE", "short_name": " " + marker + " "}).insert()
            assert employer.short_name == marker
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica", "historical_scope": "Mensual"}).insert()
            assert period.name == marker + "-4-2025-01", period.name
            imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                "source_file": f"/private/files/{marker}.csv", "historical_backfill": 1, "historical_period": period.name,
                "rows": [{"source_row": 2, "source_key": marker, "event_type": "Aplicacion", "event_date": "2025-04-23",
                    "currency": "USD", "amount": 100, "amount_usd": 100, "client_name": marker,
                    "processing_route": "Historica", "historical_period": period.name}]}).insert()
            assert imported.name == f"CONTA-{marker}-4-2025-001", imported.name
            initial_period, initial_import = period.name, imported.name
            employer.short_name = "  "
            employer.save()
            assert employer.short_name == ""
            assert frappe.db.exists(period.doctype, initial_period)
            assert frappe.db.exists(imported.doctype, initial_import)
            fallback_period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-05-01", "reconciliation_mode": "Historica", "historical_scope": "Mensual"}).insert()
            assert fallback_period.name == marker + "CODE-5-2025-01"
            fallback_period.payroll_month = "2025-06-01"
            employer.short_name = marker + "NEW"
            employer.save()
            fallback_period.save()
            assert fallback_period.name == marker + "NEW-6-2025-01"
            employer.short_name = ""
            employer.save()
            row_name = imported.rows[0].name
            imported.save()
            assert imported.name == f"CONTA-{marker}CODE-4-2025-001"
            assert frappe.db.get_value("CN Source Row", row_name, "parent") == imported.name
            assert imported.rows[0].amount_usd == 100
            # Same short names share a counter, never overwrite another employer.
            other = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + "OTHER",
                "employer_code": marker + "OTHER", "short_name": marker}).insert()
            second = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": other.name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica", "historical_scope": "Mensual"}).insert()
            assert second.name == marker + "-4-2025-02"
            return {"short_name": "OK", "code_fallback": "OK", "no_mass_rename": "OK",
                    "context_rename": "OK", "child_links": "OK", "collisions": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
