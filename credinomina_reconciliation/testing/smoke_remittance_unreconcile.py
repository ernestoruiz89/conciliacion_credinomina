"""Rollback-only reversal, accounting form save and reconciliation regression."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import unreconcile_remittance


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, month in [("Historica", "2025-04"), ("Operativa", "2026-09")]:
                marker = "FIFO-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                    "client_number": marker, "client_name": "Cliente FIFO"}).insert()
                periods, sources = [], []
                for day, amount in [("15", 100)]:
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                        "payroll_month": month + "-01", "reconciliation_mode": mode, "application_basis": "Cobranza",
                        "collection_cycle": "Primera quincena" if day == "15" else "Segunda quincena"})
                    if mode == "Historica":
                        period.historical_scope = "Fecha exacta"
                        period.historical_application_date = month + "-" + day
                    else:
                        period.append("collection_rows", {"row_key": marker + day, "client": client.name,
                            "client_name": client.client_name, "client_number": marker, "loan_number": marker + "-1",
                            "expected_usd": amount, "deducted_usd": amount, "deduction_status": "Deduccion total"})
                    period.insert()
                    imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                        "source_file": f"/private/files/{marker}-{day}.csv", "status": "Importado", "currency": "USD"})
                    imported.append("rows", {"source_key": marker + day, "event_type": "Aplicacion",
                        "event_date": month + "-" + day, "currency": "USD", "amount": amount,
                        "amount_usd": amount, "effective": 1, "client": client.name,
                        "client_number": marker, "client_name": client.client_name, "loan_number": marker + "-1",
                        "historical_period": period.name if mode == "Historica" else "", "processing_route": mode})
                    imported.insert()
                    periods.append(period)
                    sources.append(imported)
                _reconcile_sources(employer.name)
                for doc in sources + periods:
                    doc.reload()

                def new_deposit(suffix, amount):
                    return frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                        "deposit_reference": marker + suffix, "deposit_date": month + "-30",
                        "deposit_currency": "USD", "deposit_amount": amount})

                fixed = new_deposit("FIXED", 10)
                fixed.append("targets", {"historical_application": sources[0].rows[0].name, "amount_usd": 10}
                    if mode == "Historica" else {"period": periods[0].name, "row_key": marker + "15", "amount_usd": 10})
                fixed.insert(); fixed.submit()
                reconcile_deposit(fixed)
                fixed.reload()
                fixed_state = document_state(fixed)
                deposit = new_deposit("FIFO", 55.05)
                deposit.apply_fifo = 1
                # Reversed selected periods prove that application dates set the order.
                for period in reversed(periods):
                    deposit.append("detail_periods", {"period": period.name})
                deposit.detail_file = deposit.detail_source_file = "/private/files/" + marker + ".csv"
                deposit.detail_hash = marker
                for idx, amount in enumerate((33.03, 22.02), 2):
                    deposit.append("detail_rows", {"source_row": idx, "client": client.name, "client_number": marker,
                        "client_name": client.client_name, "loan_number": marker + "-1", "deducted_usd": amount})
                deposit.insert(); deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload(); fixed.reload()
                assert fixed_state == document_state(fixed), "Another deposit was changed"
                assert deposit.result == "Conciliado", deposit.as_dict()
                assert deposit.allocated_usd == 55.05 and deposit.unallocated_usd == 0
                assert all(row.match_status == "Conciliada" and row.pending_usd == 0 for row in deposit.detail_rows)
                detail = json.loads(deposit.allocation_detail)
                assert [entry["importe_usd"] for entry in detail] == [33.03, 22.02], detail
                assert all(entry["origen"] == "Automática FIFO" and entry["fila_detalle"] for entry in detail)
                assert [entry["aplicaciones_fifo"][0]["event_date"] for entry in detail] == [month + "-15", month + "-15"]
                for period, expected in zip(periods, (65.05,)):
                    period.reload()
                    assert period.remitted_usd == expected, period.as_dict()
                reconcile_deposit(deposit)
                deposit.reload(); fixed.reload()
                assert json.loads(deposit.allocation_detail) == detail, "Reconciliation duplicated or changed cash"
                assert fixed_state == document_state(fixed)

                # Reverse only this deposit, then save the accounting form as
                # a browser does (integer-valued JSON numbers lose their .0).
                unreconcile_remittance(deposit.name, str(deposit.modified), "Ensayo de reversión")
                deposit.reload(); fixed.reload()
                assert deposit.docstatus == 1 and deposit.result == "Pendiente"
                assert deposit.allocated_usd == 0 and deposit.unallocated_usd == 55.05
                assert json.loads(deposit.allocation_detail) == []
                assert fixed_state == document_state(fixed)
                for period, expected in zip(periods, (10, 0)):
                    period.reload()
                    assert period.remitted_usd == expected, period.as_dict()
                for imported in sources:
                    imported.reload()
                    payload = json.loads(frappe.as_json(imported.as_dict()))
                    for row in payload["rows"]:
                        for field in ("historical_remitted_usd", "historical_balance_usd"):
                            value = row.get(field)
                            if value is not None and float(value).is_integer():
                                row[field] = int(value)
                    frappe.get_doc(payload).save()
                assert frappe.get_all("Version", filters={"ref_doctype": deposit.doctype,
                    "docname": deposit.name, "data": ["like", '%"action": "Desconciliar depósito"%']})
                reconcile_deposit(deposit)
                deposit.reload(); fixed.reload()
                assert json.loads(deposit.allocation_detail) == detail
                assert fixed_state == document_state(fixed)

            return {"historical_and_operative": True, "unreconcile_and_save": True,
                    "other_deposit_preserved": True, "reconcile_again": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
