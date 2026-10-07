"""Persist aliases and reidentify a bulk preview without reconciling history."""
import csv
import io
from unittest.mock import patch

import frappe

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_employer.cn_employer import add_employer_alias
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "ALIAS-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker}).insert()
            other = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + "-B",
                "employer_code": marker + "-B"}).insert()
            # Existing accounting must not turn an alias edit into reconciliation.
            frappe.get_doc({"doctype": "CN Accounting Import", "name": marker,
                "employer": employer.name, "status": "Importado"}).db_insert()
            cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot", "report_date": "2097-12-31",
                "status": "Importado", "source_file": "/private/files/alias-test.xlsx",
                "rows": [{"credit_number": marker + "-1", "employer": employer.name,
                    "credit_status": "VIGENTE"}]}).insert()
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF",
                "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA", "NO_CREDITO"])
            for i in range(3):
                writer.writerow(["1602", "2025-07-15", 12, 5, marker + str(i), marker,
                    "Pago aplicado", 10, 0, marker + " alternativo", ""])
            content = stream.getvalue().encode()
            options = {"source_file": "/private/files/alias-test.csv", "currency": "USD", "portfolio_snapshot": cut.name}
            with patch.object(bulk, "_file", return_value=(frappe._dict(file_name="alias-test.csv"), content)):
                before = bulk._summary(bulk._plan(options))
                assert before["sections"]["unidentified"]["rows"] == 3
                count = frappe.db.count("CN Accounting Import")
                with patch.object(accounting, "_reconcile_sources", side_effect=AssertionError("Alias save reconciled history")):
                    assert add_employer_alias(employer.name, marker + " alternativo")["added"]
                    assert not add_employer_alias(employer.name, marker.lower() + "   ALTERNATIVO")["added"]
                    employer.reload()
                    assert len(employer.aliases) == 1
                    employer.append("aliases", {"alias_name": marker + " otro"})
                    employer.save()  # Also verify the normal company form path.
                after = bulk._summary(bulk._plan(options))
                assert after["sections"]["unidentified"]["rows"] == 0
                assert after["sections"]["identified"]["rows"] == 3
                assert after["sections"]["identified"]["group_count"] == 1
                assert frappe.db.count("CN Accounting Import") == count
                try:
                    add_employer_alias(other.name, marker + " alternativo")
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Alias conflict accepted")
            return {"rows_reidentified": 3, "grouped_imports": 1, "no_imports_created": True,
                    "normal_and_modal_save_without_reconciliation": True,
                    "idempotent_and_conflicts_checked": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
