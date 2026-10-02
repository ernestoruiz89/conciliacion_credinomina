"""Restore physical ledger identities; never reimport/reconcile during migration."""
import frappe

from credinomina_reconciliation.accounting_origin_repair import repair_accounting_origins


def execute():
    result = repair_accounting_origins(dry_run=False)
    if result["issues"]:
        frappe.log_error(title="Revisar origen de importaciones contables",
                         message=frappe.as_json(result))
    print("Identidades contables reparadas: {0} filas en {1} importaciones. Casos para revisión: {2}.".format(
        result["rows_changed"], result["imports_changed"], len(result["issues"])))
