"""Repair historical cash statuses overwritten by the operative balance pass."""
import frappe

from credinomina_reconciliation.historical_status_repair import repair_historical_statuses


def execute():
    result = repair_historical_statuses(dry_run=False)
    if result["unverified_rows"]:
        frappe.log_error(title="Revisar estados de aplicaciones históricas", message=frappe.as_json(result))
    print(f"Estados históricos restaurados: {result['rows_repaired']} filas en {result['imports_repaired']} importaciones.")
