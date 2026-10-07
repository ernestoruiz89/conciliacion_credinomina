"""Normal save preserves imported rows; only the import action replaces them."""
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as portfolio


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "HEADER-" + frappe.generate_hash(length=8)
    try:
        cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot",
            "source_file": f"/private/files/{marker}.xlsx", "report_date": "2093-11-30", "status": "Importado",
            "rows": [{"credit_number": f"{marker}-{i}", "credit_status": "VIGENTE"} for i in range(1000)]}).insert()
        before = frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
        original = frappe.db.get_value(cut.doctype, cut.name, ["file_hash", "row_count", "status"], as_dict=True)
        cut.notes = "Solo notas"
        cut.source_file = f"/private/files/{marker}-nuevo.xlsx"
        # Read-only payload edits cannot overwrite imported data through Save.
        cut.row_count = 1
        cut.file_hash = "forged"
        cut.rows[0].credit_number = "forged"
        cut.rows.pop()
        started = perf_counter()
        with patch.object(portfolio.CNCreditPortfolioSnapshot, "recalculate_summary", side_effect=AssertionError("Save recalculated credits")), \
             patch.object(portfolio, "parse_credit_portfolio", side_effect=AssertionError("Save imported a file")), \
             patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            cut.save()
        elapsed = perf_counter() - started
        assert not any("tabCN Credit Portfolio Row" in str(call.args[0]) for call in sql.call_args_list)
        assert original == frappe.db.get_value(cut.doctype, cut.name, list(original), as_dict=True)
        assert before == frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
        cut.reload()
        assert cut.notes == "Solo notas" and cut.source_file.endswith("-nuevo.xlsx")
        version = frappe.get_last_doc("Version", filters={"ref_doctype": cut.doctype, "docname": cut.name})
        assert {item[0] for item in frappe.parse_json(version.data)["changed"]} == {"notes", "source_file"}
        # The existing import endpoint still replaces the rows and calculates summary.
        records = [{"source_row": i + 2, "report_date": "2093-11-30", "credit_number": f"{marker}-NEW-{i}-1",
                    "client_name": "Prueba", "is_convenio": "No", "credit_status": "Corriente"} for i in range(25)]
        with patch.object(portfolio, "_attached_file", return_value=(frappe._dict(file_name="test.xlsx"), marker.encode())), \
             patch.object(portfolio, "parse_credit_portfolio", return_value=records):
            imported = portfolio.import_portfolio_snapshot(cut.name)
        cut = frappe.get_doc(cut.doctype, imported["snapshot_name"])
        assert cut.row_count == 25 and len(cut.rows) == 25
        assert cut.rows[0].credit_number == f"{marker}-NEW-0-1"
        # Import permission is context-local and must not leak into the next save.
        with patch.object(portfolio.CNCreditPortfolioSnapshot, "recalculate_summary", side_effect=AssertionError("Import context leaked")):
            cut.notes = "Después de importar"
            cut.save()
        cut.source_file = ""
        try:
            cut.save()
        except frappe.MandatoryError:
            pass
        else:
            raise AssertionError("Header save accepted a missing required file")
        cut.reload()
        frappe.db.set_value(cut.doctype, cut.name, "docstatus", 1, update_modified=False)
        cut.reload()
        cut.notes = "No permitido después de confirmar"
        try:
            cut.save()
        except frappe.UpdateAfterSubmitError:
            pass
        else:
            raise AssertionError("Header save bypassed allow_on_submit")
        return {"normal_save_seconds": round(elapsed, 3), "preserved_rows": 1000,
                "normal_save_child_queries": 0, "header_audited": True,
                "button_imported_rows": cut.row_count, "rolled_back": True}
    finally:
        frappe.db.rollback()
