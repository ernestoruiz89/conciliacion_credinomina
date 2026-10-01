"""Real-site guard test; all fixtures are rolled back before returning."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    close_period,
    reopen_period,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
    reconcile_all_sources,
)


def _new_import(marker, employer, client_number, loan_number, reference, voucher, deposit_voucher=None):
    doc = frappe.get_doc({
        "doctype": "CN Accounting Import",
        "source_file": f"/private/files/{marker}-{voucher}.xlsx", "status": "Importado",
    })
    doc.append("rows", {
        "source_row": 2, "source_key": f"{marker}-{voucher}",
        "event_type": "Aplicacion", "event_date": "2027-05-05",
        "reference": reference, "voucher": voucher,
        "accounting_entry": voucher, "receipt": f"REC-{voucher}",
        "employer_text": employer, "client_name": "Cliente de cierre",
        "client_number": client_number, "loan_number": loan_number,
        "installment_number": "1", "currency": "USD", "amount": 50,
        "amount_usd": 50, "processing_route": "Operativa",
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
        "deposit_amount": 50, "detail_period": period.name,
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


def _must_reject_reconciliation():
    try:
        reconcile_all_sources()
    except frappe.ValidationError as exc:
        if "Reabrir período" not in str(exc):
            raise AssertionError(f"Rechazo inesperado: {exc}") from exc
        return
    raise AssertionError("La conciliación modificó un período operativo cerrado.")


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
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
        reconcile_all_sources()
        provisional_import = frappe.get_doc("CN Accounting Import", original_import.name)
        assert provisional_import.rows[0].match_status == "Enlace provisional"
        assert provisional_import.rows[0].deposit_match_status == "Pendiente"
        assert provisional_import.matched_count == 0
        assert provisional_import.exception_count >= 1
        period.reload()
        assert period.collection_rows[0].applied_usd == 50
        assert period.collection_rows[0].application_status == "Aplicacion encontrada"
        period.deduction_basis = "Detalle de empresa"
        period.deduction_evidence_date = "2027-04-30"
        period.collection_rows[0].deducted_usd = 50
        period.collection_rows[0].deducted_nio = 1850
        period.collection_rows[0].deduction_currency = "USD"
        period.collection_rows[0].deduction_status = "Deduccion total"
        period.collection_rows[0].deduction_evidence_date = "2027-04-30"
        period.save()
        old_remittance = _new_remittance(
            marker, employer.name, period, client.name, client_number,
            loan_number, reference, "DEP-OLD", submit=True,
        )
        replacement_remittance = _new_remittance(
            marker, employer.name, period, client.name, client_number,
            loan_number, reference, "DEP-NEW", submit=False,
        )
        reconcile_all_sources()
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
            "expected_nio": 740, "deduction_status": "No deducido",
            "application_status": "Pendiente",
        })
        period.save()
        reconcile_all_sources()
        period.reload()
        assert period.status != "Conciliado"
        assert period.status == "Parcial"
        assert period.collection_rows[0].application_status == "Aplicado y remitido"
        assert period.collection_rows[1].deduction_status == "No deducido"
        frappe.db.rollback(save_point="mixed_payroll_status")
        period.reload()

        close_period(period.name)
        closed_before = frappe.get_doc("CN Reconciliation Period", period.name)
        modified_before = closed_before.modified
        reconcile_all_sources()
        closed_after = frappe.get_doc("CN Reconciliation Period", period.name)
        assert closed_after.status == "Cerrado"
        assert closed_after.modified == modified_before
        remittance_result_before = frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        )

        frappe.db.savepoint("closed_guard_application")
        frappe.db.set_value("CN Accounting Import", original_import.name, "status", "Fallido")
        replacement_import = _new_import(
            marker, employer.name, client_number, loan_number, reference, "APP-NEW",
        )
        _must_reject_reconciliation()
        frappe.db.rollback(save_point="closed_guard_application")
        assert frappe.db.get_value("CN Accounting Import", original_import.name, "status") == "Importado"
        assert not frappe.db.exists("CN Accounting Import", replacement_import.name)
        assert frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        ) == remittance_result_before

        frappe.db.savepoint("closed_guard_remittance")
        frappe.db.set_value("CN Remittance Allocation", old_remittance.name, "docstatus", 2)
        frappe.db.set_value("CN Remittance Allocation", replacement_remittance.name, "docstatus", 1)
        _must_reject_reconciliation()
        frappe.db.rollback(save_point="closed_guard_remittance")
        assert frappe.db.get_value("CN Remittance Allocation", old_remittance.name, "docstatus") == 1
        assert frappe.db.get_value("CN Remittance Allocation", replacement_remittance.name, "docstatus") == 0
        assert frappe.db.get_value(
            "CN Remittance Allocation", old_remittance.name, "result",
        ) == remittance_result_before
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
            "provisional_until_detail": True,
            "mixed_payroll_not_fully_reconciled": True,
            "idempotent": True, "application_replacement_blocked": True,
            "remittance_replacement_blocked": True,
            "closed_write_blocked": True, "reopened_and_reclosed": True,
        }
    finally:
        frappe.db.rollback()
