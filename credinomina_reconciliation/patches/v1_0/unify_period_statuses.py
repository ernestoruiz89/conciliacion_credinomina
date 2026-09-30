"""Rename period states without recalculating balances or reopening closed periods."""
import frappe

from credinomina_reconciliation.rounding import money


RENAMES = {
    "Cobranza cargada": "Pendiente",
    "Detalle empresa cargado": "Pendiente",
    "Deduccion conciliada": "Pendiente",
    "Deposito conciliado": "Conciliado",
    "Historico pendiente": "Pendiente",
    "Historico parcial": "Parcial",
    "Historico con excedente": "Con excedente",
    "Historico conciliado": "Conciliado",
}
OLD_OPERATIVE_STAGES = {"Cobranza cargada", "Detalle empresa cargado", "Deduccion conciliada"}


def execute():
    for period in frappe.get_all("CN Reconciliation Period",
            fields=["name", "status", "status_before_close", "remitted_usd"], limit_page_length=0):
        updates = {}
        for field in ("status", "status_before_close"):
            old = period.get(field)
            if old in RENAMES:
                updates[field] = ("Parcial" if old in OLD_OPERATIVE_STAGES and money(period.remitted_usd) > 0
                                  else RENAMES[old])
        if updates:
            frappe.db.set_value("CN Reconciliation Period", period.name, updates, update_modified=False)
    frappe.clear_cache(doctype="CN Reconciliation Period")
