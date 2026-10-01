"""Rename monthly portfolio cuts and let Frappe update every Link reference."""

from uuid import uuid4

import frappe
from frappe import _

from credinomina_reconciliation.portfolio_naming import (
    DOCTYPE, portfolio_snapshot_name,
)


IMPORTED_STATES = {"Importado", "Importado con alertas"}


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    if frappe.get_meta(DOCTYPE).has_field("naming_series"):
        frappe.throw(_("Sincronice CN Credit Portfolio Snapshot antes de renombrar los cortes."))
    rows = frappe.get_all(
        DOCTYPE,
        fields=["name", "report_date", "status", "creation"],
        order_by="creation asc, name asc",
        limit_page_length=0,
    )
    by_month = {}
    for row in rows:
        if not row.report_date:
            if row.status in IMPORTED_STATES:
                frappe.throw(_("El corte importado {0} no tiene fecha de reporte; complétela antes de migrar.").format(row.name))
            continue
        new_name = portfolio_snapshot_name(row.report_date)
        by_month.setdefault(new_name, []).append(row)

    selected = []
    for new_name, group in by_month.items():
        imported = [row for row in group if row.status in IMPORTED_STATES]
        if len(imported) > 1:
            frappe.throw(_(
                "Hay varios cortes importados para {0}. Resuelva los duplicados antes de migrar."
            ).format(new_name))
        # Prefer the imported cut when old drafts for that same month also exist.
        selected.append((imported[0] if imported else group[0], new_name))

    renames = [(row.name, new_name) for row, new_name in selected if row.name != new_name]
    old_names = {old for old, _new in renames}
    for old, new in renames:
        if frappe.db.exists(DOCTYPE, new) and new not in old_names:
            frappe.throw(_("No se puede renombrar {0}: el identificador {1} ya está ocupado.").format(old, new))

    # Free every destination first, so a legacy record already using another
    # cut's target name cannot break the migration midway.
    occupied = {row.name for row in rows}
    staged = []
    for old, new in renames:
        temporary = "CARTERA-MIG-" + uuid4().hex
        while temporary in occupied:
            temporary = "CARTERA-MIG-" + uuid4().hex
        occupied.add(temporary)
        frappe.rename_doc(DOCTYPE, old, temporary, force=True, merge=False,
                          show_alert=False, rebuild_search=False)
        staged.append((temporary, new))
    for temporary, new in staged:
        frappe.rename_doc(DOCTYPE, temporary, new, force=True, merge=False,
                          show_alert=False, rebuild_search=False)
    frappe.clear_cache()
