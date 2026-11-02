"""Real SQL regression for a long, rejected manual reconciliation reason.

Synthetic fixtures only, restricted to the disposable site and always rolled
back. The complete rejection must be stored without allocating any cash.
"""

import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "TARGET-RESULT-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker)).insert()
            application_period, selected_period = [frappe.get_doc(dict(
                doctype="CN Reconciliation Period", employer=company.name,
                payroll_month=month, reconciliation_mode="Historica")).insert()
                for month in ("2025-04-01", "2025-05-01")]
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_name=marker, client_number=marker)).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                source_file=f"/private/files/{marker}-core.csv", status="Importado", currency="USD",
                historical_backfill=1, rows=[dict(source_key=marker, event_type="Aplicacion",
                    event_date="2025-04-30", currency="USD", amount=100, amount_usd=100,
                    effective=1, historical_period=application_period.name, client=client.name,
                    client_name=client.client_name, client_number=client.client_number,
                    loan_number="10001-1")])).insert()
            engine._reconcile_sources(company.name)
            source.reload()
            application = source.rows[0]
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_date="2025-05-20", deposit_reference=marker, deposit_currency="USD",
                deposit_amount=100, apply_fifo=0, detail_periods=[dict(period=selected_period.name)],
                detail_file=f"/private/files/{marker}-detail.csv",
                detail_source_file=f"/private/files/{marker}-detail.csv", detail_hash=marker,
                detail_rows=[dict(source_row=2, client=client.name, client_name=client.client_name,
                    client_number=client.client_number, loan_number="10001-1", deducted_usd=100)])).insert()
            deposit.append("targets", dict(historical_application=application.name, amount_usd=100,
                detail_row=deposit.detail_rows[0].name))
            deposit.save()
            deposit.submit()

            expected_reason = (
                f"Aplicación {application.name}: el período {application_period.name} está fuera de los "
                "períodos del detalle. Revise Períodos a conciliar o autorice otros períodos."
            )
            assert len(expected_reason) > 140, expected_reason
            for _ in range(2):
                reconcile_deposit(deposit)
                deposit.reload()
                source.reload()
                assert deposit.targets[0].result == expected_reason, deposit.targets[0].result
                assert deposit.detail_rows[0].match_reason == expected_reason
                assert deposit.detail_rows[0].match_status == "Revisar"
                assert deposit.allocated_usd == 0 and deposit.unclassified_usd == 100
                assert not json.loads(deposit.allocation_detail)
                assert source.rows[0].historical_remitted_usd == 0
                assert source.rows[0].historical_balance_usd == 100

            # Explicitly permit the earlier open period, then execute once.
            deposit.allow_manual_other_periods = 1
            deposit.save()
            reconcile_deposit(deposit)
            deposit.reload()
            source.reload()
            assert deposit.targets[0].result == "Aplicada"
            assert deposit.detail_rows[0].match_status == "Conciliada"
            assert deposit.allocated_usd == 100 and deposit.unclassified_usd == 0
            assert source.rows[0].historical_remitted_usd == 100
            assert source.rows[0].historical_balance_usd == 0
            applied = json.loads(deposit.allocation_detail)
            assert len(applied) == 1 and applied[0]["importe_usd"] == 100, applied

            # Reconciliation is idempotent after the invalid link is corrected.
            reconcile_deposit(deposit)
            deposit.reload()
            source.reload()
            assert json.loads(deposit.allocation_detail) == applied
            assert deposit.allocated_usd == source.rows[0].historical_remitted_usd == 100
            return dict(rejection_characters=len(expected_reason), complete_reason_preserved=True,
                rejected_link_spends_no_cash=True, explicit_period_authorization_can_reconcile=True,
                repeated_reconciliation_preserves_one_allocation=True, rolled_back=True)
    finally:
        frappe.db.rollback()
