"""Rollback-only single-deposit isolation, shared applications and closed guards."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, month, day in [("Historica", "2025-04-01", "2025-04-30"), ("Operativa", "2026-09-01", "2026-09-30")]:
                marker = "SINGLE-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()

                def new_period(amount, suffix, payday):
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                        "payroll_month": payday[:7] + "-01", "reconciliation_mode": mode, "collection_cycle": "Mensual"})
                    if mode == "Operativa":
                        period.append("collection_rows", {"row_key": marker + suffix, "client_name": "Cliente " + suffix,
                            "client_number": marker + suffix, "loan_number": marker + suffix + "-1", "expected_usd": amount,
                            "deducted_usd": amount, "deduction_status": "Deduccion total"})
                    else:
                        period.historical_scope = "Fecha exacta"
                        period.historical_application_date = payday
                    period.insert()
                    imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                        "source_file": f"/private/files/{marker + suffix}.csv", "status": "Importado", "currency": "USD"})
                    imported.append("rows", {"source_key": marker + suffix, "event_type": "Aplicacion", "event_date": payday,
                        "currency": "USD", "amount": amount, "amount_usd": amount, "effective": 1,
                        "historical_period": period.name if mode == "Historica" else "", "processing_route": mode,
                        "client_number": marker + suffix, "client_name": "Cliente " + suffix, "loan_number": marker + suffix + "-1"})
                    imported.insert()
                    return period, imported

                period, source = new_period(100, "A", day)
                other_period, other_source = new_period(25, "B", "2025-04-15" if mode == "Historica" else "2026-10-15")
                _reconcile_sources(employer.name)
                source.reload(); other_source.reload(); period.reload(); other_period.reload()

                def new_deposit(amount, suffix, target_period, target_source):
                    deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                        "deposit_reference": marker + suffix, "deposit_date": day,
                        "deposit_currency": "USD", "deposit_amount": amount})
                    deposit.append("targets", {"historical_application": target_source.rows[0].name, "amount_usd": amount}
                        if mode == "Historica" else {"period": target_period.name, "row_key": marker + ("A" if target_period.name == period.name else "B"), "amount_usd": amount})
                    deposit.insert(); deposit.submit()
                    return deposit

                first = new_deposit(40, "D1", period, source)
                second = new_deposit(60, "D2", period, source)
                unrelated = new_deposit(25, "D3", other_period, other_source)
                # An unrelated closed period must not participate in the run.
                frappe.db.set_value(other_period.doctype, other_period.name, "status", "Cerrado")
                other_period.reload()
                other_states = [document_state(doc) for doc in (unrelated, other_period, other_source)]
                result = reconcile_deposit(first)
                assert result["scope"] == "deposit" and result["periods"] == [period.name], result
                source.reload(); first.reload(); second.reload(); period.reload()
                assert first.allocated_usd == 40 and second.allocated_usd == 0
                assert period.remitted_usd == 40 and period.applied_usd == 100
                first_state = document_state(first)
                result = reconcile_deposit(second)
                first.reload(); second.reload(); source.reload(); period.reload()
                assert first_state == document_state(first), "Other deposit changed"
                assert second.allocated_usd == 60 and period.remitted_usd == 100
                assert source.rows[0].deposit_match_status == "Depósito conciliado", source.rows[0].as_dict()
                for doc, snapshot in zip((unrelated, other_period, other_source), other_states):
                    doc.reload()
                    assert document_state(doc) == snapshot, ("Unrelated document changed", doc.name)
                # Repeating the selected run preserves amounts and all other cash.
                reconcile_deposit(second)
                first.reload(); second.reload(); period.reload()
                assert document_state(first) == first_state and period.remitted_usd == 100
                # A manual request exceeding the remaining capacity is not booked.
                excessive = new_deposit(60.01, "D4", period, source)
                reconcile_deposit(excessive)
                excessive.reload(); period.reload()
                assert excessive.allocated_usd == 0 and excessive.result == "Revisar destinos", excessive.as_dict()
                assert period.remitted_usd == 100
                first.reload()
                assert first_state == document_state(first)
                frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
                period.reload()
                closed_snapshot = document_state(period)
                try:
                    reconcile_deposit(second)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed period allowed a changed cash distribution")
                period.reload()
                assert document_state(period) == closed_snapshot
            return {"both_modes": True, "shared_application": True, "other_deposits_unchanged": True,
                    "unrelated_closed_period_unchanged": True, "idempotent": True, "overallocation_blocked": True,
                    "affected_closed_period_protected": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
