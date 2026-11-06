"""Multiple manual destinations can supplement an application's confirmed cash."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.remittance_selection import get_pending_targets
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            marker = "MANUAL-REMAINDER-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_number=marker, client_name="Cliente de prueba")).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2026-01-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                historical_backfill=1, historical_period=period.name, status="Importado", currency="USD",
                source_file=f"/private/files/{marker}.csv", rows=[dict(source_key=marker + str(i),
                    event_type="Aplicacion", event_date="2026-01-30", currency="USD", amount=40.05,
                    amount_usd=40.05, effective=1, client=client.name, client_number=client.name,
                    client_name=client.client_name, loan_number=marker + str(i), processing_route="Historica",
                    historical_period=period.name) for i in range(3)])).insert()
            _reconcile_sources(company.name, preserve_deposits=True)
            fixed = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker + "FIXED", deposit_date="2026-02-05", deposit_currency="USD", deposit_amount=5,
                targets=[dict(historical_application=source.rows[2].name, amount_usd=5)])).insert()
            fixed.submit(); reconcile_deposit(fixed)
            fixed_state = document_state(fixed.reload())

            for separate_row in (False, True):
                # Reset only this fixture branch; keep the unrelated deposit as proof.
                frappe.db.savepoint("remainder_case")
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_reference=marker + str(separate_row), deposit_date="2026-02-06", deposit_currency="USD",
                    deposit_amount=80.10, apply_fifo=0, detail_file=f"/private/files/{marker}.xlsx",
                    detail_source_file=f"/private/files/{marker}.xlsx", detail_hash="TEST",
                    detail_periods=[dict(period=period.name)], detail_rows=[dict(source_row=i + 2,
                        client=client.name, client_number=client.name, client_name=client.client_name,
                        employer=company.name, loan_number=marker + "0", amount_usd=amount, deducted_usd=amount)
                        for i, amount in enumerate([44.34, 35.76] if separate_row else [80.10])])).insert()
                first = deposit.detail_rows[0]
                deposit.append("targets", dict(historical_application=source.rows[0].name, amount_usd=4.29, detail_row=first.name))
                deposit.append("targets", dict(historical_application=source.rows[1].name, amount_usd=40.05, detail_row=first.name))
                deposit.save(); deposit.submit(); reconcile_deposit(deposit); deposit.reload()
                # Match an existing partial deposit whose user later enabled FIFO.
                deposit.apply_fifo = 1
                deposit.save()
                original_targets = [row.as_dict() for row in deposit.targets]
                original_ledger = json.loads(deposit.allocation_detail)
                target_row = deposit.detail_rows[-1]
                choices = get_pending_targets(deposit.name, detail_row_name=target_row.name)
                assert any(row.get("historical_application") == source.rows[0].name for row in choices["rows"]), (
                    separate_row, choices, deposit.allocation_detail, source.rows[0].name)
                candidate = next(row for row in choices["rows"] if row.get("historical_application") == source.rows[0].name)
                assert candidate["assigned_cents"] == 429 and candidate["pending_cents"] == 3576, candidate
                assert choices["detail_available_cents"] == 3576, choices
                rechecked = get_pending_targets(deposit.name, detail_row_name=target_row.name,
                    selected_ids=json.dumps([candidate["id"]]))
                assert rechecked["rows"] == [candidate]
                deposit.append("targets", dict(historical_application=source.rows[0].name, amount_usd=35.76,
                    detail_row=target_row.name))
                deposit.save()
                assert not any(row.get("historical_application") == source.rows[0].name for row in
                    get_pending_targets(deposit.name, detail_row_name=target_row.name)["rows"])
                reconcile_deposit(deposit); deposit.reload(); source.reload()
                assert money(deposit.allocated_usd) == money(80.10), deposit.as_dict()
                assert money(deposit.unallocated_usd) == 0
                assert all(money(row.pending_usd) == 0 for row in deposit.detail_rows)
                assert money(source.rows[0].historical_remitted_usd) == money(40.05)
                assert money(source.rows[1].historical_remitted_usd) == money(40.05)
                assert money(source.rows[2].historical_remitted_usd) == money(5)
                for before, after in zip(original_targets, deposit.targets):
                    for field in ("name", "historical_application", "detail_row", "amount_usd"):
                        assert before[field] == after.get(field), (field, before, after.as_dict())
                # Old links may be grouped with the increment, but must keep
                # their claim, detail row, origin and at least their old cash.
                ledger = json.loads(deposit.allocation_detail)
                for before in original_ledger:
                    fields = ("tipo", "aplicacion_id", "fila_detalle", "origen")
                    assert sum((money(row["importe_usd"]) for row in ledger
                        if all(row.get(field) == before.get(field) for field in fields)), money(0)) >= money(before["importe_usd"])
                reconcile_deposit(deposit); deposit.reload()
                assert json.loads(deposit.allocation_detail) == ledger, "Repeated reconcile changed cash"
                assert document_state(fixed.reload()) == fixed_state, "Unrelated reconciliation changed"
                frappe.db.rollback(save_point="remainder_case")
            return dict(pending_usd=35.76, assigned_before_usd=4.29, assigned_after_usd=40.05,
                same_row=True, different_row=True, unchanged_existing_targets=True, unchanged_other_deposit=True,
                idempotent=True, rollback=True)
    finally:
        frappe.db.rollback()
