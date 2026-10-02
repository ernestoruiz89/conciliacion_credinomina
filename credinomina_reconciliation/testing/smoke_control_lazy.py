"""Exact summary/full parity and lazy payload size on the disposable site only."""
import json
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "LAZY-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
            history, operative = [frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": month, "reconciliation_mode": mode, "collection_cycle": "Mensual"}).insert()
                for month, mode in (("2025-04-01", "Historica"), ("2026-09-01", "Operativa"))]
            source = marker + "-IMPORT"
            frappe.get_doc({"doctype": "CN Accounting Import", "name": source, "employer": employer.name,
                            "status": "Importado", "historical_backfill": 1}).db_insert()
            for index in range(600):
                frappe.get_doc({"doctype": "CN Source Row", "name": f"{marker}-H{index}",
                    "parent": source, "parenttype": "CN Accounting Import", "parentfield": "rows", "idx": index + 1,
                    "historical_period": history.name, "event_date": "2025-04-20", "event_type": "Aplicacion", "effective": 1,
                    "client_name": f"Persona {index}", "client_number": str(index), "loan_number": f"{index}-1",
                    "amount": 10, "amount_usd": 10, "net_applied_usd": 10, "currency": "USD",
                    "historical_remitted_usd": 5, "historical_balance_usd": 5,
                    "deposit_match_status": "Diferencia de importe" if index % 2 else "Depósito parcial",
                    "deposit_match_reason": "Motivo extenso de revisión. " * 10}).db_insert()
                frappe.get_doc({"doctype": "CN Collection Row", "name": f"{marker}-C{index}",
                    "parent": operative.name, "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows", "idx": index + 1,
                    "client_name": f"Persona {index}", "loan_number": f"{index}-1", "row_key": str(index),
                    "expected_usd": 10.005, "deducted_usd": 5.005,
                    "deduction_status": "Deduccion parcial" if index % 2 else "Pendiente de detalle",
                    "application_status": "Diferencia cambiaria en revision" if index % 3 else "Diferencia aplicacion vs deposito",
                    "comments": "Observación extensa por cliente. " * 10}).db_insert()
            frappe.db.set_value(history.doctype, history.name, {"applied_usd": 6000, "remitted_usd": 3000})
            frappe.db.set_value(operative.doctype, operative.name, {"expected_usd": 6000, "deducted_usd": 3000, "applied_usd": 3000})
            started = perf_counter()
            summary = control.get_control_data("Todos", employer.name)
            summary_seconds = perf_counter() - started
            started = perf_counter()
            full = control._build_control_data("Todos", employer.name, full_export=True)
            full_seconds = perf_counter() - started
            assert summary["totals"] == full["totals"], (summary["totals"], full["totals"])
            assert summary["work_items"] == full["work_items"]
            for light, complete in zip(summary["periods"], full["periods"]):
                for field in ("control_state", "worker_gap_usd", "pending_detail_usd", "historical_pending_usd"):
                    assert light[field] == complete[field], (field, light[field], complete[field])
                assert "rows" not in light and "historical_rows" not in light
                detail = control.get_period_detail(light["name"])
                assert detail["rows"] == complete["rows"]
                assert detail["historical_rows"] == complete["historical_rows"]
            # Neither permissions failure nor a modal should trigger a full dashboard read.
            with patch.object(control, "_available_years", side_effect=AssertionError("Modal loaded year catalog")), \
                 patch.object(control, "get_cash_deposits", side_effect=AssertionError("Period modal loaded all deposits")):
                assert len(control.get_period_detail(history.name)["historical_rows"]) == 600
            summary_bytes = len(json.dumps(summary, default=str).encode())
            full_bytes = len(json.dumps(full, default=str).encode())
            assert summary_bytes < full_bytes / 10, (summary_bytes, full_bytes)
            return {"rows": 1200, "totals_and_work_items_equal": True, "lazy_details_complete": True,
                    "summary_bytes": summary_bytes, "full_bytes": full_bytes,
                    "summary_seconds": round(summary_seconds, 3), "full_seconds": round(full_seconds, 3),
                    "rolled_back": True}
    finally:
        frappe.db.rollback()
