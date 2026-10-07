"""Rollback-only exact multi-application matching and legacy selection migration."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.application_deposit_detail import preview_application_detail
from credinomina_reconciliation.period_pending import get_period_pending
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_control_data
from credinomina_reconciliation.patches.v1_0.migrate_remittance_detail_periods import execute as migrate_periods
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, dates in [("Historica", ["2025-04-15", "2025-04-30", "2025-05-15"]),
                                ("Operativa", ["2026-09-15", "2026-09-30", "2026-10-15"])]:
                marker = "MULTIPER-" + frappe.generate_hash(length=8)
                company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
                frappe.get_doc({"doctype": "CN Client", "employer": company.name,
                    "client_number": marker, "client_name": "Juan de prueba"}).insert()
                periods, sources = [], []
                for index, (date, amount) in enumerate(zip(dates, [50.25, 60.26, 17])):
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name,
                        "payroll_month": date[:7] + "-01", "reconciliation_mode": mode,
                        "collection_cycle": "Primera quincena" if index != 1 else "Segunda quincena"})
                    if mode == "Historica":
                        period.historical_scope = "Fecha exacta"
                        period.historical_application_date = date
                    else:
                        period.application_basis = "Cobranza"
                        period.append("collection_rows", {"row_key": marker + str(index),
                            "application_reference": marker + str(index),
                            "client_name": "Juan de prueba", "client_number": marker, "loan_number": marker + "-1",
                            "expected_usd": amount, "deducted_usd": amount, "deduction_status": "Deduccion total"})
                    period.insert()
                    source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
                        "source_file": f"/private/files/{marker}-{index}.csv", "status": "Importado", "currency": "USD"})
                    source.append("rows", {"source_key": marker + str(index), "event_type": "Aplicacion",
                        "reference": marker + str(index),
                        "event_date": date, "currency": "USD", "amount": amount, "amount_usd": amount,
                        "effective": 1, "historical_period": period.name if mode == "Historica" else "",
                        "processing_route": mode, "client_name": "Juan de prueba", "client_number": marker,
                        "loan_number": marker + "-1"})
                    source.insert()
                    periods.append(period); sources.append(source)
                _reconcile_sources(company.name)
                for source in sources:
                    source.reload()
                other = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company.name,
                    "deposit_reference": marker + "-OTHER", "deposit_date": dates[2],
                    "deposit_currency": "USD", "deposit_amount": 17}).insert()
                other.submit()
                untouched = document_state(other)
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company.name,
                    "deposit_reference": marker + "-DEP", "deposit_date": dates[2],
                    "deposit_currency": "USD", "deposit_amount": 110.51,
                    "detail_periods": [{"period": periods[0].name}],
                    "detail_file": f"/private/files/{marker}.xlsx", "detail_source_file": f"/private/files/{marker}.xlsx",
                    "detail_hash": marker, "detail_count": 1})
                deposit.append("detail_rows", {"source_row": 2, "client_name": "Juan de prueba",
                    "client_number": marker, "loan_number": marker + "-1", "deducted_usd": 110.51})
                deposit.insert(); deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.allocated_usd == 0, "Matched an application outside the selected period"
                issues = get_period_pending(periods[0].name, kind="Depósito")
                assert any(row["source"] == deposit.name for row in issues["rows"]), issues
                control = get_control_data(year=int(dates[0][:4]), employer=company.name)
                assert any(row.get("target_name") == deposit.name for row in control["work_items"]), control["work_items"]
                deposit.append("detail_periods", {"period": periods[1].name})
                deposit.save()
                preview = preview_application_detail(deposit.name)
                assert preview["total_usd"] == 110.51 and len(preview["rows"]) == 2, preview
                assert set(preview["periods"]) == {period.name for period in periods[:2]}
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.result == "Conciliado", deposit.as_dict()
                assert deposit.allocated_usd == 110.51 and deposit.unclassified_usd == 0
                assert deposit.applied_usd == 110.51
                assert deposit.detail_rows[0].match_status == "Conciliada"
                matches = json.loads(deposit.detail_rows[0].matched_targets)
                assert sorted(match["amount_usd"] for match in matches) == [50.25, 60.26], matches
                for period, source, expected in zip(periods, sources, [50.25, 60.26, 0]):
                    period.reload(); source.reload()
                    assert period.remitted_usd == expected, period.as_dict()
                reconcile_deposit(deposit)
                deposit.reload(); other.reload()
                assert deposit.allocated_usd == 110.51 and document_state(other) == untouched
                # Keep one automatic period, explicitly authorize manual links
                # to the other, and verify both reconciliation entry points.
                deposit.set("detail_periods", [{"period": periods[0].name}])
                for index, amount in enumerate((50.25, 60.26)):
                    target = {"historical_application": sources[index].rows[0].name} if mode == "Historica" else {
                        "period": periods[index].name, "row_key": marker + str(index)}
                    deposit.append("targets", target | {"amount_usd": amount, "detail_row": deposit.detail_rows[0].name})
                deposit.save()
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.detail_rows[0].match_status == "Revisar"
                assert "no pertenece a los períodos" in deposit.detail_rows[0].match_reason
                deposit.allow_manual_other_periods = 1
                deposit.save()
                assert deposit.result == "Pendiente"
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.result == "Conciliado", deposit.result
                assert deposit.allocated_usd == 110.51
                assert "otros períodos: " + periods[1].name in deposit.detail_rows[0].match_reason
                _reconcile_sources(company.name)
                deposit.reload()
                assert deposit.result == "Conciliado", deposit.result
                assert deposit.allocated_usd == 110.51
                deposit.allow_manual_other_periods = 0
                deposit.save()
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.detail_rows[0].match_status == "Revisar"
                deposit.allow_manual_other_periods = 1
                deposit.save()
                frappe.db.set_value(periods[1].doctype, periods[1].name, "status", "Cerrado")
                try:
                    reconcile_deposit(deposit)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Manual opt-in accepted a closed external period")
                # An existing, confirmed record linked to a now closed period is
                # migrated without calling validation or changing its balances.
                frappe.db.set_value(periods[0].doctype, periods[0].name, "status", "Cerrado")
                if frappe.db.has_column("CN Remittance Allocation", "detail_period"):
                    frappe.db.sql("UPDATE `tabCN Remittance Allocation` SET detail_period=%s WHERE name=%s",
                                  (periods[0].name, other.name))
                    old_modified = other.modified
                    migrate_periods(); migrate_periods()
                    other.reload()
                    assert other.modified == old_modified and other.docstatus == 1 and other.allocated_usd == 0
                    assert len(other.detail_periods) == 1 and other.detail_periods[0].period == periods[0].name
                    assert other.detail_periods[0].docstatus == 1
                    assert not frappe.db.get_value(other.doctype, other.name, "detail_period")
                try:
                    reconcile_deposit(deposit)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed selected period accepted reconciliation")
            return {"both_modes": True, "exact_sum": "50.25 + 60.26 = 110.51", "period_scope": True,
                    "manual_other_periods_opt_in": True, "company_and_deposit_reconciliation": True,
                    "other_deposit_unchanged": True, "idempotent": True,
                    "migration_preserves_confirmed_and_closed_records": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
