"""Partial row links and unassigned deposit cash must not create period surplus."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.patches.v1_0.separate_period_and_deposit_balances import execute as backfill
from credinomina_reconciliation.period_pending import get_period_pending


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "PENDING-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype":"CN Employer", "employer_name":marker, "employer_code":marker}).insert()
            period = frappe.get_doc({"doctype":"CN Reconciliation Period", "employer":employer.name,
                "payroll_month":"2025-04-01", "reconciliation_mode":"Historica",
                "historical_scope":"Fecha exacta", "historical_application_date":"2025-04-30"}).insert()
            imported = frappe.get_doc({"doctype":"CN Accounting Import", "employer":employer.name,
                "status":"Importado", "source_file":f"/private/files/{marker}.xlsx", "historical_backfill":1,
                "historical_period":period.name})
            for i, amount in enumerate((147.95, 1334.69, 142.32)):
                imported.append("rows", {"source_row":i+2, "source_key":f"{marker}-{i}", "event_type":"Aplicacion",
                    "event_date":"2025-04-30", "client_name":f"{marker} Cliente {i}", "client_number":f"{marker}-C{i}",
                    "loan_number":f"{marker}-L{i}", "reference":f"{marker}-REF{i}", "currency":"USD", "amount":amount,
                    "amount_usd":amount, "processing_route":"Historica", "historical_period":period.name,
                    "effective":1, "match_status":"Pendiente"})
            imported.insert()
            _reconcile_sources(employer.name)
            imported.reload()
            deposit = frappe.get_doc({"doctype":"CN Remittance Allocation", "employer":employer.name,
                "deposit_date":"2025-05-27", "deposit_reference":marker, "deposit_currency":"NIO",
                "deposit_amount":58658.58, "fx_rate":36.6243, "detail_periods": [{"period": period.name}],
                "detail_file":f"/private/files/{marker}-detail.xlsx", "detail_source_file":f"/private/files/{marker}-detail.xlsx",
                "detail_hash":marker})
            for i, amount in enumerate((165.16, 1334.69)):
                application = imported.rows[i]
                deposit.append("detail_rows", {"source_row":i+2, "client_name":application.client_name,
                    "client_number":application.client_number, "loan_number":application.loan_number,
                    "deducted_usd":amount, "amount_usd":amount})
            deposit.insert()
            deposit.append("targets", {"historical_application":imported.rows[0].name,
                "amount_usd":147.95, "detail_row":deposit.detail_rows[0].name})
            deposit.save()
            assert deposit.detail_rows[0].pending_usd == 17.21
            deposit.submit()
            _reconcile_sources(employer.name)
            deposit.reload()
            period.reload()
            assert (period.status, period.applied_usd, period.remitted_usd) == ("Parcial", 1624.96, 1482.64)
            assert period.unassigned_deposit_usd == 118.99
            assert (deposit.detail_rows[0].linked_usd, deposit.detail_rows[0].pending_usd) == (147.95, 17.21)
            assert deposit.detail_rows[0].match_status == "Revisar"
            assert deposit.detail_rows[1].pending_usd == 0
            assert deposit.result == "Revisar detalle"
            before = deposit.allocation_detail
            pending = get_period_pending(period.name)
            assert pending["total"] == 2, pending
            application_issue, = [row for row in pending["rows"] if row["kind"] == "Aplicación"]
            deposit_issue, = [row for row in pending["rows"] if row["kind"] == "Depósito"]
            assert application_issue["pending"] == 142.32, application_issue
            assert deposit_issue["pending"] == 118.99, deposit_issue
            assert deposit_issue["applied"] == 1482.64, deposit_issue
            assert get_period_pending(period.name, kind="Aplicación")["count"] == 1
            deposit.reload()
            assert deposit.allocation_detail == before
            frappe.db.set_value(period.doctype, period.name, "status", "Con excedente")
            backfill()
            backfill()
            period.reload()
            deposit.reload()
            assert period.status == "Parcial" and deposit.allocation_detail == before
            assert deposit.detail_rows[0].pending_usd == 17.21
            return {"period_status":"Parcial", "pending_period_usd":142.32,
                    "unassigned_deposit_usd":118.99, "pending_detail_usd":17.21,
                    "row_still_under_review":True, "migration_idempotent":True, "rolled_back":True}
    finally:
        frappe.db.rollback()
