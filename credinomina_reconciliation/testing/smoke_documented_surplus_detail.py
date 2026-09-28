"""Real-site test of an identified deposit surplus; fixtures are rolled back."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    close_period,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    reconcile_all_sources,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    marker = "surplus-detail-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual",
        }).insert(ignore_permissions=True)
        client_number = marker + "-C"
        loan_number = marker + "-L"
        client = frappe.get_doc({
            "doctype": "CN Client", "employer": employer.name,
            "client_name": "Cliente excedente", "client_number": client_number,
        }).insert(ignore_permissions=True)
        reference = marker + "-REF"
        period = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2027-05-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual", "status": "Cobranza cargada",
            "deduction_basis": "Detalle de empresa",
            "deduction_evidence_date": "2027-05-31",
        })
        period.append("collection_rows", {
            "row_key": marker + "-ROW", "source_row": 2,
            "client": client.name, "client_name": "Cliente excedente",
            "client_number": client_number, "loan_number": loan_number,
            "installment_number": "1", "expected_usd": 100,
            "expected_nio": 3700, "deducted_usd": 100,
            "deducted_nio": 3700, "deduction_currency": "USD",
            "deduction_status": "Deduccion total",
            "deduction_evidence_date": "2027-05-31",
            "application_reference": reference,
        })
        period.insert(ignore_permissions=True)

        imported = frappe.get_doc({
            "doctype": "CN Source Import", "source_type": SOURCE_ACCOUNTING,
            "source_file": f"/private/files/{marker}-core.xlsx", "status": "Importado",
        })
        imported.append("rows", {
            "source_row": 2, "source_key": marker + "-APP",
            "event_type": "Aplicacion", "event_date": "2027-06-05",
            "reference": reference, "voucher": marker + "-AS",
            "accounting_entry": marker + "-AS", "receipt": marker + "-REC",
            "employer_text": employer.name, "client_name": "Cliente excedente",
            "client_number": client_number, "loan_number": loan_number,
            "installment_number": "1", "currency": "USD", "amount": 100,
            "amount_usd": 100, "processing_route": "Operativa",
            "effective": 1, "match_status": "Pendiente",
            "deposit_match_status": "Pendiente",
        })
        imported.insert(ignore_permissions=True)

        frappe.db.savepoint("manual_split_scenario")
        manual_remittance = frappe.get_doc({
            "doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": reference, "deposit_voucher": marker + "-MANUAL",
            "deposit_date": "2027-06-10", "deposit_currency": "USD",
            "deposit_amount": 110,
            "notes": "US$100 a la cuota y US$10 por documentar como excedente.",
        })
        manual_remittance.append("targets", {
            "period": period.name, "row_key": period.collection_rows[0].row_key,
            "amount_usd": 100,
        })
        manual_remittance.insert(ignore_permissions=True)
        manual_remittance.submit()
        manual_remittance.reload()
        assert manual_remittance.detail_status == "Detalle pendiente"
        assert manual_remittance.result == "Detalle pendiente"

        manual_surplus = frappe.get_doc({
            "doctype": "CN Deposit Surplus", "period": period.name,
            "registered_deposit": manual_remittance.name, "amount_usd": 10,
            "reason_type": "Error de la empresa",
            "explanation": "Los US$10 restantes son saldo a favor de la empresa, no pago de crédito.",
        }).insert(ignore_permissions=True)
        manual_surplus.submit()
        manual_remittance.reload()
        assert manual_remittance.detail_status == "Distribución manual; excedente documentado"
        assert manual_remittance.result == "Parcial con saldo a favor"
        assert manual_remittance.targets[0].result == "Aplicada"

        frappe.db.savepoint("manual_split_before_close")
        assert close_period(period.name)["status"] == "Cerrado"
        try:
            frappe.get_doc({
                "doctype": "CN Deposit Surplus", "period": period.name,
                "registered_deposit": manual_remittance.name, "amount_usd": 1,
                "reason_type": "Otro por aclarar",
                "explanation": "Prueba: intentar alterar un saldo a favor cerrado.",
            }).insert(ignore_permissions=True)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se agregó excedente a un período cerrado.")
        manual_surplus.reload()
        try:
            manual_surplus.cancel()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se canceló un excedente de un período cerrado.")
        frappe.db.rollback(save_point="manual_split_before_close")

        manual_surplus.reload()
        manual_surplus.cancel()
        manual_remittance.reload()
        assert manual_remittance.detail_status == "Detalle pendiente"
        assert manual_remittance.result == "Detalle pendiente"
        frappe.db.rollback(save_point="manual_split_scenario")
        period.reload()

        remittance = frappe.get_doc({
            "doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": reference, "deposit_voucher": marker + "-DEP",
            "deposit_date": "2027-06-10", "deposit_currency": "USD",
            "deposit_amount": 110, "detail_period": period.name,
            "detail_file": f"/private/files/{marker}-detail.xlsx",
            "detail_source_file": f"/private/files/{marker}-detail.xlsx",
            "detail_hash": marker + "-detail", "detail_count": 1,
            "notes": "Depósito sintético: US$100 de cuota y US$10 excedentes.",
        })
        remittance.append("detail_rows", {
            "source_row": 2, "row_key": period.collection_rows[0].row_key,
            "client": client.name, "identity_reason": "Identificador exacto",
            "client_name": "Cliente excedente", "client_number": client_number,
            "loan_number": loan_number, "installment_number": "1",
            "application_reference": reference, "deducted_usd": 100,
        })
        remittance.insert(ignore_permissions=True)
        remittance.submit()
        reconcile_all_sources()
        remittance.reload()
        assert remittance.detail_status == "Parcial; saldo sin detalle"
        assert round(remittance.unallocated_usd, 4) == 10
        assert round(remittance.justified_surplus_usd, 4) == 0

        surplus = frappe.get_doc({
            "doctype": "CN Deposit Surplus", "period": period.name,
            "registered_deposit": remittance.name,
            "amount_usd": 10, "reason_type": "Error de la empresa",
            "explanation": "Excedente de US$10 informado por la empresa; queda como saldo a favor no aplicado al crédito.",
        }).insert(ignore_permissions=True)
        surplus.submit()
        remittance.reload()
        assert remittance.detail_status == "Conciliado; excedente documentado"
        assert remittance.result == "Parcial con saldo a favor"
        assert round(remittance.justified_surplus_usd, 4) == 10
        assert round(remittance.unclassified_usd, 4) == 0

        frappe.db.savepoint("surplus_detail_before_close")
        assert close_period(period.name)["status"] == "Cerrado"
        frappe.db.rollback(save_point="surplus_detail_before_close")

        surplus.cancel()
        remittance.reload()
        assert remittance.detail_status == "Parcial; saldo sin detalle"
        assert remittance.result == "Parcial"
        try:
            close_period(period.name)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se cerró un período con saldo sin detalle ni excedente documentado.")
        return {
            "manual_split_plus_surplus_needs_no_file": True,
            "manual_split_reversible": True,
            "documented_surplus_clears_detail": True,
            "close_allowed_when_documented": True,
            "closed_period_surplus_protected": True,
            "cancel_reopens_detail_gap": True,
            "close_blocked_after_cancel": True,
        }
    finally:
        frappe.db.rollback()
