"""Rollback-only periodless and shared multi-client/company deposit destinations."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.remittance_selection import get_pending_targets
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            marker = "GENERIC-" + frappe.generate_hash(length=8)
            companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
                "employer_code": marker + suffix, "payroll_frequency": "Mensual"}).insert() for suffix in ["A", "B"]]
            a, b = [company.name for company in companies]
            clients = []
            for company, suffix in [(a, "1"), (a, "2"), (b, "3")]:
                clients.append(frappe.get_doc({"doctype": "CN Client", "employer": company,
                    "client_number": marker + suffix, "client_name": marker + " CLIENTE " + suffix}).insert())
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": a,
                "payroll_month": "2025-05-01", "reconciliation_mode": "Historica", "historical_scope": "Fecha exacta",
                "historical_application_date": "2025-05-30"}).insert()
            source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": a,
                "source_file": f"/private/files/{marker}.csv", "status": "Importado", "currency": "USD"})
            source.append("rows", {"source_key": marker, "event_type": "Aplicacion", "event_date": "2025-05-30",
                "currency": "USD", "amount": 63.73, "amount_usd": 63.73, "effective": 1,
                "historical_period": period.name, "processing_route": "Historica", "client_number": clients[0].client_number,
                "client_name": clients[0].client_name, "loan_number": marker + "-1"})
            source.insert()
            _reconcile_sources(a); source.reload()

            def complement(amount, generic=False):
                item = frappe.get_doc({"doctype": "CN Complementary Item", "employer": a, "category": "Ajuste de conciliación",
                    "reference": marker + "-UNRELATED", "posting_date": "2025-05-15", "currency": "USD", "amount": amount,
                    "description": "Prueba de partida con saldo único", "generic_distribution": int(generic)})
                if generic:
                    item.append("distribution_companies", {"employer": b})
                else:
                    item.client_number = clients[0].client_number
                    item.loan_number = marker + "-1"
                item.insert(); item.flags.defer_reconciliation = True; item.submit()
                return item

            specific = complement(63.74)
            generic = complement(500, True)

            def deposit(company, portions, item, reference, core=False):
                document = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company,
                    "deposit_reference": marker + reference, "deposit_date": "2025-06-30", "deposit_currency": "USD",
                    "deposit_amount": sum(amount for _, amount in portions), "detail_file": f"/private/files/{marker}.xlsx",
                    "detail_source_file": f"/private/files/{marker}.xlsx", "detail_hash": marker})
                if company == a:
                    document.append("detail_periods", {"period": period.name})
                for index, (client, amount) in enumerate(portions):
                    document.append("detail_rows", {"source_row": index + 2, "employer": company,
                        "client_number": client.client_number, "client_name": client.client_name,
                        "loan_number": marker + "-1" if core else client.client_number + "-1", "deducted_usd": amount})
                document.insert()
                if core:
                    document.append("targets", {"historical_application": source.rows[0].name, "amount_usd": 63.73,
                        "detail_row": document.detail_rows[0].name})
                for row, (_, amount) in zip(document.detail_rows, portions):
                    document.append("targets", {"complementary_item": item.name, "amount_usd": amount - 63.73 if core else amount,
                        "detail_row": row.name})
                document.save(); document.submit()
                return document

            original = deposit(a, [(clients[0], 127.47)], specific, "-SPECIFIC", True)
            reconcile_deposit(original); original.reload()
            assert original.result == "Conciliado" and original.detail_rows[0].match_status == "Conciliada", original.as_dict()
            first = deposit(a, [(clients[0], 100), (clients[1], 150)], generic, "-GEN1")
            second = deposit(b, [(clients[2], 250)], generic, "-GEN2")
            feedback = reconcile_deposit(first); first.reload()
            assert feedback["detail_matched"] == 2 and feedback["detail_pending"] == 0, feedback
            snapshot = document_state(first)
            pending = get_pending_targets(second.name)
            generic_row = next(row for row in pending["rows"] if row.get("complementary_item") == generic.name) if pending["rows"] else None
            # This deposit already has its explicit target, so the picker must not duplicate it.
            assert generic_row is None
            reconcile_deposit(second); second.reload(); first.reload()
            assert document_state(first) == snapshot, "Other company deposit was changed"
            assert first.result == second.result == "Conciliado"
            assert all(row.match_status == "Conciliada" for row in first.detail_rows)
            assert second.detail_rows[0].employer == b
            entries = json.loads(first.allocation_detail) + json.loads(second.allocation_detail)
            generic_entries = [entry for entry in entries if entry.get("partida") == generic.name]
            assert sum(entry["importe_usd"] for entry in generic_entries) == 500
            assert {entry["empresa"] for entry in generic_entries} == {a, b}
            assert all(entry.get("fila_detalle") and entry.get("nro_cliente") for entry in generic_entries)
            third = deposit(b, [(clients[2], 0.01)], generic, "-OVER")
            reconcile_deposit(third); third.reload()
            assert third.allocated_usd == 0 and third.result != "Conciliado", third.as_dict()
            assert third.targets[0].result == "Excede cobranza"
            reconcile_deposit(second); second.reload(); first.reload()
            assert second.allocated_usd == 250 and document_state(first) == snapshot
            _reconcile_sources(b)
            first.reload(); second.reload(); original.reload(); third.reload()
            assert first.result == second.result == original.result == "Conciliado"
            assert first.allocated_usd + second.allocated_usd == 500 and third.allocated_usd == 0
            assert {field: source.rows[0].get(field) for field in ["amount", "amount_usd"]} == {"amount": 63.73, "amount_usd": 63.73}
            frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
            from credinomina_reconciliation.complementary_distribution import guard_closed_distributions
            try:
                guard_closed_distributions(generic)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Shared complement ignored a closed period of its deposit")
            return {"periodless_identified_item": "OK", "shared_500_usd_three_clients_two_companies": "OK",
                    "other_deposits_unchanged": "OK", "over_capacity_cent_rejected": "OK", "company_reconciliation_capacity": "OK",
                    "closed_period_guard": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
