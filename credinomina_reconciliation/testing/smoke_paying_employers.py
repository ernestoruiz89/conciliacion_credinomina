"""Shared payer, independent debts; real Frappe, isolated site, rollback only."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.remittance_selection import get_pending_targets
from credinomina_reconciliation.application_deposit_detail import preview_application_detail
from credinomina_reconciliation.control_deposits import get_cash_deposits
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute as aging
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "PAYER-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
                "employer_code": marker + suffix, "payroll_frequency": "Mensual"}).insert() for suffix in ("A", "B", "C")]
            payer, beneficiary, outsider = companies
            payer.append("paying_for", {"employer": beneficiary.name})
            payer.save()
            for mode, month in [("Historica", "2025-04-01"), ("Operativa", "2026-09-01")]:
                periods, imports, clients = [], [], []
                for i, company in enumerate((payer, beneficiary)):
                    client = frappe.get_doc({"doctype": "CN Client", "employer": company.name,
                        "client_name": f"Cliente {mode} {i} {marker}", "client_number": f"{marker}-{mode}-{i}"}).insert()
                    clients.append(client)
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name,
                        "payroll_month": month, "reconciliation_mode": mode, "historical_scope": "Mensual",
                        "collection_cycle": "Mensual"})
                    amount = 700 if i == 0 else 500
                    loan = f"{marker}-{mode}-L{i}"
                    if mode == "Operativa":
                        period.append("collection_rows", {"row_key": loan, "client": client.name,
                            "client_name": client.client_name, "client_number": client.client_number, "loan_number": loan,
                            "expected_usd": amount, "deducted_usd": amount, "deduction_status": "Deduccion total"})
                    period.insert()
                    periods.append(period)
                    imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
                        "source_file": f"/private/files/{marker}-{mode}-{i}.csv", "status": "Importado",
                        "historical_backfill": int(mode == "Historica"), "historical_period": period.name if mode == "Historica" else "",
                        "rows": [{"source_row": 2, "source_key": loan, "event_type": "Aplicacion", "event_date": month[:7] + "-23",
                            "employer_text": company.name, "client_name": client.client_name, "client_number": client.client_number,
                            "loan_number": loan, "currency": "USD", "amount": amount, "amount_usd": amount,
                            "processing_route": mode, "historical_period": period.name if mode == "Historica" else "",
                            "reference": loan, "effective": 1}]}).insert()
                    imports.append(imported)
                _reconcile_sources(payer.name)
                for imported in imports:
                    imported.reload()
                    assert imported.rows[0].match_status == "Conciliado", imported.rows[0].as_dict()
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": payer.name,
                    "deposit_reference": marker + mode, "deposit_date": month[:7] + "-28", "deposit_currency": "USD",
                    "deposit_amount": 1000, "detail_file": f"/private/files/{marker}-{mode}-detail.xlsx",
                    "detail_source_file": f"/private/files/{marker}-{mode}-detail.xlsx", "detail_hash": marker + mode,
                    "detail_rows": [{"source_row": i + 2, "client_name": client.client_name,
                        "deducted_usd": 700 if i == 0 else 300} for i, client in enumerate(clients)]}).insert()
                choices = get_pending_targets(deposit.name)
                assert {row["employer"] for row in choices["rows"]} == {payer.name, beneficiary.name}
                if mode == "Historica":
                    deposit.append("targets", {"historical_application": imports[1].rows[0].name,
                        "detail_row": deposit.detail_rows[1].name, "amount_usd": 300})
                    deposit.save()
                    foreign = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": outsider.name,
                        "deposit_reference": marker + "-UNAUTHORIZED", "deposit_date": "2025-04-28",
                        "deposit_currency": "USD", "deposit_amount": 300,
                        "targets": [{"historical_application": imports[1].rows[0].name, "amount_usd": 300}]})
                    try:
                        foreign.insert()
                    except frappe.ValidationError as exc:
                        assert "no autorizada" in str(exc), str(exc)
                    else:
                        raise AssertionError("Accepted an unauthorized beneficiary")
                deposit.submit()
                _reconcile_sources(beneficiary.name)
                deposit.reload()
                assert deposit.result == "Conciliado", (deposit.result, [(r.match_status, r.match_reason) for r in deposit.detail_rows])
                assert deposit.allocated_usd == 1000 and deposit.unallocated_usd == 0
                assert {row.employer for row in deposit.detail_rows} == {payer.name, beneficiary.name}
                for period, paid in zip(periods, (700, 300)):
                    period.reload()
                    assert period.remitted_usd == paid, (period.name, period.remitted_usd)
                rows = aging({"employer": beneficiary.name, "as_of_date": "2026-12-31"})[1]
                assert any(row["period"] == periods[1].name and row["amount_usd"] == 200 for row in rows), rows
                cash = get_cash_deposits(int(month[:4]), payer.name)
                cash = [row for row in cash if row["name"] == deposit.name]
                assert len(cash) == 1 and cash[0]["total_usd"] == 1000
                assert {row["employer"] for row in cash[0]["destinations"]} == {payer.name, beneficiary.name}
                assert not any(row["name"] == deposit.name for row in get_cash_deposits(int(month[:4]), beneficiary.name))
                remainder = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": beneficiary.name,
                    "deposit_reference": marker + mode + "-REST", "deposit_date": month[:7] + "-29", "deposit_currency": "USD",
                    "deposit_amount": 200, "detail_period": periods[1].name}).insert()
                preview = preview_application_detail(remainder.name)
                assert preview["total_usd"] == 200, preview
                targets = get_pending_targets(remainder.name)["rows"]
                assert len(targets) == 1 and targets[0]["pending_cents"] == 20000, targets
                target = targets[0]
                remainder.append("targets", {key: target.get(key) for key in ("period", "row_key", "historical_application") } | {"amount_usd": 200})
                remainder.save()
                remainder.submit()
                _reconcile_sources(beneficiary.name)
                periods[1].reload()
                assert periods[1].remitted_usd == 500
                if mode == "Historica":
                    for period in periods:
                        assert close_period(period.name)["status"] == "Cerrado"
                    _reconcile_sources(beneficiary.name)
                    deposit.reload()
                    assert deposit.allocated_usd == 1000 and deposit.result == "Conciliado"
                # Removing a used authorization cannot invalidate settled cash.
                payer.set("paying_for", [])
                try:
                    payer.save()
                except frappe.ValidationError:
                    payer.reload()
                else:
                    raise AssertionError("Removed a used payer authorization")
            return {"historical_and_operative": "OK", "automatic_names": "OK", "manual_remaining": "OK",
                    "aging": "OK", "cash_once": "OK", "scoped_reconciliation": "OK", "unauthorized_blocked": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
