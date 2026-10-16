"""Monthly first-stage workflow; each period is an independent transaction."""
import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.historical import OPERATIVE_START
from credinomina_reconciliation.rounding import money_float, sum_money

PERIOD = "CN Reconciliation Period"
IMPORT = "CN Accounting Import"
IMPORTED = ["Importado", "Importado con excepciones"]


def _month(value):
    if not value:
        frappe.throw(_("Seleccione el mes de cobranza."))
    return getdate(value).replace(day=1)


def _permissions():
    frappe.has_permission(PERIOD, "write", throw=True)
    frappe.has_permission(IMPORT, "write", throw=True)


def _period(name, payroll_month=None):
    _permissions()
    period = frappe.get_doc(PERIOD, name)
    period.check_permission("write")
    if payroll_month and _month(period.payroll_month) != _month(payroll_month):
        frappe.throw(_("El período ya no pertenece al mes seleccionado. Actualice la lista."))
    return period


def _unavailable(period):
    if period.docstatus == 2:
        return "Cancelado"
    if period.status == "Cerrado":
        return "Cerrado"
    if period.reconciliation_mode != "Operativa" or period.get("no_collection"):
        return "No aplica conciliación 1"
    if not period.collection_rows:
        return "Sin cobranza"
    return ""


def _assigned_count(period):
    return frappe.db.sql("""select count(*) from `tabCN Source Row` r
        inner join `tabCN Accounting Import` i on i.name = r.parent
        where r.parenttype = 'CN Accounting Import' and r.event_type = 'Aplicacion'
        and r.effective = 1 and i.docstatus < 2 and i.status in %s and i.employer = %s
        and coalesce(nullif(r.historical_period, ''), i.historical_period) = %s""",
        (tuple(IMPORTED), period.employer, period.name))[0][0]


@frappe.whitelist()
def list_periods(payroll_month):
    _permissions()
    # No current-list filters, page size or selection: include the whole month
    # within the caller's document permissions, including both payroll cycles.
    return frappe.get_list(PERIOD, filters={"payroll_month": _month(payroll_month), "docstatus": ["<", 2]},
        fields=["name", "employer", "collection_cycle"], order_by="employer asc, name asc", limit_page_length=0)


@frappe.whitelist(methods=["POST"])
def reconcile_period(period_name, payroll_month):
    period = _period(period_name, payroll_month)
    state = _unavailable(period)
    result = dict(name=period.name, employer=period.employer, state=state, can_assign=False)
    if state:
        return result
    assigned = _assigned_count(period)
    if not assigned:
        # Also remove stale payroll matches left by the earlier automatic
        # matcher, even when the company has no assigned applications at all.
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
        _reconcile_sources(period.employer, preserve_deposits=True, period_name=period.name)
        return dict(result, state="Sin aplicaciones asignadas", assigned=0, can_assign=True)
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import reconcile_first
    feedback = reconcile_first(period.name)
    conforming = feedback["conforming"] == feedback["reviewed"] and not feedback["missing_basis"]
    return dict(result, state="Conforme" if conforming else "Con diferencias", assigned=assigned,
        reviewed=feedback["reviewed"], conforming=feedback["conforming"], differences=feedback["differences"],
        missing_basis=feedback["missing_basis"])


def _eligible(row, document):
    return (row.event_type == "Aplicacion" and row.effective and row.currency == "USD"
            and not (row.historical_period or document.historical_period)
            and row.event_date and getdate(row.event_date) >= OPERATIVE_START
            and row.processing_route != "Historica")


@frappe.whitelist()
def get_unassigned_imports(period_name):
    period = _period(period_name)
    if _unavailable(period):
        frappe.throw(_("El período debe estar abierto, ser operativo y tener cobranza."))
    imports = frappe.get_list(IMPORT, filters={"employer": period.employer, "status": ["in", IMPORTED],
        "docstatus": ["<", 2], "historical_period": ["is", "not set"]}, pluck="name", limit_page_length=0)
    candidates = []
    for name in imports:
        document = frappe.get_doc(IMPORT, name)
        if not frappe.has_permission(IMPORT, "write", doc=document):
            continue
        rows = [row for row in document.rows if _eligible(row, document)]
        if not rows:
            continue
        dates = sorted(str(getdate(row.event_date)) for row in rows)
        candidates.append(dict(name=name, modified=str(document.modified), applications=[row.name for row in rows],
            count=len(rows), start_date=dates[0], end_date=dates[-1],
            amount_usd=money_float(sum_money(row.amount_usd for row in rows)),
            same_month=all(_month(row.event_date) == _month(period.payroll_month) for row in rows)))
    candidates.sort(key=lambda row: (not row["same_month"], row["start_date"], row["name"]))
    return dict(period=period.name, employer=period.employer, payroll_month=str(period.payroll_month),
                modified=str(period.modified), imports=candidates)


@frappe.whitelist(methods=["POST"])
def assign_and_reconcile(period_name, modified, selections):
    period = _period(period_name)
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    lock_cash_pool(reconciliation_companies(period.employer))
    period = frappe.get_doc(PERIOD, period_name, for_update=True)
    period.check_permission("write")
    if str(period.modified) != str(modified) or _unavailable(period):
        frappe.throw(_("El período cambió o ya no admite la conciliación 1. Actualice el resultado."))
    selections = frappe.parse_json(selections) if isinstance(selections, str) else selections
    if not isinstance(selections, list) or not 1 <= len(selections) <= 200:
        frappe.throw(_("Seleccione entre 1 y 200 importaciones."))
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"] for row in selections):
        frappe.throw(_("La selección de importaciones no es válida."))
    if len({row["name"] for row in selections}) != len(selections):
        frappe.throw(_("No repita importaciones en la selección."))
    count = 0
    for selection in sorted(selections, key=lambda row: row["name"]):
        document = frappe.get_doc(IMPORT, selection["name"], for_update=True)
        document.check_permission("write")
        if document.employer != period.employer or document.docstatus == 2 or document.status not in IMPORTED:
            frappe.throw(_("La importación no pertenece a esta empresa o no está disponible."))
        if str(document.modified) != str(selection.get("modified")):
            frappe.throw(_("La importación {0} cambió. Vuelva a abrir el modal.").format(document.name))
        eligible = [row for row in document.rows if _eligible(row, document)]
        names = selection.get("applications")
        if (not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names) or set(names) != {row.name for row in eligible}):
            frappe.throw(_("Las aplicaciones pendientes de {0} cambiaron. Vuelva a abrir el modal.").format(document.name))
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _source_linked_periods
        if any(set(_source_linked_periods(row)) - {period.name} for row in eligible):
            frappe.throw(_("La importación {0} conserva vínculos de cobranza con otro período. Revise o concilie ese período antes de asignarla aquí.").format(document.name))
        # Assign exactly the applications the operator reviewed. Existing row
        # assignments to other periods are never overwritten.
        for row in eligible:
            row.historical_period = period.name
            row.processing_route = "Operativa"
        if len(eligible) == sum(row.event_type == "Aplicacion" for row in document.rows):
            document.historical_period = period.name
        document.save()  # Includes cash, adjustment, evidence and closed-period guards.
        document.add_comment("Comment", _("Asignadas {0} aplicaciones al período {1} desde la conciliación mensual.").format(len(eligible), period.name))
        count += len(eligible)
    # Assignment and reconciliation succeed or roll back together in this POST.
    result = reconcile_period(period.name, str(period.payroll_month))
    return dict(result, assigned_now=count)
