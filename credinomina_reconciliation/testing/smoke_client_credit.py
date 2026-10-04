"""Rollback-only real Frappe workflow for customer-owned deposit excess."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import client_credit as credit
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.control_deposits import get_cash_deposits
from credinomina_reconciliation.deposit_distribution import get_distribution
from credinomina_reconciliation.accounting_control import build_rows, summarize
from credinomina_reconciliation.remittance_selection import get_pending_targets
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import create_complementary_item, _apply_remittance_detail


def must_fail(action):
    frappe.db.savepoint("client_credit_rejection")
    try:
        action()
    except frappe.ValidationError:
        frappe.db.rollback(save_point="client_credit_rejection")
    else:
        raise AssertionError("Unsafe operation accepted")


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            for mode, month, day, deposit_date, included in [
                ("Historica", "2025-04-01", "2025-04-30", "2025-05-10", True),
                ("Historica", "2025-04-01", "2025-04-30", "2025-05-10", False),
                ("Operativa", "2026-09-01", "2026-09-30", "2026-10-01", True),
                ("Operativa", "2026-09-01", "2026-09-30", "2026-10-01", False),
            ]:
                marker = "CLIENT-CREDIT-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
                payer = employer
                if mode == "Historica":
                    payer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + "-PAYER", "employer_code": marker + "-PAYER",
                        "paying_for": [{"employer": employer.name}]}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name, "client_name": "Cliente prueba excedente", "client_number": marker}).insert()
                period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name, "payroll_month": month,
                    "reconciliation_mode": mode, "collection_cycle": "Mensual", "historical_scope": "Fecha exacta", "historical_application_date": day})
                if mode == "Operativa":
                    period.append("collection_rows", {"row_key": marker, "client": client.name, "client_name": client.client_name,
                        "client_number": marker, "loan_number": "987654321-1", "expected_usd": 100, "deducted_usd": 100, "deduction_status": "Deduccion total"})
                period.insert()
                source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name, "currency": "USD",
                    "source_file": f"/private/files/{marker}.csv", "status": "Importado"})
                source.append("rows", {"event_type": "Aplicacion", "event_date": day, "source_key": marker, "client_name": client.client_name,
                    "client_number": marker, "loan_number": "987654321-1", "amount": 100, "amount_usd": 100, "currency": "USD", "effective": 1,
                    "historical_period": period.name if mode == "Historica" else "", "processing_route": mode})
                source.insert()
                _reconcile_sources(employer.name)
                detail_file = f"/private/files/{marker}-detail.csv"
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": payer.name,
                    "deposit_reference": marker, "deposit_date": deposit_date, "deposit_currency": "NIO", "deposit_amount": 4028.67, "fx_rate": 36.6243,
                    "detail_file": detail_file, "detail_source_file": detail_file, "detail_hash": marker, "detail_count": 1,
                    "detail_periods": [{"period": period.name}]})
                deposit.append("detail_rows", {"client": client.name, "client_number": marker, "client_name": client.client_name,
                    "loan_number": "987654321-1", "deducted_nio": 4028.67 if included else 3662.43, "source_row": 2})
                deposit.insert(); deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.result == ("Revisar detalle" if included else "Parcial"), deposit.result
                # Reclassify an imported original; never duplicate its GL evidence.
                item = frappe.get_doc({"doctype": "CN Complementary Item", "category": "Por clasificar", "review_action": "Pendiente de revisión",
                    "employer": employer.name, "posting_date": deposit_date, "currency": "USD", "amount": 10,
                    "accounting_source_key": marker + "-GL", "source_file": source.source_file, "source_row": 3,
                    "source_account": "3004", "source_currency": "NIO", "source_debit": 0, "source_credit": 366.24, "source_fx_rate": 36.6243,
                    "source_date": deposit_date, "source_voucher": marker + "-AS", "voucher": marker + "-AS", "voucher_line": marker + "-GL",
                    "source_description": "Saldo cliente excedente", "description": "Saldo cliente excedente"}).insert()
                item.category = credit.CATEGORY
                item.registered_deposit = deposit.name
                item.credit_client = client.name
                item.credit_detail_row = deposit.detail_rows[0].name if included else ""
                item.credit_assigned_to = "Administrator"
                item.credit_commitment_date = "2026-10-05"
                item.credit_treatment = "Devolución"
                item.save(); item.submit()
                # Calendar year must not hide pending financial/accounting management.
                if mode == "Historica" and included:
                    from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_control_data, get_work_overview
                    current_year = get_control_data(2026, employer.name)
                    assert current_year["work_scope"] == "calendar"
                    assert not any(task.get("target_name") == item.name for task in current_year["work_items"])
                    overview = get_work_overview(employer.name)
                    assert overview["work_scope"] == "Todos"
                    assert any(task.get("target_name") == item.name and task["kind"] == "credit_management" for task in overview["work_items"])
                    assert "periods" not in overview and "year" not in overview, "Work fetch must not replace calendar"
                item.reload(); deposit.reload(); source.reload(); period.reload()
                assert item.result == credit.RESULT
                assert (deposit.allocated_usd, deposit.justified_surplus_usd, deposit.unclassified_usd) == (100, 10, 0), deposit.as_dict()
                assert deposit.result == "Conciliado con saldo a favor del cliente", deposit.result
                row = deposit.detail_rows[0]
                assert (row.amount_usd, row.linked_usd, row.client_credit_usd, row.pending_usd, row.match_status) == (110 if included else 100, 100, 10 if included else 0, 0, "Conciliada"), row.as_dict()
                assert source.rows[0].amount == 100 and period.applied_usd == 100 and period.remitted_usd == 100
                assert (item.credit_resolved_usd, item.credit_pending_usd, item.credit_management_status) == (0, 10, "Pendiente")
                assert all(entry.get("partida") != item.name for entry in json.loads(deposit.allocation_detail))
                assert get_pending_targets(deposit.name)["available_cents"] == 0
                # The full customer position separates cash-covered applications
                # from the credit awaiting an external refund, in both modes.
                from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo.estado_de_cuenta_operativo import execute as customer_position
                _, position, _, _, position_totals = customer_position({"employer": employer.name, "client_number": marker})
                applications = [entry for entry in position if entry["position_type"] == "Aplicación"]
                credits = [entry for entry in position if entry["position_type"] == "Partida complementaria" and entry["source_document"] == item.name]
                assert len(applications) == len(credits) == 1, position
                assert applications[0]["applied_usd"] == applications[0]["remitted_usd"] == 100
                assert applications[0]["applied_pending_usd"] == 0
                assert credits[0]["credit_pending_usd"] == 10
                assert [total["value"] for total in position_totals] == [0, 10]
                overview, = get_cash_deposits(None, deposit_name=deposit.name)
                assert overview["settled"] and not overview["needs_review"]
                assert overview["credits_usd"] == 100 and overview["client_credit_usd"] == 10
                assert sum(part["amount_usd"] for part in overview["destinations"]) == 110
                table = get_distribution(deposit.name)
                assert table["consistent"] and table["pending_usd"] == 0 and table["distributed_usd"] == 110
                assert sum(r["amount_usd"] for r in table["rows"]) == 110
                cash_row = next(r for r in table["rows"] if r["category"] == "Pago a crédito")
                credit_row = next(r for r in table["rows"] if r["category"] == credit.CATEGORY)
                assert cash_row["amount_usd"] == 100 and cash_row["client_number"] == marker
                assert credit_row["amount_usd"] == 10 and credit_row["record_name"] == item.name
                assert credit_row["employer"] == employer.name and credit_row["management_pending_usd"] == 10
                assert not deposit.targets  # A view is not a cash allocation instruction.
                ledger, _ = build_rows([], {}, [item.as_dict()])
                totals = summarize(ledger)
                assert len(ledger) == 1 and totals[0]["credit_nio"] == 366.24 and totals[0]["credit_usd"] == 10
                must_fail(lambda: create_complementary_item(deposit.name, str(deposit.modified), {
                    "category": credit.CATEGORY, "currency": "USD", "amount": 1, "posting_date": deposit_date,
                    "credit_client": client.name, "credit_assigned_to": "Administrator", "credit_commitment_date": "2026-10-05",
                    "credit_treatment": "Devolución", "description": "Duplicate excess"}))
                must_fail(lambda: _apply_remittance_detail(deposit, [], b"x", detail_file))
                deposit.reload(); item.reload()
                # Before management, cancellation safely exposes the unresolved excess.
                frappe.db.savepoint("unmanaged_credit_cancel")
                item.cancel(); deposit.reload()
                assert not deposit.result.startswith("Conciliado") and deposit.justified_surplus_usd == 0
                created = create_complementary_item(deposit.name, str(deposit.modified), {
                    "category": credit.CATEGORY, "currency": "USD", "amount": 10, "posting_date": deposit_date,
                    "credit_client": client.name, "credit_assigned_to": "Administrator", "credit_commitment_date": "2026-10-05",
                    "credit_detail_row": deposit.detail_rows[0].name if included else "",
                    "credit_treatment": "Devolución", "description": "Excedente documentado desde el depósito"})
                replacement = frappe.get_doc("CN Complementary Item", created["name"])
                assert replacement.employer == employer.name
                assert replacement.credit_detail_row == (deposit.detail_rows[0].name if included else "")
                assert created["client_credit"] and created["result"] == credit.RESULT
                deposit.reload()
                assert deposit.result == "Conciliado con saldo a favor del cliente" and not deposit.targets
                assert get_distribution(deposit.name)["detailed_usd"] == 110
                frappe.db.rollback(save_point="unmanaged_credit_cancel")
                item.reload(); deposit.reload()
                distribution = deposit.allocation_detail
                must_fail(lambda: deposit.cancel())
                assert close_period(period.name)["status"] == "Cerrado"
                # Synthetic linked support has no physical file; rollback removes its DB record.
                support = frappe.get_doc({"doctype": "File", "file_name": marker + ".txt", "file_url": "https://example.invalid/" + marker,
                    "attached_to_doctype": item.doctype, "attached_to_name": item.name}).insert()
                item.reload()
                result = credit.record_management(item.name, str(item.modified), "Devolución", 4, deposit_date, marker + "-REFUND1", support.file_url)
                assert result["status"] == "Parcialmente resuelto" and result["pending_usd"] == 6
                item.reload()
                must_fail(lambda: credit.record_management(item.name, str(item.modified), "Devolución", 7, deposit_date, marker + "-BAD", support.file_url))
                item.reload()
                result = credit.record_management(item.name, str(item.modified), "Aplicación futura", 6, deposit_date, marker + "-CORE2", support.file_url)
                item.reload(); deposit.reload(); source.reload(); period.reload()
                assert result["status"] == "Resuelto" and item.credit_pending_usd == 0 and len(json.loads(item.credit_history)) == 2
                table = get_distribution(deposit.name)
                credit_row = next(r for r in table["rows"] if r["category"] == credit.CATEGORY)
                assert credit_row["state"] == "Documentado" and credit_row["management_status"] == "Resuelto" and credit_row["management_pending_usd"] == 0
                assert table["distributed_usd"] == table["detailed_usd"] == 110
                assert deposit.allocation_detail == distribution and period.applied_usd == 100 and source.rows[0].amount == 100
                item.credit_resolved_usd = 0; item.credit_history = "[]"
                item.save(); item.reload()
                assert item.credit_resolved_usd == 10 and len(json.loads(item.credit_history)) == 2
                must_fail(lambda: item.cancel())
                item.reload()
                history_before = json.loads(item.credit_history)
                entry = credit.get_management_history(item.name)["rows"][0]
                reversed_result = credit.reverse_management(item.name, str(item.modified), entry["entry_id"], deposit_date, "Corrección de gestión sintética")
                item.reload(); deposit.reload(); period.reload()
                assert reversed_result["pending_usd"] == 4 and item.credit_resolved_usd == 6
                assert json.loads(item.credit_history)[:2] == history_before
                must_fail(lambda: credit.reverse_management(item.name, str(item.modified), entry["entry_id"], deposit_date, "Repetición"))
                second_entry = next(row for row in credit.get_management_history(item.name)["rows"] if row["can_reverse"])
                credit.reverse_management(item.name, str(item.modified), second_entry["entry_id"], deposit_date, "Corrección de la segunda gestión")
                item.reload(); deposit.reload(); period.reload()
                assert item.credit_resolved_usd == 0 and item.credit_pending_usd == 10
                assert item.credit_management_status == "Pendiente" and len(json.loads(item.credit_history)) == 4
                assert deposit.allocation_detail == distribution and deposit.justified_surplus_usd == 10
                assert period.status == "Cerrado" and period.applied_usd == 100 and period.remitted_usd == 100
                assert deposit.amount_usd - deposit.allocated_usd - deposit.justified_surplus_usd == 0
                must_fail(lambda: get_pending_targets(deposit.name))  # Closed period remains locked.
                must_fail(lambda: item.cancel())
            return {"both_modes": True, "authorized_other_payer": True, "excess_inside_and_outside_detail": True, "NIO_deposit_USD_reconciliation": True, "application": 100, "client_credit": 10,
                    "original_GL_preserved": True, "closed_period_management": True, "partial_and_full_management": True, "append_only_reversal_without_freeing_cash": True,
                    "no_double_use": True, "complete_deposit_distribution": True, "create_credit_from_detail": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
