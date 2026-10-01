"""Unknown companies are independent, editable cases; isolated and rollback-only."""
import csv
import io

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER, ensure_unidentified_employer
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "unknown-" + frappe.generate_hash(length=8)
    try:
        company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker}).insert()
        cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot", "report_date": "2098-04-30",
            "status": "Importado", "source_file": "/private/files/unknown-test.xlsx",
            "rows": [{"credit_number": marker + "-1", "client_number_core": marker,
                      "client_name": marker, "employer": company.name, "employer_text": company.name,
                      "employer_match_status": "Empresa identificada", "credit_status": "Corriente",
                      "validation_status": "Cliente y empresa validados"}]}).insert()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF",
                         "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA", "NO_CREDITO"])
        for index, (label, loan) in enumerate([("Texto desconocido", ""), ("Texto desconocido", ""),
                                             ("", ""), ("Sin alias pero con cartera", marker + "-1")]):
            writer.writerow(["1602", "2025-04-15", 12, 5, marker + str(index), marker,
                             "Aplicación", 25, 0, label, loan])
        file = save_file(marker + ".csv", stream.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": file.file_url, "currency": "USD", "portfolio_snapshot": cut.name}
        count_before = frappe.db.count("CN Employer")
        plan = bulk._plan(options)
        assert frappe.db.count("CN Employer") == count_before, "Preview created a master"
        assert not plan["issues"] and len(plan["groups"]) == 4, plan
        summary = bulk._summary(plan)
        assert summary["unidentified_count"] == 3
        assert summary["sections"]["identified"]["rows"] == 1
        assert summary["sections"]["identified"]["application_total_usd"] == 25
        assert summary["sections"]["unidentified"]["rows"] == 3
        assert summary["sections"]["unidentified"]["application_total_usd"] == 75
        assert len(summary["sections"]["unidentified"]["applications"]) == 3
        created = bulk._create_imports(plan, options)
        documents = [frappe.get_doc("CN Accounting Import", result["name"]) for result in created]
        unknown = [doc for doc in documents if doc.employer == UNIDENTIFIED_EMPLOYER]
        assert len(unknown) == 3 and len({doc.name for doc in unknown}) == 3
        assert all(len(doc.rows) == 1 and doc.total_usd == 25 for doc in unknown)
        assert {doc.rows[0].employer_text or "" for doc in unknown} == {"Texto desconocido", ""}
        assert ensure_unidentified_employer() == UNIDENTIFIED_EMPLOYER
        assert frappe.db.count("CN Employer", {"employer_name": UNIDENTIFIED_EMPLOYER}) == 1
        first = unknown[0]
        row_name = first.rows[0].name
        accounting.import_source_file(first.name)
        first.reload()
        assert first.rows[0].name == row_name and first.rows[0].match_status == "Sin coincidencia"
        assert "Empresa pendiente de identificar" in first.rows[0].match_reason
        first.employer = company.name
        first.save()
        first = frappe.get_doc("CN Accounting Import", first.name)
        assert first.employer == company.name
        # A manually reviewed case stays assigned to its selected company.
        response = accounting.import_source_file(first.name)
        first = frappe.get_doc("CN Accounting Import", response["import_name"])
        assert first.employer == company.name and first.rows[0].name == row_name
        assert all(frappe.db.get_value("CN Accounting Import", doc.name, "employer") == UNIDENTIFIED_EMPLOYER
                   for doc in unknown[1:])
        repeat = bulk._plan(options)
        assert not repeat["groups"] and len(repeat["already_imported"]) == 4
        return {"independent_unknown_imports": 3, "same_date": True, "preview_read_only": True,
                "holding_company_reused": True, "selected_portfolio_priority": True,
                "manual_company_correction": True, "csv_retry_preserves_assignment": True,
                "rolled_back": True}
    finally:
        frappe.db.rollback()
