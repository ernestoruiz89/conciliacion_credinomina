"""Reconcile a reloaded source with read-only evidence from related imports."""
import frappe
from frappe import _


def linked_periods(document):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    names = {document.historical_period}
    for row in document.rows or []:
        names.update(engine._source_linked_periods(row))
    return sorted(names - {None, ""})


def load_context(import_name, employer, companies, previous_periods):
    from credinomina_reconciliation.complementary_cancellation import _load_imports
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    document = frappe.get_doc("CN Accounting Import", import_name)
    document.check_permission("write")
    if document.employer != employer:
        frappe.throw(_("La importación pertenece a otra empresa."))
    names = set(previous_periods) | set(linked_periods(document))
    # Old JSON may still name a renamed period. Live collection-row links above
    # resolve its current parent; a deleted/stale name is not a new write scope.
    existing = frappe.get_all("CN Reconciliation Period", filters={"name": ["in", sorted(names)]},
                              pluck="name", limit_page_length=0) if names else []
    periods = [frappe.get_doc("CN Reconciliation Period", name) for name in sorted(existing)]
    for period in periods:
        period.check_permission("write")
        if period.employer not in companies:
            frappe.throw(_("El período {0} pertenece a otra empresa.").format(period.name))
        if period.status == "Cerrado":
            frappe.throw(_("Reabra el período {0} antes de recargar sus movimientos.").format(period.name))
    active = {period.name for period in periods}
    fixed = []
    for other in _load_imports(periods, [], [], set(companies)) if periods else []:
        if other.name == document.name:
            continue
        for original in other.rows:
            assigned = original.historical_period or other.historical_period
            if original.event_type != "Aplicacion" or not (
                assigned in active or active.intersection(engine._source_linked_periods(original))
            ):
                continue
            # Copies contribute to balances and reserve their existing payroll
            # matches. Neither their identity nor their parent is saved.
            row = frappe._dict(original.as_dict())
            row._source_import, row._source_employer = other.name, other.employer
            row._historical_backfill = row.processing_route == "Historica" or (
                row.processing_route != "Operativa" and bool(other.historical_backfill or other.historical_period))
            if not engine._application_allocations(row):
                row.historical_period = assigned or ""
            fixed.append(row)
    return document, periods, fixed
