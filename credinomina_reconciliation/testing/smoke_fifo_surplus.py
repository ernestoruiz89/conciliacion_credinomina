"""FIFO settles available applications and leaves the detail's excess pending."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, month in (("Historica", "2025-04"), ("Operativa", "2026-09")):
                marker = "FIFO-EXCESS-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                    "client_number": marker, "client_name": "Cliente FIFO excedente"}).insert()
                periods = []
                for day, amount in (("15", 2), ("30", 7.62)):
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                        "payroll_month": month + "-01", "reconciliation_mode": mode, "application_basis": "Cobranza",
                        "collection_cycle": "Primera quincena" if day == "15" else "Segunda quincena"})
                    if mode == "Historica":
                        period.historical_scope = "Fecha exacta"
                        period.historical_application_date = month + "-" + day
                    else:
                        period.append("collection_rows", {"row_key": marker + day, "client": client.name,
                            "client_name": client.client_name, "client_number": marker, "loan_number": marker + "-1",
                            "application_reference": marker + day,
                            "expected_usd": amount, "deducted_usd": amount, "deduction_status": "Deduccion total"})
                    period.insert()
                    frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                        "source_file": f"/private/files/{marker}-{day}.csv", "status": "Importado", "currency": "USD",
                        "rows": [{"source_key": marker + day, "event_type": "Aplicacion",
                            "event_date": month + "-" + day, "reference": marker + day, "currency": "USD", "amount": amount,
                            "amount_usd": amount, "effective": 1, "client": client.name, "client_number": marker,
                            "client_name": client.client_name, "loan_number": marker + "-1",
                            "historical_period": period.name if mode == "Historica" else "", "processing_route": mode}]}).insert()
                    periods.append(period)
                _reconcile_sources(employer.name)
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                    "deposit_reference": marker, "deposit_date": month + "-30", "deposit_currency": "USD",
                    "deposit_amount": 13.05, "apply_fifo": 1,
                    "detail_periods": [{"period": p.name} for p in reversed(periods)],
                    "detail_file": f"/private/files/{marker}.csv", "detail_source_file": f"/private/files/{marker}.csv",
                    "detail_hash": marker, "detail_rows": [{"source_row": 2, "client": client.name,
                        "client_number": marker, "client_name": client.client_name, "loan_number": marker + "-1",
                        "deducted_usd": 13.05}]}).insert()
                deposit.submit()
                for _attempt in range(2):
                    reconcile_deposit(deposit)
                    deposit.reload()
                    assert deposit.allocated_usd == 9.62, deposit.as_dict()
                    assert deposit.unallocated_usd == 3.43
                    assert deposit.justified_surplus_usd == 0 and deposit.unclassified_usd == 3.43
                    row = deposit.detail_rows[0]
                    assert row.amount_usd == 13.05 and row.linked_usd == 9.62 and row.pending_usd == 3.43
                    assert row.match_status == "Revisar" and deposit.detail_status == "Revisar filas"
                    assert deposit.result != "Conciliado"
                    assert "pendiente de distribuir US$ 3.43" in row.match_reason
                    allocation = json.loads(deposit.allocation_detail)
                    assert [entry["importe_usd"] for entry in allocation] == [2, 7.62]
                    assert all(entry["origen"] == "Automática FIFO" for entry in allocation)
                    assert not frappe.db.exists("CN Complementary Item", {"employer": employer.name})
                for period, expected in zip(periods, (2, 7.62)):
                    period.reload()
                    assert period.remitted_usd == expected
        return {"historical_and_operative": True, "allocated_usd": 9.62, "pending_usd": 3.43,
                "oldest_first": True, "no_automatic_surplus_or_adjustment": True,
                "repeatable": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
