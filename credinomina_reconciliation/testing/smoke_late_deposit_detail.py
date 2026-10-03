"""Save and import late detail on confirmed deposits without clearing cash; rollback only."""
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.desk.form.save import savedocs

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import (
    import_remittance_detail, reconcile_remittance,
)


RESULT_FIELDS = (
    "allocated_usd", "unallocated_usd", "justified_surplus_usd",
    "unclassified_usd", "allocation_detail", "inherited_exception_comment",
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "LATE-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker}).insert()
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
                "historical_scope": "Fecha exacta", "historical_application_date": "2025-04-30"}).insert()
            source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                "source_file": f"/private/files/{marker}-mov.csv", "status": "Importado", "currency": "USD"})
            for index, amount in enumerate((1000, 445.11)):
                source.append("rows", {"source_key": marker + str(index), "event_type": "Aplicacion",
                    "event_date": "2025-04-30", "currency": "USD", "amount": amount, "amount_usd": amount,
                    "effective": 1, "historical_period": period.name, "processing_route": "Historica",
                    "client_number": marker + str(index), "client_name": f"{marker} Cliente {index}",
                    "loan_number": f"987654{index}-1"})
            source.insert()
            _reconcile_sources(employer.name)
            source.reload()

            deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                "deposit_reference": marker, "deposit_date": "2025-05-10", "deposit_currency": "USD",
                "deposit_amount": 1445.11, "detail_periods": [{"period": period.name}]}).insert()
            deposit.submit()
            reconcile_remittance(deposit.name)
            deposit.reload()
            assert deposit.unallocated_usd == 1445.11

            # Exercise the exact Desk save/submit path with the stale zero in
            # the reported traceback, then exercise a partial cash distribution.
            for allocated, remaining in ((0, 1445.11), (1000, 445.11)):
                if allocated:
                    deposit.append("targets", {"historical_application": source.rows[0].name,
                        "amount_usd": allocated})
                    deposit.save()
                    reconcile_remittance(deposit.name)
                    deposit.reload()
                assert deposit.allocated_usd == allocated and deposit.unallocated_usd == remaining
                recorded = {field: deposit.get(field) for field in RESULT_FIELDS}
                payload = deposit.as_dict()
                payload.update({field: "" if field in ("allocation_detail", "inherited_exception_comment") else 0
                                for field in RESULT_FIELDS})
                payload["detail_file"] = f"/private/files/{marker}-{allocated}.csv"
                payload["result"] = "Conciliado"
                savedocs(frappe.as_json(payload), "Submit")
                deposit.reload()
                assert {field: deposit.get(field) for field in RESULT_FIELDS} == recorded
                assert deposit.result == "Pendiente" and deposit.docstatus == 1

            # A File metadata fixture and mocked content avoid filesystem
            # leftovers. The production importer still parses the actual CSV.
            attachment = frappe.get_doc({"doctype": "File", "name": marker, "file_name": marker + ".csv",
                "file_url": deposit.detail_file, "is_private": 1,
                "attached_to_doctype": deposit.doctype, "attached_to_name": deposit.name})
            attachment.db_insert()
            content = ("Nombre del cliente,Nro. Cliente,Nro. Crédito,Deducido US$\n"
                       f"{marker} Cliente 0,{marker}0,9876540,1000\n"
                       f"{marker} Cliente 1,{marker}1,9876541,445.11\n").encode("utf-8")
            with patch.object(File, "get_content", return_value=content):
                imported = import_remittance_detail(deposit.name)
            deposit.reload()
            assert imported["rows"] == 2 and len(deposit.detail_rows) == 2
            assert {field: deposit.get(field) for field in RESULT_FIELDS} == recorded
            assert deposit.result == "Pendiente"
            assert [row.loan_number for row in deposit.detail_rows] == ["9876540-1", "9876541-1"]
            # The old manual destination needs its explicit link to the new
            # detail row (the same operation as Vincular detalle y destinos).
            deposit.targets[0].detail_row = deposit.detail_rows[0].name
            deposit.save()
            assert {field: deposit.get(field) for field in RESULT_FIELDS} == recorded
            reconcile_remittance(deposit.name)
            deposit.reload()
            assert deposit.result == "Conciliado", deposit.as_dict()
            assert deposit.allocated_usd == 1445.11 and deposit.unallocated_usd == 0
            assert all(row.match_status == "Conciliada" for row in deposit.detail_rows)

            # Ordinary saves cannot forge a result, alter the bank amount, or
            # bypass the existing protection of a closed affected period.
            deposit.result = "Parcial"
            deposit.notes = "Detalle recibido posteriormente"
            deposit.save()
            assert deposit.result == "Conciliado"
            deposit.deposit_amount = 1446
            try:
                deposit.save()
            except frappe.UpdateAfterSubmitError:
                pass
            else:
                raise AssertionError("Confirmed bank amount was editable")
            deposit.reload()
            frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
            deposit.detail_file = f"/private/files/{marker}-closed.csv"
            try:
                deposit.save()
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Closed period accepted a deposit detail change")
            return {"reported_zero_guard_fixed": True, "partial_cash_preserved": True,
                    "late_csv_imported": True, "explicit_reconciliation_updates_balances": True,
                    "bank_amount_locked": True, "closed_period_protected": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
