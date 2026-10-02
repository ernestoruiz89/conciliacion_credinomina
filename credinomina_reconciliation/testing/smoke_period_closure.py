"""Rollback-only closure with late deposits, shared capacity and stale totals."""
import json
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.patches.v1_0.index_period_closure_links import INDEXES, execute as ensure_indexes
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        # A second execution must not alter the migrated schema or commit data.
        for doctype, _, name in INDEXES:
            assert frappe.db.has_index("tab" + doctype, name), name
        ensure_indexes()
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime") as progress:
            marker = "CLOSE-" + frappe.generate_hash(length=8)
            companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
                "employer_code": marker + suffix, "payroll_frequency": "Mensual"}).insert() for suffix in ["A", "B", "Z"]]
            a, b, z = [company.name for company in companies]
            clients = [frappe.get_doc({"doctype": "CN Client", "employer": company.name,
                "client_number": marker + str(index), "client_name": marker + " Cliente " + str(index)}).insert()
                for index, company in enumerate(companies)]

            def application(company, client, mode, date, amount):
                period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company,
                    "payroll_month": date[:7] + "-01", "reconciliation_mode": mode})
                if mode == "Historica":
                    period.historical_scope = "Fecha exacta"
                    period.historical_application_date = date
                else:
                    period.collection_cycle = "Mensual"
                    period.append("collection_rows", {"row_key": marker + date,
                        "client_name": client.client_name, "client_number": client.client_number,
                        "loan_number": client.client_number + "-1", "expected_usd": amount,
                        "deducted_usd": amount, "deduction_status": "Deduccion total"})
                period.insert()
                source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company,
                    "source_file": f"/private/files/{marker}-{date}.csv", "status": "Importado", "currency": "USD"})
                source.append("rows", {"source_key": marker + company + date, "event_type": "Aplicacion",
                    "event_date": date, "currency": "USD", "amount": amount, "amount_usd": amount,
                    "effective": 1, "historical_period": period.name if mode == "Historica" else "",
                    "processing_route": mode, "client_name": client.client_name, "client_number": client.client_number,
                    "loan_number": client.client_number + "-1"})
                source.insert()
                return period, source

            historical, historical_source = application(a, clients[0], "Historica", "2025-04-30", 80)
            operative, operative_source = application(a, clients[0], "Operativa", "2026-09-30", 70)
            unrelated, unrelated_source = application(z, clients[2], "Historica", "2025-04-30", 17)
            generic = frappe.get_doc({"doctype": "CN Complementary Item", "employer": a,
                "category": "Ajuste de conciliación", "reference": marker + "-GENERIC",
                "posting_date": "2025-05-01", "currency": "USD", "amount": 500,
                "description": "Saldo compartido de prueba", "generic_distribution": 1})
            generic.append("distribution_companies", {"employer": b})
            generic.insert(); generic.flags.defer_reconciliation = True; generic.submit()

            def deposit(company, client, date, amount, period=None, item=None):
                document = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company,
                    "deposit_reference": marker + date + company + str(amount), "deposit_date": date,
                    "deposit_currency": "USD", "deposit_amount": amount,
                    "detail_file": f"/private/files/{marker}.xlsx", "detail_source_file": f"/private/files/{marker}.xlsx",
                    "detail_hash": marker})
                if period:
                    document.append("detail_periods", {"period": period.name})
                document.append("detail_rows", {"source_row": 2, "client_name": client.client_name,
                    "client_number": client.client_number, "loan_number": client.client_number + "-1",
                    "deducted_usd": amount})
                document.insert()
                if item:
                    document.append("targets", {"complementary_item": item.name, "amount_usd": amount,
                        "detail_row": document.detail_rows[0].name})
                    document.save()
                document.submit()
                return document

            historical_deposit = deposit(a, clients[0], "2025-06-15", 80, historical)
            operative_deposit = deposit(a, clients[0], "2026-11-15", 70, operative)
            generic_a = deposit(a, clients[0], "2025-07-01", 300, item=generic)
            generic_b = deposit(b, clients[1], "2025-08-01", 200, item=generic)
            _reconcile_sources(a)
            historical.reload(); historical_source.reload()
            assert historical.status == "Conciliado", historical.as_dict()
            untouched = [document_state(unrelated), document_state(unrelated_source)]
            # A new core amount must invalidate a previously reconciled status.
            historical_source.rows[0].amount = historical_source.rows[0].amount_usd = 90
            historical_source.save()
            try:
                close_period(historical.name, marker + "-FAIL")
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Closed using stale stored totals")
            historical.reload()
            assert historical.status != "Cerrado"
            assert not any(call.args[1].get("percent") == 100 for call in progress.call_args_list)
            historical_source.reload()
            historical_source.rows[0].amount = historical_source.rows[0].amount_usd = 80
            historical_source.save()
            elapsed = {}
            for period in [historical, operative]:
                started = perf_counter()
                result = close_period(period.name, marker + "-" + period.reconciliation_mode)
                elapsed[period.reconciliation_mode] = round(perf_counter() - started, 3)
                period.reload()
                assert result["status"] == period.status == "Cerrado"
                assert period.status_before_close == "Conciliado" and period.closed_by == "Administrator"
            for document in [unrelated, unrelated_source, historical_deposit, operative_deposit, generic_a, generic_b]:
                document.reload()
            assert [document_state(unrelated), document_state(unrelated_source)] == untouched
            assert historical.remitted_usd == 80 and operative.remitted_usd == 70
            assert all(document.result == "Conciliado" for document in [historical_deposit, operative_deposit, generic_a, generic_b])
            assigned = json.loads(generic_a.allocation_detail) + json.loads(generic_b.allocation_detail)
            assert sum(entry["importe_usd"] for entry in assigned if entry.get("partida") == generic.name) == 500
            historical.remark = "No debe permitirse"
            try:
                historical.save()
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Closed period remained editable")
            return {"both_modes_closed": True, "late_deposits_included": True,
                "stale_totals_rejected": True, "unrelated_company_unchanged": True,
                "shared_500_usd_capacity_preserved": True, "closed_period_locked": True,
                "closure_indexes_idempotent": True, "elapsed_seconds": elapsed, "rolled_back": True}
    finally:
        frappe.db.rollback()
