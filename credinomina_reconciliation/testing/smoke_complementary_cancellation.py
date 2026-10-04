"""Rollback-only cancellation isolation using real documents and engine writes."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, date in [("Historica", "2025-04-30"), ("Operativa", "2026-09-30")]:
                marker = "CANCEL-" + frappe.generate_hash(length=8)
                companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
                    "employer_code": marker + suffix}).insert() for suffix in ("A", "B")]

                def new_period(company, suffix, amount, deducted=None, payday=date):
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name,
                        "payroll_month": payday[:7] + "-01", "reconciliation_mode": mode, "collection_cycle": "Mensual"})
                    if mode == "Historica":
                        period.historical_scope, period.historical_application_date = "Fecha exacta", payday
                    else:
                        period.append("collection_rows", {"row_key": marker + suffix, "client_name": "Cliente " + suffix,
                            "client_number": marker + suffix, "loan_number": marker + suffix + "-1", "expected_usd": deducted or amount,
                            "deducted_usd": deducted or amount, "deduction_status": "Deduccion total"})
                    period.insert()
                    imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
                        "source_file": f"/private/files/{marker + suffix}.csv", "status": "Importado", "currency": "USD"})
                    imported.append("rows", {"source_key": marker + suffix, "event_type": "Aplicacion", "event_date": payday,
                        "currency": "USD", "amount": amount, "amount_usd": amount, "effective": 1,
                        "historical_period": period.name if mode == "Historica" else "", "processing_route": mode,
                        "client_number": marker + suffix, "client_name": "Cliente " + suffix, "loan_number": marker + suffix + "-1"})
                    imported.insert()
                    return period, imported

                period, source = new_period(companies[0], "A", 100, 110)
                other_period, other_source = new_period(companies[0], "OTHER", 25, payday="2025-05-15" if mode == "Historica" else "2026-10-15")
                foreign_period, foreign_source = new_period(companies[1], "FOREIGN", 25)
                _reconcile_sources(companies[0].name); _reconcile_sources(companies[1].name)
                source.reload(); other_source.reload(); foreign_source.reload()

                def new_item(amount, suffix, owner=companies[0], target_period=None, generic=False):
                    item = frappe.get_doc({"doctype": "CN Complementary Item", "employer": owner.name,
                        "category": "Cobranza administrativa", "reference": marker + suffix, "posting_date": date,
                        "currency": "USD", "amount": amount, "description": "Partida de prueba",
                        "period": target_period.name if target_period else "", "generic_distribution": int(generic)})
                    if generic:
                        item.append("distribution_companies", {"employer": companies[1].name})
                    elif target_period and mode == "Operativa":
                        item.loan_number = target_period.collection_rows[0].loan_number
                        item.client_number = target_period.collection_rows[0].client_number
                    item.insert(); item.flags.defer_reconciliation = True; item.submit()
                    return item

                def deposit(amount, suffix, target_period=None, imported=None, item=None, portion=0, owner=companies[0]):
                    document = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": owner.name,
                        "deposit_reference": marker + suffix, "deposit_date": date,
                        "deposit_currency": "USD", "deposit_amount": amount})
                    if imported:
                        document.append("detail_periods", {"period": target_period.name})
                        document.append("targets", {"historical_application": imported.rows[0].name, "amount_usd": amount - portion}
                            if mode == "Historica" else {"period": target_period.name,
                                "row_key": target_period.collection_rows[0].row_key, "amount_usd": amount - portion})
                    if item:
                        document.append("targets", {"complementary_item": item.name, "amount_usd": portion})
                    document.insert(); document.submit(); reconcile_deposit(document); document.reload()
                    return document

                complement = new_item(10, "-COMP", target_period=period)
                first = deposit(45, "-D1", period, source, complement, 5)
                second = deposit(65, "-D2", period, source, complement, 5)
                unrelated = deposit(25, "-OTHER", other_period, other_source)
                foreign = deposit(25, "-FOREIGN", foreign_period, foreign_source, owner=companies[1])
                frappe.db.set_value(other_period.doctype, other_period.name, "status", "Cerrado")
                for document in (other_period, other_source, foreign_period, foreign_source):
                    document.reload()
                protected = [(document, document_state(document)) for document in
                             (unrelated, other_period, other_source, foreign, foreign_period, foreign_source)]
                # A cancellation with any affected closed period must be rejected
                # before changing the item or releasing its assigned bank cash.
                period.reload(); open_status = period.status
                frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
                bank_states = [document_state(document) for document in (first, second)]
                try:
                    complement.reload(); complement.cancel()
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed period allowed complementary cancellation")
                assert frappe.db.get_value(complement.doctype, complement.name, "docstatus") == 1
                for document, snapshot in zip((first, second), bank_states):
                    assert document_state(document.reload()) == snapshot
                frappe.db.set_value(period.doctype, period.name, "status", open_status)
                # Global and even company-wide engines are forbidden in cancel.
                with patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import._reconcile_sources",
                           side_effect=AssertionError("Cancellation called the company/global engine")):
                    complement.reload(); complement.cancel()
                first.reload(); second.reload(); source.reload(); period.reload()
                assert first.allocated_usd == 40 and second.allocated_usd == 60, (first.as_dict(), second.as_dict())
                assert first.unclassified_usd == second.unclassified_usd == 5
                assert all(document.result == "Revisar destinos" for document in (first, second))
                assert all(document.targets[-1].complementary_item == complement.name for document in (first, second)), "Lost target audit"
                assert period.remitted_usd == 100 and period.applied_usd == 100
                assert set(complement.flags.cancellation_result["deposits"]) == {first.name, second.name}
                assert complement.flags.cancellation_result["periods"] == [period.name]
                for document, snapshot in protected:
                    document.reload()
                    assert document_state(document) == snapshot, ("Unrelated document changed", document.name)

                # Another deposit covering the SAME application is frozen unless
                # it actually uses the canceled complement.
                shared_period, shared_source = new_period(companies[0], "SAME-CLAIM", 100, 110,
                    payday="2025-07-15" if mode == "Historica" else "2026-12-15")
                _reconcile_sources(companies[0].name); shared_source.reload()
                fee = new_item(10, "-FEE", target_period=shared_period)
                fee_cash = deposit(70, "-FEE-CASH", shared_period, shared_source, fee, 10)
                fixed_cash = deposit(40, "-FIXED-CASH", shared_period, shared_source)
                fixed_snapshot = document_state(fixed_cash)
                fee.reload(); fee.cancel()
                fee_cash.reload(); fixed_cash.reload(); shared_period.reload()
                assert fee_cash.allocated_usd == 60 and fee_cash.unclassified_usd == 10
                assert document_state(fixed_cash) == fixed_snapshot
                assert shared_period.remitted_usd == 100

                # The automatic allocation JSON is sufficient even if no
                # CN Remittance Target points to the complement.
                automatic_period, automatic_source = new_period(companies[0], "AUTO", 100, 110,
                    payday="2025-08-15" if mode == "Historica" else "2027-01-15")
                _reconcile_sources(companies[0].name); automatic_source.reload()
                automatic = new_item(10, "-AUTO-CASH", target_period=automatic_period)
                automatic_cash = deposit(110, "-AUTO-CASH", automatic_period, automatic_source,
                    item=automatic, portion=10)
                # Seed the stored-evidence case with no planned target. Only
                # rollback-only fixture children are removed, never real data.
                frappe.db.delete("CN Remittance Target", {"name": automatic_cash.targets[-1].name})
                automatic_cash.reload()
                assert automatic.name in automatic_cash.allocation_detail, automatic_cash.as_dict()
                assert not any(target.complementary_item == automatic.name for target in automatic_cash.targets)
                automatic.reload(); automatic.cancel()
                automatic_cash.reload()
                assert automatic_cash.allocated_usd == 100 and automatic_cash.unclassified_usd == 10
                assert automatic.flags.cancellation_result["deposits"] == [automatic_cash.name]

                credit_period, credit_source = new_period(companies[0], "CREDIT", 100,
                    payday="2025-09-15" if mode == "Historica" else "2027-02-15")
                _reconcile_sources(companies[0].name); credit_source.reload()
                credit_cash = deposit(110, "-CREDIT-CASH", credit_period, credit_source, portion=10)
                credit = frappe.get_doc({"doctype": "CN Complementary Item", "category": "Saldo a favor de la empresa",
                    "reference": credit_cash.deposit_reference,
                    "employer": companies[0].name, "period": credit_period.name, "registered_deposit": credit_cash.name,
                    "posting_date": date, "currency": "USD", "amount": 10, "reason_type": "Error de la empresa",
                    "credit_assigned_to": "Administrator", "credit_commitment_date": date, "credit_treatment": "Pendiente de decisión",
                    "description": "Saldo de prueba"}).insert()
                credit.submit(); credit_cash.reload()
                allocation_before = credit_cash.allocation_detail
                assert credit_cash.justified_surplus_usd == 10 and credit_cash.unclassified_usd == 0
                credit.reload(); credit.cancel(); credit_cash.reload(); credit_period.reload()
                assert credit_cash.allocation_detail == allocation_before
                assert credit_cash.allocated_usd == 100 and credit_cash.unclassified_usd == 10 and credit_cash.justified_surplus_usd == 0
                assert credit_period.unassigned_deposit_usd == 10

                negative_period, negative_source = new_period(companies[0], "NEGATIVE", 100, 90,
                    payday="2025-10-15" if mode == "Historica" else "2027-03-15")
                negative = new_item(-10, "-NEGATIVE", target_period=negative_period)
                _reconcile_sources(companies[0].name); negative_source.reload()
                negative_cash = deposit(90, "-NEGATIVE-CASH", negative_period, negative_source, negative, -10)
                assert negative_cash.allocated_usd == 90
                stable_cash = document_state(fixed_cash.reload())
                negative.reload(); negative.cancel(); negative_cash.reload(); fixed_cash.reload()
                assert negative_cash.result == "Revisar destinos" and negative_cash.allocated_usd == 0
                assert negative_cash.unclassified_usd == 90
                assert document_state(fixed_cash) == stable_cash

                # A separate application's cancellation restores net, preserving
                # its deposited cash and an unrelated application in the parent.
                adjustment_period, adjusted_source = new_period(companies[0], "ADJUST", 100, payday="2025-06-15" if mode == "Historica" else "2026-11-15")
                _reconcile_sources(companies[0].name); adjusted_source.reload()
                cash = deposit(70, "-CASH", adjustment_period, adjusted_source)
                adjustment = frappe.get_doc({"doctype": "CN Complementary Item", "employer": companies[0].name,
                    "category": "Ajuste de aplicación", "review_action": "Ajuste de aplicación", "posting_date": date,
                    "currency": "USD", "amount": 30, "application_adjustment_usd": 30,
                    "related_application": adjusted_source.rows[0].name, "description": "NC prueba", "review_notes": "Cancelación del pendiente"}).insert()
                confirm_adjustment(adjustment.name)
                cash.reload(); cash_state = document_state(cash)
                stable = [(document, document_state(document.reload())) for document in (first, second, source, period, *[doc for doc, _ in protected])]
                with patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import._reconcile_sources",
                           side_effect=AssertionError("Adjustment cancellation called company engine")):
                    adjustment.reload(); adjustment.cancel()
                cash.reload(); adjusted_source.reload(); adjustment_period.reload()
                assert document_state(cash) == cash_state
                assert adjusted_source.rows[0].net_applied_usd == adjustment_period.applied_usd == 100
                assert adjustment_period.remitted_usd == 70
                for document, snapshot in stable:
                    document.reload()
                    assert document_state(document) == snapshot, ("Adjustment changed an unrelated document", document.name)

                # Periodless generic shared amount: cancel all its real deposit
                # destinations atomically, regardless of company or payment date.
                generic = new_item(500, "-GENERIC", generic=True)
                shared_a = deposit(250, "-SHARED-A", item=generic, portion=250)
                shared_b = deposit(250, "-SHARED-B", item=generic, portion=250, owner=companies[1])
                assert shared_a.allocated_usd == shared_b.allocated_usd == 250
                before = document_state(cash.reload())
                generic.reload(); generic.cancel()
                shared_a.reload(); shared_b.reload(); cash.reload()
                assert shared_a.allocated_usd == shared_b.allocated_usd == 0
                assert shared_a.unclassified_usd == shared_b.unclassified_usd == 250
                assert set(generic.flags.cancellation_result["deposits"]) == {shared_a.name, shared_b.name}
                assert document_state(cash) == before
            return {"both_modes": True, "only_linked_deposits_and_periods_recalculated": True,
                    "same_company_unrelated_closed_period_preserved": True, "other_company_unchanged": True,
                    "adjustment_net_restored_cash_preserved": True, "shared_periodless_item_canceled_atomically": True,
                    "same_application_other_deposit_frozen": True, "automatic_allocation_without_target": True,
                    "company_credit_reclassified_without_moving_cash": True, "negative_item_destinations_flagged": True,
                    "closed_affected_period_blocks_before_writes": True, "target_audit_preserved": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
