"""Legacy manual ledger gains row metadata while keeping all confirmed cash."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            marker = "LEGACY-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            clients = [frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_number=marker + str(index),
                client_name="Cliente " + str(index))).insert() for index in range(3)]
            periods = [frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-11-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-11-" + day,
                collection_cycle="Primera quincena" if day == "15" else "Segunda quincena")).insert() for day in ("15", "30")]
            def application(suffix, client, period, amount):
                return frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, currency="USD",
                    source_file=f"/private/files/{marker}-{suffix}.csv", status="Importado", historical_backfill=1,
                    historical_period=period.name, rows=[dict(source_key=marker + suffix, event_type="Aplicacion",
                        event_date=period.historical_application_date, currency="USD", amount=amount, amount_usd=amount,
                        client=client.name, client_number=client.name, client_name=client.client_name, loan_number=marker + suffix,
                        processing_route="Historica", historical_period=period.name, effective=1)])).insert()
            first = application("A", clients[0], periods[1], 26)
            second = application("B", clients[0], periods[0], 23.48)
            automatic = application("AUTO", clients[1], periods[0], 40)
            _reconcile_sources(company.name, preserve_deposits=True)
            fixed = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker + "FIXED", deposit_date="2025-12-17", deposit_currency="USD", deposit_amount=5,
                targets=[dict(historical_application=automatic.rows[0].name, amount_usd=5)])).insert()
            fixed.submit(); reconcile_deposit(fixed)
            fixed_state = document_state(fixed.reload())
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-12-18", deposit_currency="USD", deposit_amount=100,
                detail_file=f"/private/files/{marker}.xlsx", detail_source_file=f"/private/files/{marker}.xlsx",
                detail_hash="TEST", apply_fifo=1,
                detail_periods=[dict(period=period.name) for period in periods],
                detail_rows=[dict(client=client.name, client_number=client.name, client_name=client.client_name,
                    employer=company.name, loan_number=marker + suffix if suffix != "A" else "", deducted_usd=amount, amount_usd=amount)
                    for client, suffix, amount in ((clients[0], "A", 49.48), (clients[1], "AUTO", 35), (clients[2], "LATE", 15.52))])).insert()
            row_name = deposit.detail_rows[0].name
            for document, amount in ((first, 26), (second, 23.48)):
                deposit.append("targets", dict(historical_application=document.rows[0].name, amount_usd=amount, detail_row=row_name))
            deposit.save(); deposit.submit(); reconcile_deposit(deposit)
            deposit.reload()
            assert money(deposit.allocated_usd) == money(84.48), (deposit.allocated_usd,
                [(row.amount_usd, row.match_status, row.match_reason) for row in deposit.detail_rows])
            original = json.loads(deposit.allocation_detail)
            def legacy_ledger():
                saved = json.loads(deposit.allocation_detail)
                for entry in saved:
                    if entry.get("origen") == "Selección manual":
                        entry.pop("fila_detalle", None)
                frappe.db.set_value(deposit.doctype, deposit.name, "allocation_detail", json.dumps(saved))
                deposit.reload()
            legacy_ledger()
            reconcile_deposit(deposit); deposit.reload()
            assert json.loads(deposit.allocation_detail) == original
            assert money(deposit.allocated_usd) == money(84.48)
            legacy_ledger()
            late = application("LATE", clients[2], periods[0], 15.52)
            _reconcile_sources(company.name, preserve_deposits=True)
            deposit.reload()
            reconcile_deposit(deposit); deposit.reload()
            current = json.loads(deposit.allocation_detail)
            def portions(ledger):
                return {(entry["aplicacion_id"], entry.get("fila_detalle")): money(entry["importe_usd"]) for entry in ledger}
            before, after = portions(original), portions(current)
            assert all(after[key] == amount for key, amount in before.items())
            assert money(deposit.allocated_usd) == money(100) and not money(deposit.unallocated_usd)
            assert after[(late.rows[0].name, deposit.detail_rows[2].name)] == money(15.52)
            assert all(entry.get("fila_detalle") == row_name for entry in current if entry.get("origen") == "Selección manual")
            assert document_state(fixed.reload()) == fixed_state
            reconcile_deposit(deposit); deposit.reload()
            assert portions(json.loads(deposit.allocation_detail)) == after
            assert document_state(fixed.reload()) == fixed_state
            return dict(legacy_manual_links_preserved=True, fifo_cash_preserved=True, metadata_completed=True,
                added_only_remaining_cash=15.52, repeated_reconciliation_preserved=True, other_deposit_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
