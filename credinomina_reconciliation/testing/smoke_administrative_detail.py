"""Historical deposit: client detail + separate administrative collection.

Synthetic fixtures only, always rolled back. No user files or records are used.
"""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import reconcile_all_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo puede ejecutarse en el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = "admin-detail-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                  "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                 "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
                                 "historical_scope": "Mensual"}).insert()
        imported = frappe.get_doc({
            "doctype": "CN Accounting Import", "employer": employer.name,
            "status": "Importado",
            "source_file": f"/private/files/{marker}-core.xlsx", "historical_backfill": 1,
            "historical_period": period.name,
        })
        amounts = (20.40, 17.70, 27.14, 21.71, 16.21, 58.61)
        for i, amount in enumerate(amounts):
            imported.append("rows", {
                "source_row": i + 2, "source_key": f"{marker}-{i}", "event_type": "Aplicacion",
                "event_date": "2025-04-30", "client_name": f"Cliente de prueba {i}",
                "client_number": f"{marker}-C{i}", "loan_number": f"{marker}-L{i}",
                "reference": f"{marker}-APP{i}", "accounting_entry": f"{marker}-AS{i}",
                "receipt": f"{marker}-REC{i}", "currency": "USD", "amount": amount,
                "amount_usd": amount, "processing_route": "Historica", "historical_period": period.name,
                "effective": 1, "match_status": "Pendiente",
            })
        imported.insert()
        reconcile_all_sources()
        imported.reload()
        deposit = frappe.get_doc({
            "doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": f"{marker}-DEP", "deposit_voucher": f"{marker}-DEP",
            "deposit_date": "2025-05-20", "deposit_currency": "NIO", "deposit_amount": 4394.92,
            "fx_rate": 36.6243, "detail_period": period.name,
            "detail_file": f"/private/files/{marker}-detail.xlsx",
            "detail_source_file": f"/private/files/{marker}-detail.xlsx", "detail_hash": marker,
        })
        for i, application in enumerate(imported.rows[:5]):
            deposit.append("detail_rows", {
                "source_row": i + 2, "client_number": application.client_number,
                "client_name": application.client_name, "loan_number": application.loan_number,
                "application_reference": application.reference, "deducted_usd": amounts[i],
            })
        deposit.insert()
        deposit.submit()
        reconcile_all_sources()
        deposit.reload()
        assert deposit.detail_status == "Parcial; saldo sin detalle", (
            deposit.detail_status, [(r.match_status, r.match_reason) for r in deposit.detail_rows]
        )
        assert deposit.unclassified_usd == 16.84
        item = frappe.get_doc({
            "doctype": "CN Complementary Item", "employer": employer.name, "period": period.name,
            "reference": deposit.deposit_reference, "category": "Cobranza administrativa",
            "posting_date": "2025-05-20", "currency": "USD", "amount": 16.84,
            "description": "Cobranza administrativa separada de los pagos por cliente.",
        })
        item.flags.defer_reconciliation = True
        item.insert()
        item.submit()
        deposit.append("targets", {"complementary_item": item.name, "amount_usd": 16.84})
        deposit.save()
        reconcile_all_sources()
        deposit.reload()
        assert deposit.result == "Conciliado", deposit.result
        assert deposit.detail_status == "Conciliado", deposit.detail_status
        assert deposit.allocated_usd == 120 and deposit.unclassified_usd == 0
        assert deposit.detail_total_usd == 103.16 and len(deposit.detail_rows) == 5
        assert all(row.match_status == "Conciliada" for row in deposit.detail_rows)
        assert deposit.targets[0].result == "Aplicada"
        assert item.accounting_status == "Pendiente de registro"
        period.reload()
        assert period.applied_usd == 161.77 and period.remitted_usd == 103.16
        try:
            close_period(period.name)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se cerró el período con US$58.61 de aplicaciones pendientes.")
        other = frappe.get_doc({
            "doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": f"{marker}-OTHER", "deposit_date": "2025-05-25",
            "deposit_currency": "USD", "deposit_amount": 58.61,
        })
        other.append("targets", {"historical_application": imported.rows[-1].name, "amount_usd": 58.61})
        other.insert()
        other.submit()
        reconcile_all_sources()
        assert close_period(period.name)["status"] == "Cerrado"
        return {"deposit_usd": 120, "client_detail_usd": 103.16, "administrative_usd": 16.84,
                "client_rows": 5, "deposit_reconciled": True, "closure_blocked_while_other_applications_unpaid": True,
                "closure_allowed_after_payment": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
