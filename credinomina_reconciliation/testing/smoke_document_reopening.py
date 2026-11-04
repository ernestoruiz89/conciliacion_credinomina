"""Rollback-only restoration with core evidence, two applications and split cash."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_evidence import CORE_REMOVAL_ROLE, has_core_removal_role
from credinomina_reconciliation.application_adjustment_links import distribute
from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.document_reopening import unconfirm_complement, undo_cancellation
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            if not frappe.db.exists("Role", CORE_REMOVAL_ROLE):
                frappe.get_doc(dict(doctype="Role", role_name=CORE_REMOVAL_ROLE)).insert()
            if not has_core_removal_role():
                frappe.get_doc(dict(doctype="Has Role", parent="Administrator", parenttype="User",
                    parentfield="roles", role=CORE_REMOVAL_ROLE)).insert(ignore_permissions=True)
            assert has_core_removal_role()
            for mode, date in (("Historica", "2025-09-30"), ("Operativa", "2026-09-30")):
                marker = "REOPEN-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
                clients = [frappe.get_doc(dict(doctype="CN Client", employer=employer.name,
                    client_number=marker + str(index), client_name="Prueba " + str(index))).insert() for index in range(2)]
                period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                    payroll_month=date[:7] + "-01", reconciliation_mode=mode, collection_cycle="Mensual"))
                if mode == "Historica":
                    period.historical_scope, period.historical_application_date = "Fecha exacta", date
                else:
                    for index in range(2):
                        period.append("collection_rows", dict(row_key=marker + str(index), client_name="Prueba " + str(index),
                            client=clients[index].name, loan_number=marker + str(index) + "-1",
                            expected_usd=310 if index == 0 else 300))
                period.insert()
                imports = []
                for index in range(2):
                    source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                        currency="USD", source_file=f"/private/files/{marker}-{index}.csv", status="Importado", historical_period=period.name))
                    source.append("rows", dict(source_key=marker + str(index), event_type="Aplicacion", event_date=date,
                        currency="USD", amount=310 if index == 0 else 300, amount_usd=310 if index == 0 else 300,
                        effective=1, processing_route=mode,
                        historical_period=period.name, client_name="Prueba " + str(index),
                        client_number=clients[index].name, client=clients[index].name, loan_number=marker + str(index) + "-1"))
                    source.insert()
                    imports.append(source)
                _reconcile_sources(employer.name)
                def rows():
                    return [doc.reload().rows[0] for doc in imports]
                def source_state():
                    return tuple(document_state(doc.reload()) for doc in imports)
                # Another confirmed adjustment already uses the first credit.
                # Restoring the selected item must neither remove nor count it twice.
                other_key = marker + "-EXISTING"
                existing = frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name,
                    category="Ajuste de aplicación", review_action="Ajuste de aplicación", posting_date=date,
                    currency="USD", amount=10, application_adjustment_usd=10, related_application=rows()[0].name,
                    description="Otro ajuste confirmado del mismo crédito", accounting_source_key=other_key,
                    source_currency="USD", source_credit=10, source_debit=0, source_date=date,
                    source_voucher=other_key, source_description="Otro ajuste confirmado del mismo crédito"))
                with imported_item(other_key):
                    existing.insert()
                confirm_adjustment(existing.name)
                existing_state = document_state(existing.reload())
                deposits = []
                for index, amount in ((0, 100), (0, 70.97), (1, 170.97)):
                    deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                        deposit_reference=marker + str(len(deposits)), deposit_date=date,
                        deposit_currency="USD", deposit_amount=amount))
                    deposit.append("detail_periods", dict(period=period.name))
                    deposit.append("targets", dict(historical_application=rows()[index].name, amount_usd=amount)
                        if mode == "Historica" else dict(period=period.name, row_key=period.collection_rows[index].row_key, amount_usd=amount))
                    deposit.insert(); deposit.submit(); reconcile_deposit(deposit); deposit.reload()
                    deposits.append(deposit)
                # Another reconciled period of the same company is closed.
                # Its receipt, import and payroll must remain byte-for-byte stable.
                spare_key = marker + "-CLOSED"
                spare_period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                    payroll_month="2025-10-01", reconciliation_mode="Historica", collection_cycle="Mensual",
                    historical_scope="Fecha exacta", historical_application_date="2025-10-31")).insert()
                spare_import = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name, currency="USD",
                    source_file=f"/private/files/{spare_key}.csv", status="Importado", historical_period=spare_period.name,
                    rows=[dict(source_key=spare_key, event_type="Aplicacion", event_date="2025-10-31", currency="USD",
                        amount=50, amount_usd=50, effective=1, processing_route="Historica", historical_period=spare_period.name,
                        client=clients[0].name, client_number=clients[0].name, client_name=clients[0].client_name,
                        loan_number=spare_key)])).insert()
                _reconcile_sources(employer.name)
                spare_import.reload()
                spare_cash = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                    deposit_reference=spare_key, deposit_date="2025-10-31", deposit_currency="USD", deposit_amount=50,
                    targets=[dict(historical_application=spare_import.rows[0].name, amount_usd=50)])).insert()
                spare_cash.submit(); reconcile_deposit(spare_cash)
                assert not money(spare_import.reload().rows[0].historical_balance_usd)
                frappe.db.set_value(spare_period.doctype, spare_period.name, "status", "Cerrado")
                protected = [(doc, document_state(doc.reload())) for doc in (spare_period, spare_import, spare_cash)]
                item = frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name,
                    category="Ajuste de aplicación", review_action="Ajuste de aplicación", posting_date=date,
                    currency="USD", amount=258.06, application_adjustment_usd=129.03,
                    related_application=rows()[0].name, description="Nota de crédito de prueba",
                    accounting_source_key=marker, source_currency="USD", source_credit=258.06, source_debit=0,
                    source_date=date, source_voucher=marker, source_description="Nota de crédito de prueba",
                    accounting_classification="Movimiento interno", tmov="01", tdoc="01"))
                with imported_item(marker):
                    item.insert()
                confirm_adjustment(item.name)
                item.reload()
                distribute(item.name, [dict(application=rows()[1].name, amount_usd=129.03)], str(item.modified))
                item.reload(); period.reload()
                assert all(money(row.net_applied_usd) == money(170.97) for row in rows())
                if mode == "Historica":
                    assert all(not money(row.historical_balance_usd) for row in rows())
                assert all(row.historical_period == period.name for row in rows())
                bank = {doc.name: document_state(doc.reload()) for doc in deposits}
                links = item.application_adjustment_history
                source_fields = ("historical_period", "collection_period", "collection_row_id", "application_allocation_detail",
                    "historical_detail", "historical_remitted_usd", "historical_balance_usd", "match_status", "deposit_match_status",
                    "application_adjustment_usd", "net_applied_usd", "complementary_usd", "rounding_adjustment_usd")
                period_fields = ("applied_usd", "remitted_usd", "applied_total_usd", "remitted_total_usd", "pending_usd",
                    "deposit_coverage_usd", "complementary_coverage_usd", "conversion_coverage_usd")
                def source_profile():
                    return [{field: row.get(field) for field in source_fields} for row in rows()]
                def period_profile():
                    period.reload()
                    return {field: period.get(field) for field in period_fields}
                settled_sources, settled_period = source_profile(), period_profile()
                def check_adjustments(confirmed):
                    expected = [money(139.03), money(129.03)] if confirmed else [money(10), money(0)]
                    assert [money(row.application_adjustment_usd) for row in rows()] == expected
                    assert [money(row.amount) for row in rows()] == [money(310), money(300)]
                    assert document_state(existing.reload()) == existing_state
                    assert all(document_state(doc.reload()) == bank[doc.name] for doc in deposits)
                    assert item.reload().application_adjustment_history == links
                    assert all(document_state(doc.reload()) == state for doc, state in protected)
                    traces = frappe.get_all("CN Reconciliation Trace", filters=dict(source_doctype=item.doctype,
                        source_name=item.name, target_doctype="CN Source Row"), fields=["target_name", "delta_usd"], limit_page_length=0)
                    for row in rows():
                        traced = sum((money(entry.delta_usd) for entry in traces if entry.target_name == row.name), money(0))
                        assert traced == (money(129.03) if confirmed else money(0)), "The adjustment audit counted a portion twice"
                    if confirmed:
                        assert source_profile() == settled_sources, "Reconfirmation changed saved application links or coverage"
                        assert period_profile() == settled_period, "Reconfirmation changed the period's financial summary"
                check_adjustments(True)
                # Closed periods reject the complete operation before a status change.
                open_status = period.status
                frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
                try:
                    unconfirm_complement(item.name, str(item.modified), "Prueba de cierre")
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Closed period allowed unconfirmation")
                assert item.reload().docstatus == 1
                frappe.db.set_value(period.doctype, period.name, "status", open_status)
                # Even a failure after financial writes must roll back the
                # complete return to draft, not leave a half-reversed item.
                from credinomina_reconciliation import complementary_cancellation
                scoped = complementary_cancellation.reconcile_scoped
                def fail_after_reversal(*args, **kwargs):
                    scoped(*args, **kwargs)
                    raise RuntimeError("Injected failure after financial writes")
                with patch.object(complementary_cancellation, "reconcile_scoped", side_effect=fail_after_reversal):
                    try:
                        unconfirm_complement(item.name, str(item.modified), "Prueba atómica")
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Failure injection was not reached")
                assert item.reload().docstatus == 1
                assert all(money(row.net_applied_usd) == money(170.97) for row in rows())
                assert all(document_state(doc.reload()) == bank[doc.name] for doc in deposits)
                with patch.object(type(item), "cancel", side_effect=AssertionError("Unconfirmation canceled document")), \
                     patch.object(type(item), "before_cancel", side_effect=AssertionError("Unconfirmation ran cancel hooks")), \
                     patch.object(type(item), "on_cancel", side_effect=AssertionError("Unconfirmation ran cancel hooks")):
                    unconfirm_complement(item.name, str(item.modified), "Corregir el ajuste")
                item.reload(); period.reload()
                assert item.docstatus == 0 and item.review_status == "Ajuste pendiente de confirmar"
                assert item.application_adjustment_history == links
                assert all(money(row.net_applied_usd) == money(300) for row in rows())
                assert all(document_state(doc.reload()) == bank[doc.name] for doc in deposits)
                versions = frappe.get_all("Version", filters=dict(ref_doctype=item.doctype, docname=item.name), pluck="data")
                changes = [entry for value in versions for entry in json.loads(value).get("changed", []) if entry[0] == "docstatus"]
                assert ["docstatus", 1, 0] in changes and ["docstatus", 1, 2] not in changes
                confirm_adjustment(item.name)
                item.reload()
                assert all(money(row.net_applied_usd) == money(170.97) for row in rows())
                item.cancel(); item.reload()
                assert item.docstatus == 2 and all(money(row.net_applied_usd) == money(300) for row in rows())
                before_restore = source_state()
                with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=False):
                    undo_cancellation(item.doctype, item.name, str(item.modified), "Cancelación accidental")
                item.reload()
                assert item.docstatus == 0 and item.application_adjustment_history == links
                assert source_state() == before_restore
                assert all(document_state(doc.reload()) == bank[doc.name] for doc in deposits)
                confirm_adjustment(item.name)
                assert all(money(row.net_applied_usd) == money(170.97) for row in rows())
                check_adjustments(True)
                # Repeated cancellation/restoration cycles use the same two
                # saved links, even when a different adjustment shares a loan.
                for cycle in range(3):
                    item.reload(); item.cancel()
                    check_adjustments(False)
                    with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=False):
                        undo_cancellation(item.doctype, item.name, str(item.modified), f"Revisión de ciclo {cycle}")
                    check_adjustments(False)
                    confirm_adjustment(item.name)
                    check_adjustments(True)
                    # A second click is rejected before any new financial or
                    # audit write, rather than creating another adjustment.
                    before_duplicate = source_state()
                    versions, traces = frappe.db.count("Version"), frappe.db.count("CN Reconciliation Trace")
                    try:
                        confirm_adjustment(item.name)
                    except frappe.ValidationError:
                        pass
                    else:
                        raise AssertionError("Confirmed adjustment accepted a second confirmation")
                    assert source_state() == before_duplicate
                    assert frappe.db.count("Version") == versions and frappe.db.count("CN Reconciliation Trace") == traces
                    check_adjustments(True)

                # A canceled bank receipt returns only to draft, retaining its
                # planned targets and leaving the other two receipts untouched.
                selected = deposits[0].reload()
                selected.cancel(); selected.reload()
                stable_source = source_state()
                stable_period = document_state(period.reload())
                others = {doc.name: document_state(doc.reload()) for doc in deposits[1:]}
                target_ids = [row.name for row in selected.targets]
                with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=False):
                    undo_cancellation(selected.doctype, selected.name, str(selected.modified), "Restaurar depósito")
                selected.reload()
                assert selected.docstatus == 0 and not money(selected.allocated_usd)
                assert [row.name for row in selected.targets] == target_ids
                assert all(row.docstatus == 0 for row in selected.targets)
                assert source_state() == stable_source and document_state(period.reload()) == stable_period
                assert all(document_state(doc.reload()) == others[doc.name] for doc in deposits[1:])
                assert all(document_state(doc.reload()) == state for doc, state in protected)
                # A cash complement uses the same return-to-draft path. Only
                # its assigned cash is released, preserving other receipts.
                fee_key = marker + "FEE"
                fee = frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name,
                    category="Otros ingresos", review_action="Partida de depósito", posting_date=date,
                    currency="USD", amount=10, reference=fee_key, amount_reviewed=1, description="Partida de depósito de prueba",
                    accounting_source_key=fee_key, source_currency="USD", source_credit=10, source_debit=0,
                    source_date=date, source_voucher=fee_key, source_description="Partida de depósito de prueba"))
                with imported_item(fee_key):
                    fee.insert()
                fee.submit(); fee.reload()
                cash = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                    deposit_reference=fee_key, deposit_date=date, deposit_currency="USD", deposit_amount=10,
                    targets=[dict(complementary_item=fee.name, amount_usd=10)])).insert()
                cash.submit(); reconcile_deposit(cash); cash.reload()
                assert money(cash.allocated_usd) == money(10)
                stable_source, stable_period = source_state(), document_state(period.reload())
                unconfirm_complement(fee.name, str(fee.modified), "Revisar partida de depósito")
                assert fee.reload().docstatus == 0
                cash.reload()
                assert not money(cash.allocated_usd) and money(cash.unclassified_usd) == money(10)
                assert cash.targets[0].complementary_item == fee.name
                assert source_state() == stable_source and document_state(period.reload()) == stable_period
                assert all(document_state(doc.reload()) == others[doc.name] for doc in deposits[1:])
            # Accounting imports are not submittable now; retain support for
            # a legacy canceled import without activating its applications.
            legacy = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name, currency="USD",
                source_file=f"/private/files/{marker}-legacy.csv", status="Borrador",
                rows=[dict(event_type="Aplicacion", event_date=date, currency="USD", amount=1, amount_usd=1,
                    source_key=marker + "-LEGACY", effective=0)])).insert()
            frappe.db.set_value(legacy.doctype, legacy.name, "docstatus", 2)
            frappe.db.set_value("CN Source Row", legacy.rows[0].name, "docstatus", 2)
            legacy.reload()
            with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=False):
                undo_cancellation(legacy.doctype, legacy.name, str(legacy.modified), "Restaurar carga antigua")
            assert legacy.reload().docstatus == 0 and legacy.status == "Borrador"
            assert legacy.rows[0].docstatus == 0 and not legacy.rows[0].effective
            return dict(both_modes=True, multiple_application_links_preserved=True, split_cash_unchanged=True,
                no_cancel_during_unconfirmation=True, restore_returns_only_draft=True, closed_period_guard=True,
                audit_status_transition_1_to_0=True, reconfirmation_no_duplicate_adjustment=True,
                repeated_restore_cycles=3, duplicate_confirmation_rejected=True, existing_adjustment_same_credit_preserved=True,
                multiple_accounting_imports=True, unrelated_closed_reconciliation_preserved=True,
                rollback_after_financial_writes=True, cash_item_unconfirmation=True, legacy_import_inactive=True, rolled_back=True)
    finally:
        frappe.db.rollback()
