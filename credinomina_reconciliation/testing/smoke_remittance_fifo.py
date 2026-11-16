"""Rollback-only FIFO with repeated detail rows, fixed cash and both modes."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            for mode, month in [("Historica", "2025-04"), ("Operativa", "2026-09")]:
                marker = "FIFO-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                    "client_number": marker, "client_name": "Cliente FIFO"}).insert()
                periods, sources = [], []
                for day, amount in [("15", 40), ("30", 60)]:
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                        "payroll_month": month + "-01", "reconciliation_mode": mode,
                        "application_basis": "Cobranza" if mode == "Operativa" else "",
                        "collection_cycle": "Primera quincena" if day == "15" else "Segunda quincena"})
                    if mode == "Historica":
                        period.historical_scope = "Fecha exacta"
                        period.historical_application_date = month + "-" + day
                    else:
                        period.append("collection_rows", {"row_key": marker + day, "client": client.name,
                            "client_name": client.client_name, "client_number": marker, "loan_number": marker + "-1",
                            "expected_usd": amount})
                    period.insert()
                    imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                        "source_file": f"/private/files/{marker}-{day}.csv", "status": "Importado", "currency": "USD"})
                    imported.append("rows", {"source_key": marker + day, "event_type": "Aplicacion",
                        "event_date": month + "-" + day, "currency": "USD", "amount": amount,
                        "amount_usd": amount, "effective": 1, "client": client.name,
                        "client_number": marker, "client_name": client.client_name, "loan_number": marker + "-1",
                        "historical_period": period.name if mode == "Historica" else "", "processing_route": mode})
                    imported.insert()
                    periods.append(period)
                    sources.append(imported)
                _reconcile_sources(employer.name)
                for doc in sources + periods:
                    doc.reload()
                if mode == "Operativa":
                    # Operational applications require an explicit period;
                    # matching client/credit and date alone must not assign one.
                    for imported, period in zip(sources, periods):
                        row = imported.rows[0]
                        assert row.match_status == "Sin coincidencia"
                        assert "sin período operativo asignado" in row.match_reason
                        assert not row.collection_row_id and not row.collection_period
                        assert period.applied_usd == period.remitted_usd == 0
                        imported.historical_period = period.name
                        row.historical_period = period.name
                        imported.save()
                    _reconcile_sources(employer.name)
                    for imported, period in zip(sources, periods):
                        imported.reload(); period.reload()
                        row = imported.rows[0]
                        assert row.match_status == "Conciliado", row.match_reason
                        assert row.collection_period == period.name
                        assert row.collection_row_id == period.collection_rows[0].name

                def new_deposit(suffix, amount):
                    return frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                        "deposit_reference": marker + suffix, "deposit_date": month + "-30",
                        "deposit_currency": "USD", "deposit_amount": amount})

                fixed = new_deposit("FIXED", 10)
                # Explicit period assignments keep application-level cash links
                # in both modes, independently of the operative payroll match.
                fixed.append("targets", {"historical_application": sources[0].rows[0].name, "amount_usd": 10})
                fixed.insert(); fixed.submit()
                reconcile_deposit(fixed)
                fixed.reload()
                assert fixed.allocated_usd == 10 and fixed.unallocated_usd == 0, fixed.result
                fixed_journal = json.loads(fixed.allocation_detail)
                assert len(fixed_journal) == 1 and fixed_journal[0]["importe_usd"] == 10
                assert fixed_journal[0]["aplicacion_id"] == sources[0].rows[0].name
                fixed_state = document_state(fixed)
                deposit = new_deposit("FIFO", 55.05)
                deposit.apply_fifo = 1
                deposit.detail_file = deposit.detail_source_file = "/private/files/" + marker + ".csv"
                deposit.detail_hash = marker
                for idx, amount in enumerate((33.03, 22.02), 2):
                    deposit.append("detail_rows", {"source_row": idx, "client": client.name, "client_number": marker,
                        "client_name": client.client_name, "loan_number": marker + "-1", "deducted_usd": amount})
                deposit.insert(); deposit.submit()
                # The public flow rejects missing periods before recalculating
                # this deposit or touching already confirmed cash.
                pending_state = document_state(deposit)
                try:
                    reconcile_deposit(deposit)
                except frappe.ValidationError as error:
                    assert "al menos un período" in str(error), str(error)
                else:
                    raise AssertionError("FIFO accepted detail without selected periods")
                deposit.reload(); fixed.reload()
                assert pending_state == document_state(deposit)
                assert deposit.allocated_usd == 0 and deposit.unallocated_usd == 55.05
                assert json.loads(deposit.allocation_detail) == []
                assert fixed_state == document_state(fixed)
                # Reversed selected periods prove that application dates set the order.
                for period in reversed(periods):
                    deposit.append("detail_periods", {"period": period.name})
                deposit.save()
                reconcile_deposit(deposit)
                deposit.reload(); fixed.reload()
                assert fixed_state == document_state(fixed), "Another deposit was changed"
                assert deposit.result == "Conciliado", deposit.as_dict()
                assert deposit.allocated_usd == 55.05 and deposit.unallocated_usd == 0
                assert all(row.match_status == "Conciliada" and row.pending_usd == 0 for row in deposit.detail_rows)
                detail = json.loads(deposit.allocation_detail)
                assert [entry["importe_usd"] for entry in detail] == [30, 3.03, 22.02], detail
                assert all(entry["origen"] == "Automática FIFO" and entry["fila_detalle"] for entry in detail)
                assert [entry["aplicaciones_fifo"][0]["event_date"] for entry in detail] == [month + "-15", month + "-30", month + "-30"]
                for period, expected in zip(periods, (40, 25.05)):
                    period.reload()
                    assert period.remitted_usd == expected, period.as_dict()
                reconcile_deposit(deposit)
                deposit.reload(); fixed.reload()
                assert json.loads(deposit.allocation_detail) == detail, "Reconciliation duplicated or changed cash"
                assert fixed_state == document_state(fixed)

                # A later import must refresh applications without replaying
                # FIFO against a now-closed older period, including a deposit
                # shared between the closed period and an open period.
                frappe.db.set_value(periods[0].doctype, periods[0].name, "status", "Cerrado")
                periods[0].reload()
                closed_state = document_state(periods[0])
                deposit_state = document_state(deposit)
                later_month = "2025-06" if mode == "Historica" else "2026-10"
                later = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                    "payroll_month": later_month + "-01", "reconciliation_mode": mode,
                    "application_basis": "Cobranza" if mode == "Operativa" else "",
                    "collection_cycle": "Primera quincena", "historical_scope": "Fecha exacta",
                    "historical_application_date": later_month + "-15"})
                if mode == "Operativa":
                    later.append("collection_rows", {"row_key": marker + "LATER", "client": client.name,
                        "client_name": client.client_name, "client_number": marker, "loan_number": marker + "-1",
                        "expected_usd": 25})
                later.insert()
                imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                    "source_file": f"/private/files/{marker}-later.csv", "status": "Importado", "currency": "USD",
                    "historical_period": later.name})
                imported.append("rows", {"source_key": marker + "LATER", "event_type": "Aplicacion",
                    "event_date": later_month + "-15", "currency": "USD", "amount": 25,
                    "amount_usd": 25, "effective": 1, "client": client.name, "client_number": marker,
                    "client_name": client.client_name, "loan_number": marker + "-1",
                    "historical_period": later.name, "processing_route": mode})
                imported.insert()
                result = _reconcile_sources(employer.name, preserve_deposits=True)
                assert result["preserved_deposits"] == 2
                for doc in (deposit, fixed, periods[0], later, imported):
                    doc.reload()
                assert deposit_state == document_state(deposit), "Import redistributed an existing FIFO deposit"
                assert fixed_state == document_state(fixed)
                assert closed_state == document_state(periods[0]), "Import changed a closed period"
                assert later.applied_usd == 25 and later.remitted_usd == 0
                assert imported.rows[0].match_status == "Conciliado"

                # Preserving cash does not disable the accounting close guard.
                frappe.db.savepoint("fifo_closed_guard")
                try:
                    frappe.db.set_value("CN Source Row", sources[0].rows[0].name, "amount", 41)
                    try:
                        _reconcile_sources(employer.name, preserve_deposits=True)
                    except frappe.ValidationError:
                        pass
                    else:
                        raise AssertionError("Changed closed application was accepted")
                finally:
                    frappe.db.rollback(save_point="fifo_closed_guard")
            return {"historical_and_operative": True, "repeated_rows": True, "oldest_first": True,
                    "operative_period_required": True, "unselected_fifo_rejected_without_changes": True,
                    "other_deposit_preserved": True, "idempotent": True, "later_import_preserves_closed_period": True,
                    "closed_changes_still_rejected": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
