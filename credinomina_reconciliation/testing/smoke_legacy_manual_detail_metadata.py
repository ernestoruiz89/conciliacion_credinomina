"""Legacy manual ledger gains row metadata while keeping all confirmed cash."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run(manual_remainder=False):
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
            def application(suffix, client, period, amount, loan_suffix=None):
                return frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, currency="USD",
                    source_file=f"/private/files/{marker}-{suffix}.csv", status="Importado", historical_backfill=1,
                    historical_period=period.name, rows=[dict(source_key=marker + suffix, event_type="Aplicacion",
                        event_date=period.historical_application_date, currency="USD", amount=amount, amount_usd=amount,
                        client=client.name, client_number=client.name, client_name=client.client_name, loan_number=marker + (loan_suffix or suffix),
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
            core_amount = 526.55 if manual_remainder else 0
            if manual_remainder:
                application("CORE", clients[2], periods[0], core_amount)
                _reconcile_sources(company.name, preserve_deposits=True)
                from credinomina_reconciliation.manual_complementary_policy import imported_item
                key = marker + "CORE"
                core = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, currency="USD",
                    amount=core_amount, category="Otros ingresos", review_action="Partida de depósito", amount_reviewed=1,
                    reference=marker, posting_date="2025-12-18", description="Contrapartida importada de prueba",
                    accounting_source_key=key, source_currency="USD", source_credit=core_amount, source_debit=0,
                    source_date="2025-12-18", client_number=clients[2].name, loan_number=marker + "CORE"))
                with imported_item(key):
                    core.insert()
                core.submit()
            details = ((clients[0], "A", 49.48), (clients[1], "AUTO", 50.52), (clients[2], "CORE", core_amount)) if manual_remainder else (
                       (clients[0], "A", 49.48), (clients[1], "AUTO", 35), (clients[2], "LATE", 15.52))
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-12-18", deposit_currency="USD", deposit_amount=100 + core_amount,
                detail_file=f"/private/files/{marker}.xlsx", detail_source_file=f"/private/files/{marker}.xlsx",
                detail_hash="TEST", apply_fifo=1,
                detail_periods=[dict(period=period.name) for period in periods],
                detail_rows=[dict(client=client.name, client_number=client.name, client_name=client.client_name,
                    employer=company.name, loan_number=marker + suffix if suffix not in {"A", "CORE"} else "",
                    deducted_usd=amount, amount_usd=amount)
                    for client, suffix, amount in details])).insert()
            row_name = deposit.detail_rows[0].name
            for document, amount in ((first, 26), (second, 23.48)):
                deposit.append("targets", dict(historical_application=document.rows[0].name, amount_usd=amount, detail_row=row_name))
            if manual_remainder:
                deposit.append("targets", dict(complementary_item=core.name, amount_usd=core_amount, detail_row=deposit.detail_rows[2].name))
            deposit.save(); deposit.submit(); reconcile_deposit(deposit)
            deposit.reload()
            assert money(deposit.allocated_usd) == money(84.48 + core_amount), (deposit.allocated_usd,
                [(row.amount_usd, row.match_status, row.match_reason) for row in deposit.detail_rows])
            original = json.loads(deposit.allocation_detail)
            def signature(ledger):
                # Descriptive names may be completed after resolving the core
                # credit. Destinations, amounts and provenance must be identical.
                return [(entry.get("tipo"), entry.get("aplicacion_id"), entry.get("partida"), entry.get("periodo"),
                    entry.get("fila_detalle"), entry.get("empresa"), money(entry["importe_usd"]), entry.get("origen"),
                    entry.get("aplicaciones_fifo"), entry.get("reclassification_operation_id")) for entry in ledger]
            def legacy_ledger():
                saved = json.loads(deposit.allocation_detail)
                for entry in saved:
                    if entry.get("origen") == "Selección manual":
                        entry.pop("fila_detalle", None)
                frappe.db.set_value(deposit.doctype, deposit.name, "allocation_detail", json.dumps(saved))
                deposit.reload()
            legacy_ledger()
            reconcile_deposit(deposit); deposit.reload()
            assert signature(json.loads(deposit.allocation_detail)) == signature(original)
            assert money(deposit.allocated_usd) == money(84.48 + core_amount)
            legacy_ledger()
            late = application("LATE", clients[1] if manual_remainder else clients[2], periods[1] if manual_remainder else periods[0],
                               15.52, loan_suffix="AUTO" if manual_remainder else None)
            _reconcile_sources(company.name, preserve_deposits=True)
            deposit.reload()
            if manual_remainder:
                deposit.append("targets", dict(historical_application=late.rows[0].name, amount_usd=15.52,
                    detail_row=deposit.detail_rows[1].name))
                deposit.save()
            reconcile_deposit(deposit); deposit.reload()
            current = json.loads(deposit.allocation_detail)
            def portions(ledger):
                return {(entry.get("aplicacion_id") or "X:" + entry["partida"], entry.get("fila_detalle")):
                        money(entry["importe_usd"]) for entry in ledger}
            before, after = portions(original), portions(current)
            assert all(after[key] == amount for key, amount in before.items())
            assert money(deposit.allocated_usd) == money(100 + core_amount) and not money(deposit.unallocated_usd)
            assert after[(late.rows[0].name, deposit.detail_rows[1 if manual_remainder else 2].name)] == money(15.52)
            for entry in current:
                if entry.get("origen") == "Selección manual":
                    target = next(target for target in deposit.targets if entry.get("aplicacion_id")
                        and target.historical_application == entry["aplicacion_id"]
                        or entry.get("partida") and target.complementary_item == entry["partida"])
                    assert entry.get("fila_detalle") == target.detail_row
            assert document_state(fixed.reload()) == fixed_state
            reconcile_deposit(deposit); deposit.reload()
            assert portions(json.loads(deposit.allocation_detail)) == after
            assert signature(json.loads(deposit.allocation_detail)) == signature(current)
            assert document_state(fixed.reload()) == fixed_state
            return dict(legacy_manual_links_preserved=True, fifo_cash_preserved=True, metadata_completed=True,
                added_only_remaining_cash=15.52, fifo_plus_manual_remainder=manual_remainder,
                core_metadata_completed=bool(core_amount), repeated_reconciliation_preserved=True, other_deposit_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
