"""Selected cut wins through preview, persisted grouping and CSV reprocessing."""
import csv
import io

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    token = "EMP-" + frappe.generate_hash(length=8)
    try:
        companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": f"{token}-{suffix}",
            "employer_code": f"{token}-{suffix}", "payroll_frequency": "Mensual",
            "aliases": [{"alias_name": f"Alias {token}"}] if suffix == "A" else []}).insert() for suffix in ("A", "B")]
        cuts = []
        for month, company in ((5, companies[1]), (6, companies[0])):
            cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot", "report_date": f"2097-{month:02d}-28",
                "status": "Importado", "source_file": "/private/files/priority-test.xlsx",
                "rows": [{"credit_number": f"1337{i}-1", "client_number_core": f"{token}-{i}",
                          "client_name": f"Cliente {token}-{i}", "employer": company.name,
                          "employer_text": company.name, "employer_match_status": "Empresa identificada",
                          "credit_status": "Corriente", "validation_status": "Cliente y empresa validados"}
                         for i in (5, 6)]}).insert()
            cuts.append(cut)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                         "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE"])
        for i, label in ((5, "Texto sin alias"), (6, companies[1].name), (7, companies[0].name), (8, f"Alias {token}")):
            writer.writerow(["1602", "2025-04-15", "12", "05", f"{token}-{i}", f"{token}-{i}", "Pago",
                             100, 0, f"1337{i}", label, f"Cliente {token}-{i}"])
        file = save_file(token + ".csv", output.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": file.file_url, "currency": "USD", "portfolio_snapshot": cuts[1].name}
        plan = bulk._plan(options)
        assert not plan["issues"], plan["issues"]
        assert len(plan["groups"]) == 1 and plan["groups"][0]["employer"] == companies[0].name
        assert plan["groups"][0]["count"] == 4
        records = plan["groups"][0]["rows"]
        assert records[0]["portfolio_snapshot_used"] == cuts[1].name
        assert records[0]["employer_text"] == "Texto sin alias"
        assert records[1]["employer_text"] == companies[1].name
        created = bulk._create_imports(plan, options)
        document = frappe.get_doc("CN Accounting Import", created[0]["name"])
        assert document.employer == companies[0].name and document.total_usd == 400
        accounting.import_source_file(document.name)
        document.reload()
        assert document.employer == companies[0].name and document.total_usd == 400
        assert all(row.portfolio_employer == companies[0].name for row in document.rows[:2])
        assert accounting._company_imports([document], companies[0].name) == [document]
        return {"selected_snapshot": "OK", "portfolio_before_text": "OK", "name_and_alias_fallback": "OK",
                "individual_csv_reprocessing": "OK", "company_reconciliation_scope": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
