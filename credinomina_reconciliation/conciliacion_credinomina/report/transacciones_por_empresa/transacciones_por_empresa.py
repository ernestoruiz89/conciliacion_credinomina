import frappe
from frappe import _
from frappe.utils import cint, nowdate

from credinomina_reconciliation.company_transactions import (
    IMPORTED, application_state, deposit_state, monthly_counts,
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    raw_year = str(filters.get("year") or nowdate()[:4])
    if not raw_year.isdigit() or not 1900 <= int(raw_year) <= 9998:
        frappe.throw(_("Indique un año válido entre 1900 y 9998."))
    year = int(raw_year)
    kind = filters.get("transaction_type") or "Aplicaciones"
    if kind not in {"Aplicaciones", "Depósitos"}:
        frappe.throw(_("Seleccione Aplicaciones o Depósitos."))
    drafts = bool(cint(filters.get("include_drafts")))
    doctype = "CN Accounting Import" if kind == "Aplicaciones" else "CN Remittance Allocation"
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("No tiene permiso para consultar estas transacciones."), frappe.PermissionError)
    dates = [f"{year}-01-01", f"{year}-12-31"]
    scoped = {"employer": filters.employer} if filters.get("employer") else {}
    records = []
    if kind == "Aplicaciones":
        # Accounting imports are not submittable; their business status defines drafts.
        statuses = sorted(IMPORTED | ({"Borrador"} if drafts else set()))
        parents = frappe.get_list(doctype, filters={**scoped, "status": ["in", statuses], "docstatus": ["!=", 2]},
            fields=["name", "employer", "status"], limit_page_length=0)
        by_name = {parent.name: parent for parent in parents}
        names = list(by_name)
        for offset in range(0, len(names), 500):
            # Access child rows only through permission-filtered parent records.
            rows = frappe.get_all("CN Source Row", filters={"parent": ["in", names[offset:offset + 500]],
                "parenttype": doctype, "parentfield": "rows", "event_type": "Aplicacion",
                "event_date": ["between", dates], "docstatus": ["!=", 2]},
                fields=["name", "parent", "event_date", "effective", "match_status", "deposit_match_status",
                        "historical_remitted_usd", "application_adjustment_usd"], limit_page_length=0)
            for row in rows:
                parent = by_name[row.parent]
                records.append({"employer": parent.employer, "date": row.event_date, "state": application_state(row, parent)})
    else:
        rows = frappe.get_list(doctype, filters={**scoped, "docstatus": ["in", [0, 1]] if drafts else 1,
            "deposit_date": ["between", dates]}, fields=["name", "employer", "deposit_date", "docstatus", "result",
                "amount_usd", "allocated_usd", "justified_surplus_usd", "unclassified_usd"], limit_page_length=0)
        records = [{"employer": row.employer, "date": row.deposit_date, "state": deposit_state(row)} for row in rows]
    data = monthly_counts(records, year)
    message = _("Verde: todas las transacciones conciliadas. Naranja: conciliación parcial o mezcla de conciliadas y pendientes. "
                "Rojo: todas pendientes. —: sin transacciones. Pase el cursor sobre un mes para ver el desglose. "
                "El color refleja el estado actual, no un corte histórico; no se ejecuta ninguna conciliación.")
    message += " " + (_("Aplicaciones: una transacción por fila de aplicación, según Fecha del movimiento; no por documento ni por período. "
                        "Conciliar con la cobranza no equivale a conciliar el depósito. Importaciones fallidas y registros cancelados se excluyen.")
        if kind == "Aplicaciones" else _("Depósitos: un registro por depósito, según Fecha del depósito y empresa pagadora, aunque cubra otras empresas o períodos. "
            "Un saldo a favor completamente documentado puede estar conciliado aunque su devolución siga pendiente. Se excluyen los cancelados."))
    if drafts:
        message += " " + _("Los borradores se incluyen como pendientes; en aplicaciones se utiliza el estado Borrador de la importación.")
    summary = [{"label": _(label), "value": sum(record["state"] == state for record in records), "datatype": "Int", "indicator": color}
        for state, label, color in (("Conciliado", "Conciliadas", "Green"), ("Parcial", "Parcialmente conciliadas", "Orange"),
                                    ("Pendiente", "Pendientes", "Red"))]
    return columns(), data, message, None, summary


def columns():
    months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sept", "Oct", "Nov", "Dic"]
    return [{"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 240},
            *[{"fieldname": f"m{index:02}", "label": _(label), "fieldtype": "Int", "width": 70}
              for index, label in enumerate(months, 1)],
            {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 85}]
