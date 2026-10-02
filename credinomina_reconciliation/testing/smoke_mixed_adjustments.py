"""Rollback-only mixed settlement and cancellation tests for both modes."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_adjustments import confirm_adjustment, MIXED_STATUS
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, month, day in [("Historica", "2025-04-01", "2025-04-30"), ("Operativa", "2026-09-01", "2026-09-30")]:
                marker = "MIX-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype":"CN Employer", "employer_name":marker, "employer_code":marker}).insert()
                period = frappe.get_doc({"doctype":"CN Reconciliation Period", "employer":employer.name,
                    "payroll_month":month, "reconciliation_mode":mode, "collection_cycle":"Mensual"})
                if mode == "Operativa":
                    period.append("collection_rows", {"row_key":marker, "client_name":"Cliente prueba", "client_number":marker,
                        "loan_number":marker+"-1", "expected_usd":137.33, "deducted_usd":137.33, "deduction_status":"Deduccion total"})
                period.insert()
                source = frappe.get_doc({"doctype":"CN Accounting Import", "employer":employer.name,
                    "source_file":f"/private/files/{marker}.csv", "status":"Importado", "currency":"USD"})
                source.append("rows", {"event_type":"Aplicacion", "event_date":day, "source_key":marker,
                    "currency":"USD", "amount":137.33, "amount_usd":137.33, "effective":1,
                    "historical_period":period.name if mode == "Historica" else "", "processing_route":mode,
                    "client_number":marker, "client_name":"Cliente prueba", "loan_number":marker+"-1"})
                source.insert()
                _reconcile_sources(employer.name)
                source.reload()
                deposit = frappe.get_doc({"doctype":"CN Remittance Allocation", "employer":employer.name,
                    "deposit_reference":marker, "deposit_date":day, "deposit_currency":"USD", "deposit_amount":111.32})
                deposit.append("targets", {"historical_application":source.rows[0].name, "amount_usd":111.32}
                    if mode == "Historica" else {"period":period.name, "row_key":marker, "amount_usd":111.32})
                deposit.insert()
                deposit.submit()
                _reconcile_sources(employer.name)
                deposit.reload()
                original_distribution = deposit.allocation_detail
                def new_adjustment(amount):
                    return frappe.get_doc({"doctype":"CN Complementary Item", "employer":employer.name,
                        "review_action":"Ajuste de aplicación", "category":"Ajuste de aplicación", "posting_date":day,
                        "currency":"USD", "amount":amount, "application_adjustment_usd":amount,
                        "related_application":source.rows[0].name, "description":"NC de prueba",
                        "review_notes":"Cancelación documentada del saldo pendiente"}).insert()
                excessive = new_adjustment(26.02)
                try:
                    confirm_adjustment(excessive.name)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Adjustment consumed assigned cash")
                adjustment = new_adjustment(26.01)
                confirm_adjustment(adjustment.name)
                reconcile_deposit(deposit)
                source.reload(); deposit.reload(); period.reload()
                assert source.rows[0].amount == 137.33 and source.rows[0].net_applied_usd == 111.32
                assert source.rows[0].deposit_match_status == MIXED_STATUS, source.rows[0].as_dict()
                assert deposit.allocated_usd == 111.32 and deposit.allocation_detail == original_distribution
                assert period.applied_usd == 111.32 and period.remitted_usd == 111.32
                if mode == "Operativa":
                    assert period.deducted_usd == 137.33, "Adjustment must not rewrite payroll evidence"
                adjustment.reload(); adjustment.cancel()
                source.reload(); deposit.reload()
                assert source.rows[0].net_applied_usd == 137.33
                assert source.rows[0].deposit_match_status != MIXED_STATUS
                assert deposit.allocation_detail == original_distribution
                # A closed period still blocks confirmation with partial cash.
                frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
                blocked = new_adjustment(26.01)
                try:
                    confirm_adjustment(blocked.name)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed period accepted mixed adjustment")
            return {"historical_and_operative_mixed":True, "original":137.33, "deposit":111.32,
                    "adjustment":26.01, "overadjustment_blocked":True, "cash_preserved_on_cancel":True,
                    "closed_period_protected":True, "rolled_back":True}
    finally:
        frappe.db.rollback()
