"""Reimport without a default period unlinks rows and recomputes totals."""
import io
from unittest.mock import patch

import frappe
from openpyxl import Workbook

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api
from credinomina_reconciliation.period_lock import period_write_action


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            for mode, day in (("Historica", "2025-07-15"), ("Operativa", "2026-09-15")):
                marker = "UNLINK-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
                period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                    "payroll_month": day[:7] + "-01", "reconciliation_mode": mode,
                    "collection_cycle": "Mensual", "application_basis": "Cobranza"})
                if mode == "Operativa":
                    period.append("collection_rows", {"client_name": marker, "loan_number": "185654-1",
                        "expected_usd": 100, "deducted_usd": 100, "currency": "USD"})
                period.insert()
                workbook = Workbook()
                sheet = workbook.active
                sheet.append(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                              "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
                sheet.append(["123", day, marker, marker, "NOTA AL PRESTAMO 185654-1 PAGO APLICADO",
                              100, 0, "185654-1", marker, marker, "12", "05"])
                stream = io.BytesIO()
                workbook.save(stream)
                source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                    "currency": "USD", "historical_period": period.name,
                    "source_file": f"/private/files/{marker}.xlsx"}).insert()

                def reload_file():
                    with patch.object(api, "_attached_file", return_value=(
                        frappe._dict(file_name=marker + ".xlsx"), stream.getvalue())):
                        result = api.import_source_file(source.name)
                    source.name = result["import_name"]
                    source.reload()
                    period.reload()

                reload_file()
                assert source.rows[0].collection_period == period.name, source.rows[0].as_dict()
                assert period.applied_usd == 100, period.as_dict()
                row_name = source.rows[0].name
                source.historical_period = ""
                source.save()
                for _ in range(2):
                    reload_file()
                    row = source.rows[0]
                    assert row.name == row_name
                    assert not row.collection_period and not row.historical_period and not row.collection_row_id
                    assert row.application_allocation_detail in (None, "", "[]")
                    assert row.match_status == "Sin coincidencia", row.as_dict()
                    assert period.applied_usd == 0, period.as_dict()
                    assert all(not item.applied_usd for item in period.collection_rows)
                # An explicit assignment remains available after the unlink.
                source.historical_period = period.name
                source.save()
                reload_file()
                assert source.rows[0].collection_period == period.name
                assert period.applied_usd == 100
                # Clearing the header cannot bypass the closed-period guard.
                period.status = "Cerrado"
                with period_write_action("close"):
                    period.save()
                source.historical_period = ""
                try:
                    source.save()
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed-period unlink accepted")
            return {"historical_and_operative": True, "row_ids_preserved": True,
                    "period_totals_cleared": True, "no_immediate_rematch": True,
                    "reassignment": True, "closed_period_protected": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
