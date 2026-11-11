"""Assigned applications, explicit cash reconciliation and closed-period guards.

An identified payroll match is not a deposit. Current applications require an
explicit period; the legacy ``Enlace provisional`` label is no longer generated
for new matches. All fixtures use the disposable site and are rolled back.
"""

from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    close_period,
    reopen_period,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
    _reconcile_sources,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import reconcile_remittance
from credinomina_reconciliation.reconciliation_scope import document_state


def _new_import(marker, employer, client_number, loan_number, reference, voucher, deposit_voucher=None,
                *, period=None, amount=50):
    doc = frappe.get_doc({
        "doctype": "CN Accounting Import", "employer": employer, "currency": "USD",
        "historical_period": period,
        "source_file": f"/private/files/{marker}-{voucher}.xlsx", "status": "Importado",
    })
    doc.append("rows", {
        "source_row": 2, "source_key": f"{marker}-{voucher}",
        "event_type": "Aplicacion", "event_date": "2027-05-05",
        "reference": reference, "voucher": voucher,
        "accounting_entry": voucher, "receipt": f"REC-{voucher}",
        "employer_text": employer, "client_name": "Cliente de cierre",
        "client_number": client_number, "loan_number": loan_number,
        "installment_number": "1", "currency": "USD", "amount": amount,
        "amount_usd": amount, "processing_route": "Operativa", "historical_period": period,
        "effective": 1, "match_status": "Pendiente",
        "deposit_match_status": "Pendiente",
    })
    if deposit_voucher:
        doc.append("rows", {
            "source_row": 3, "source_key": f"{marker}-{deposit_voucher}",
            "event_type": "Deposito", "event_date": "2027-05-10",
            "reference": reference, "voucher": deposit_voucher,
            "accounting_entry": deposit_voucher,
            "employer_text": employer, "currency": "USD", "amount": 50,
            "amount_usd": 50,
            "effective": 1, "match_status": "Pendiente",
            "deposit_match_status": "Pendiente",
        })
    doc.insert(ignore_permissions=True)
    return doc


def _new_remittance(marker, employer, period, client, client_number, loan_number, reference, voucher, submit):
    doc = frappe.get_doc({
        "doctype": "CN Remittance Allocation", "employer": employer,
        "deposit_reference": reference, "deposit_voucher": voucher,
        "deposit_date": "2027-05-10", "deposit_currency": "USD",
        "deposit_amount": 50, "detail_periods": [{"period": period.name}],
        "notes": "Depósito sintético para verificar el bloqueo del período cerrado.",
        "detail_file": f"/private/files/{marker}-{voucher}-detalle.xlsx",
        "detail_source_file": f"/private/files/{marker}-{voucher}-detalle.xlsx",
        "detail_hash": f"{marker}-{voucher}", "detail_count": 1,
    })
    doc.append("detail_rows", {
        "source_row": 2, "row_key": period.collection_rows[0].row_key,
        "client": client, "identity_reason": "Identificador exacto",
        "client_name": "Cliente de cierre", "client_number": client_number,
        "loan_number": loan_number, "installment_number": "1",
        "application_reference": reference, "deducted_usd": 50,
    })
    doc.insert(ignore_permissions=True)
    if submit:
        doc.submit()
    return doc


def _must_reject(action, expected_message="Reabrir período"):
    try:
        action()
    except frappe.ValidationError as exc:
        if expected_message not in str(exc):
            raise AssertionError(f"Rechazo inesperado: {exc}") from exc
        return
    raise AssertionError("La conciliación modificó un período operativo cerrado.")


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    tables = ("CN Employer", "CN Client", "CN Accounting Import", "CN Source Row",
              "CN Remittance Allocation", "CN Remittance Detail", "CN Remittance Target",
              "CN Reconciliation Period", "CN Collection Row")
    before = {doctype: frappe.db.count(doctype) for doctype in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            return _run()
    finally:
        frappe.db.rollback()
        assert before == {doctype: frappe.db.count(doctype) for doctype in tables}, "El ensayo dejó registros"


def _run():
    marker = "closed-guard-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual",
        }).insert(ignore_permissions=True)
        client_number = marker + "-C"
        loan_number = marker + "-L"
        client = frappe.get_doc({
            "doctype": "CN Client", "employer": employer.name,
            "client_name": "Cliente de cierre", "client_number": client_number,
        }).insert(ignore_permissions=True)
        reference = marker + "-REF"
        period = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2027-04-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual", "status": "Pendiente",
        })
        period.append("collection_rows", {
            "row_key": marker + "-ROW", "source_row": 2, "client": client.name,
            "client_name": "Cliente de cierre", "client_number": client_number,
            "loan_number": loan_number, "installment_number": "1",
            "expected_usd": 50, "expected_nio": 1850,
            "deduction_status": "Pendiente de detalle",
            "application_reference": reference, "application_status": "Pendiente",
        })
        period.insert(ignore_permissions=True)
        original_import = _new_import(
            marker, employer.name, client_number, loan_number, reference,
            "APP-OLD", deposit_voucher="DEP-OLD",
        )
        _reconcile_sources(employer.name)
        original_import.reload()
        unassigned = original_import.rows[0]
        assert unassigned.match_status == "Sin coincidencia", unassigned.match_reason
        assert "sin período operativo asignado" in unassigned.match_reason
        assert not unassigned.collection_row_id and unassigned.application_allocation_detail == "[]"
        period.reload()
        assert period.applied_usd == period.remitted_usd == 0

        original_import.historical_period = period.name
        original_import.rows[0].historical_period = period.name
        original_import.save()
        _reconcile_sources(employer.name)
        original_import.reload()
        assert original_import.rows[0].match_status == "Conciliado"
        assert original_import.rows[0].collection_row_id == period.collection_rows[0].name
        assert original_import.rows[0].deposit_match_status == "Sin deposito"
        assert original_import.matched_count == 0 and original_import.exception_count >= 1
        assert original_import.rows[0].historical_remitted_usd == 0
        assert original_import.rows[0].historical_balance_usd == 50
        period.reload()
        assert period.collection_rows[0].applied_usd == 50
        assert period.collection_rows[0].application_status == "Aplicacion encontrada"
        _must_reject(lambda: close_period(period.name), "aplicaciones pendientes de cubrir")
        period.reload()
        period.deduction_basis = "Detalle de empresa"
        period.deduction_evidence_date = "2027-04-30"
        period.collection_rows[0].deducted_usd = 50
        period.collection_rows[0].deducted_nio = 1850
        period.collection_rows[0].deduction_currency = "USD"
        period.collection_rows[0].deduction_status = "Deduccion total"
        period.collection_rows[0].deduction_evidence_date = "2027-04-30"
        _must_reject(period.save, "El detalle anterior es de solo lectura")
        period.reload()
        old_remittance = _new_remittance(
            marker, employer.name, period, client.name, client_number,
            loan_number, reference, "DEP-OLD", submit=True,
        )
        replacement_remittance = _new_remittance(
            marker, employer.name, period, client.name, client_number,
            loan_number, reference, "DEP-NEW", submit=False,
        )
        # Merely registering or refreshing a deposit must never spend it.
        _reconcile_sources(employer.name)
        old_remittance.reload()
        assert old_remittance.allocated_usd == 0
        period.reload()
        assert period.remitted_usd == 0 and period.pending_usd == 50
        reconcile_remittance(old_remittance.name)
        old_remittance.reload()
        cash_before = document_state(old_remittance)
        period.reload()
        assert period.collection_rows[0].application_status == "Aplicado y remitido"
        confirmed_import = frappe.get_doc("CN Accounting Import", original_import.name)
        assert confirmed_import.rows[0].match_status == "Conciliado"
        assert confirmed_import.rows[0].deposit_match_status == "Depósito conciliado"

        frappe.db.savepoint("mixed_payroll_status")
        period.append("collection_rows", {
            "row_key": marker + "-UNDEDUCTED", "source_row": 3,
            "client": client.name, "client_name": "Cliente de cierre",
            "client_number": client_number, "loan_number": marker + "-OTHER",
            "installment_number": "1", "expected_usd": 20,
            "expected_nio": 740, "deduction_status": "Pendiente de detalle",
            "application_status": "Pendiente",
        })
        period.save()
        _reconcile_sources(employer.name)
        period.reload()
        # A collection without an application remains an informational mismatch;
        # it does not create a new financial claim against the existing deposit.
        assert period.status == "Conciliado"
        assert period.applied_usd == period.remitted_usd == 50 and period.pending_usd == 0
        assert period.collection_rows[0].application_status == "Aplicado y remitido"
        assert period.collection_rows[1].deducted_usd == 0
        assert period.collection_rows[1].quality_difference_usd == -20
        assert document_state(old_remittance.reload()) == cash_before
        _new_import(marker, employer.name, client_number, marker + "-OTHER", reference,
                    "APP-PENDING", period=period.name, amount=20)
        _reconcile_sources(employer.name)
        period.reload()
        assert period.status == "Parcial" and period.pending_usd == 20
        _must_reject(lambda: close_period(period.name), "aplicaciones pendientes de cubrir")
        assert document_state(old_remittance.reload()) == cash_before
        frappe.db.rollback(save_point="mixed_payroll_status")
        period.reload()

        close_period(period.name)
        closed_before = frappe.get_doc("CN Reconciliation Period", period.name)
        modified_before = closed_before.modified
        _reconcile_sources(employer.name)
        closed_after = frappe.get_doc("CN Reconciliation Period", period.name)
        assert closed_after.status == "Cerrado"
        assert closed_after.modified == modified_before
        remittance_result_before = frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        )
        source_before = document_state(original_import.reload())

        frappe.db.savepoint("closed_guard_application")
        frappe.db.set_value("CN Accounting Import", original_import.name, "status", "Fallido")
        # The normal import path must reject a new assignment even before the
        # company refresh can replace any application in the closed period.
        _must_reject(lambda: _new_import(
            marker, employer.name, client_number, loan_number, reference, "APP-NEW", period=period.name,
        ))
        _must_reject(lambda: _reconcile_sources(employer.name))
        frappe.db.rollback(save_point="closed_guard_application")
        assert document_state(original_import.reload()) == source_before
        assert frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        ) == remittance_result_before

        frappe.db.savepoint("closed_guard_remittance")
        frappe.db.set_value("CN Remittance Allocation", old_remittance.name, "docstatus", 2)
        frappe.db.set_value("CN Remittance Allocation", replacement_remittance.name, "docstatus", 1)
        _must_reject(lambda: _reconcile_sources(employer.name))
        frappe.db.rollback(save_point="closed_guard_remittance")
        assert frappe.db.get_value("CN Remittance Allocation", old_remittance.name, "docstatus") == 1
        assert frappe.db.get_value("CN Remittance Allocation", replacement_remittance.name, "docstatus") == 0
        assert frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        ) == remittance_result_before
        assert document_state(old_remittance.reload()) == cash_before
        assert document_state(original_import.reload()) == source_before
        closed_after.reload()
        closed_after.notes = "Cambio no autorizado"
        try:
            closed_after.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("El período cerrado permitió editar notas.")
        reopened = reopen_period(period.name, "Revisión del cierre liquidado")
        assert reopened["status"] != "Cerrado"
        open_period = frappe.get_doc("CN Reconciliation Period", period.name)
        assert open_period.reopened_by == "Administrator"
        open_period.notes = "Revisión posterior a reapertura"
        open_period.save()
        close_period(period.name)
        assert frappe.db.get_value("CN Reconciliation Period", period.name, "status") == "Cerrado"
        return {
            "unassigned_application_not_matched": True,
            "payroll_match_does_not_create_cash": True,
            "legacy_employer_evidence_read_only": True,
            "explicit_deposit_reconciliation": True,
            "unapplied_payroll_is_informational": True,
            "unfunded_application_blocks_closure": True,
            "idempotent": True, "application_replacement_blocked": True,
            "remittance_replacement_blocked": True,
            "closed_write_blocked": True, "reopened_and_reclosed": True,
            "existing_cash_and_links_preserved": True,
        }
    finally:
        frappe.db.rollback()
