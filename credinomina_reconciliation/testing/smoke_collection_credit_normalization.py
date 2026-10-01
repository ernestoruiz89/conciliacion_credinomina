"""Exercise both Excel imports and stable row identities; roll back test data."""

import io
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from openpyxl import Workbook

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_module,
)
from credinomina_reconciliation.parsers import source_key
from credinomina_reconciliation.reconciliation import application_matches_collection


def _workbook(detail=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Nro. Cliente", "Nombre y Apellidos del Cliente", "Nro. Crédito",
                  "Nro. cuota", "Deducido US$" if detail else "Monto de la cuota en US$"])
    sheet.append(["NORM-1", "Cliente de prueba uno", 13375, 1, 50])
    sheet.append(["NORM-2", "Cliente de prueba dos", "13376-2", 1, 40])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "credit-norm-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                  "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2026-09-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual", "collection_file": "collection.xlsx"}).insert()
        files = {"collection.xlsx": _workbook(), "response.xlsx": _workbook(detail=True)}

        def attached(document, url):
            return SimpleNamespace(file_name=url), files[url]

        with patch.object(period_module, "_attached_file", side_effect=attached), \
             patch.object(period_module, "_reconcile_if_sources", return_value=None):
            period_module.import_collection(period.name)
            period.reload()
            assert [row.loan_number for row in period.collection_rows] == ["13375-1", "13376-2"]
            child_ids = [row.name for row in period.collection_rows]
            assert application_matches_collection(
                {"loan_number": "13375-1", "client_number": "NORM-1"}, period.collection_rows[0].as_dict(),
            )

            # Reproduce an old import of the very same file, already linked to cash.
            row = period.collection_rows[0]
            old_key = source_key(employer.name, "2026-09-01", "Mensual", "NORM-1", "13375", "1")[:24]
            frappe.db.set_value("CN Collection Row", row.name, {
                "loan_number": "13375", "row_key": old_key, "applied_usd": 50, "remitted_usd": 25,
            })
            result = period_module.import_collection(period.name)
            assert not result.get("unchanged")
            period.reload()
            assert [row.name for row in period.collection_rows] == child_ids
            row = period.collection_rows[0]
            assert (row.loan_number, row.row_key, row.applied_usd, row.remitted_usd) == ("13375-1", old_key, 50, 25)
            assert period_module.import_collection(period.name)["unchanged"]

            # Employer detail also normalizes an older collection while keeping its keys.
            period.employer_response_file = "response.xlsx"
            period.deduction_evidence_date = "2026-09-30"
            period.save()
            frappe.db.set_value("CN Collection Row", row.name, "loan_number", "13375")
            period_module.import_employer_response(period.name)
            period.reload()
            assert [row.loan_number for row in period.collection_rows] == ["13375-1", "13376-2"]
            assert [row.deducted_usd for row in period.collection_rows] == [50, 40]
            assert [row.name for row in period.collection_rows] == child_ids
            assert period.collection_rows[0].row_key == old_key
            assert not frappe.db.count("CN Reconciliation Exception", {"period": period.name})
            assert period_module.import_employer_response(period.name)["unchanged"]

            # Correcting a file later must reuse its original row IDs and keys too.
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["Nro. Cliente", "Nombre y Apellidos del Cliente", "Nro. Crédito",
                          "Nro. cuota", "Monto de la cuota en US$"])
            sheet.append(["NORM-1", "Cliente de prueba uno", "13375-1", 1, 50])
            sheet.append(["NORM-2", "Cliente de prueba dos", "13376-2", 1, 40])
            stream = io.BytesIO()
            workbook.save(stream)
            files["collection.xlsx"] = stream.getvalue()
            period_module.import_collection(period.name)
            period.reload()
            assert [row.name for row in period.collection_rows] == child_ids
            assert period.collection_rows[0].row_key == old_key

            period.status = "Cerrado"
            frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
            for importer in (period_module.import_collection, period_module.import_employer_response):
                try:
                    importer(period.name)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Un período cerrado no debe permitir normalizar/importar")
        return {"collection": "OK", "employer_detail": "OK", "suffix_preserved": "OK",
                "same_file_reimport": "OK", "row_ids_and_cash": "OK", "closed_period": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
