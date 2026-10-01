"""Preview decisions survive regrouping, creation and individual CSV reload."""
import csv
import io

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.parsers import SourceFileError
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "choice-" + frappe.generate_hash(length=8)
    try:
        companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
            "employer_code": marker + suffix}).insert().name for suffix in ("A", "B")]
        cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot", "report_date": "2098-05-31",
            "status": "Importado", "source_file": "/private/files/choice-test.xlsx",
            "rows": [{"credit_number": marker + "-1", "client_number_core": marker, "client_name": marker,
                "employer": companies[0], "employer_text": companies[0], "credit_status": "Corriente",
                "validation_status": "Cliente y empresa validados"}]}).insert()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF",
                         "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA", "NO_CREDITO"])
        for i in range(5):
            writer.writerow(["1602", "2025-04-15", 12, 5, marker + str(i), marker, "Descripción completa del asiento",
                10, 0, "Sin alias" if i != 1 else "", marker + "-1" if i == 4 else ""])
        writer.writerow(["1602", "2025-04-15", 1, 1, marker, marker, "Asiento complementario", 5, 0, "Sin alias", ""])
        writer.writerow(["1602", "2025-04-15", 2, 12, marker, marker, "U$15.00 DEPOSITO POR CONVENIO DESCONOCIDO", 0, 15, "Sin alias", ""])
        file = save_file(marker + ".csv", stream.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": file.file_url, "currency": "USD", "portfolio_snapshot": cut.name}
        first = bulk._plan(options)
        assert len(first["groups"]) == 5 and bulk._summary(first)["unidentified_count"] == 6
        options.update(employer_assignments={"2": companies[0], "3": companies[0], "4": companies[1],
                                            "7": companies[1], "8": companies[1]}, assignments_file_hash=first["file_hash"])
        before = frappe.db.count("CN Accounting Import")
        plan = bulk._plan(options)
        assert frappe.db.count("CN Accounting Import") == before
        assert len(plan["groups"]) == 3 and not plan["issues"], plan
        summary = bulk._summary(plan)
        assert summary["sections"]["unidentified"]["rows"] == 1
        assert summary["sections"]["unidentified"]["applications"][0]["description"] == "Descripción completa del asiento"
        assert summary["sections"]["identified"]["rows"] == 4
        assert summary["sections"]["identified"]["application_total_usd"] == 40
        try:
            bulk._plan({**options, "assignments_file_hash": "different-file"})
        except SourceFileError:
            pass
        else:
            raise AssertionError("Choices from another file were accepted")
        created = bulk._create_imports(plan, options)
        imports = [entry for entry in created if not entry.get("doctype")]
        assert len(imports) == 3 and len(created) == 5
        for entry in imports:
            document = frappe.get_doc("CN Accounting Import", entry["name"])
            ids = [row.name for row in document.rows]
            texts = [row.employer_text or "" for row in document.rows]
            if document.employer == companies[1]:
                assert not document.portfolio_snapshot and cut.name in document.notes
            accounting.import_source_file(document.name)
            document.reload()
            assert [row.name for row in document.rows] == ids
            assert [row.employer_text or "" for row in document.rows] == texts
            assert document.employer == entry["employer"]
            if document.employer == companies[0]:
                assert len(document.rows) == 3 and document.total_usd == 30
            if document.employer == companies[1]:
                assert not document.portfolio_snapshot
        return {"regrouped_imports": 3, "manual_decisions": 5, "remaining_unidentified": 1,
                "original_text_and_csv_reimport": "OK", "file_binding": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
