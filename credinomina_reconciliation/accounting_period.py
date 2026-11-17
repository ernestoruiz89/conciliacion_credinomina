"""Create an empty draft period from an accounting import, without reconciling."""

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.cadence import EXACT_DATE, FIRST_HALF, MONTHLY, SECOND_HALF
from credinomina_reconciliation.historical import OPERATIVE_START, is_historical_date


def _source(import_name):
    source = frappe.get_doc("CN Accounting Import", import_name)
    source.check_permission("read")
    frappe.has_permission("CN Reconciliation Period", "create", throw=True)
    if not source.employer:
        frappe.throw(_("Seleccione la empresa de la importación antes de crear el período."))
    employer = frappe.get_doc("CN Employer", source.employer)
    employer.check_permission("read")
    return source, employer


@frappe.whitelist()
def get_period_defaults(import_name: str):
    source, employer = _source(import_name)
    dates = sorted({getdate(row.event_date) for row in source.rows or []
                    if row.event_type == "Aplicacion" and row.event_date})
    event_date = getdate(source.bulk_event_date) if source.bulk_event_date else next(iter(dates), None)
    if source.bulk_event_date:
        dates = [event_date]
    month = event_date.replace(day=1) if event_date else None
    historical = month is not None and month < OPERATIVE_START
    frequency = employer.payroll_frequency or MONTHLY
    cycle = ""
    if not historical:
        if event_date and event_date.day not in (15, 30):
            cycle = EXACT_DATE
        elif frequency == MONTHLY:
            cycle = MONTHLY
        elif event_date:
            cycle = FIRST_HALF if event_date.day == 15 else SECOND_HALF
    return {
        "employer": source.employer,
        "payroll_month": month.isoformat() if month else None,
        "reconciliation_mode": "Historica" if historical else "Operativa",
        "payroll_frequency": frequency,
        "collection_cycle": cycle,
        "cutoff_date": event_date.isoformat() if cycle == EXACT_DATE else None,
        "historical_scope": "Fecha exacta" if len(dates) == 1 else "Rango de fechas" if dates else "Mensual",
        "historical_application_date": dates[0].isoformat() if len(dates) == 1 else None,
        "historical_start_date": dates[0].isoformat() if len(dates) > 1 else None,
        "historical_end_date": dates[-1].isoformat() if len(dates) > 1 else None,
    }


@frappe.whitelist()
def create_draft_period(import_name: str, values):
    source, employer = _source(import_name)
    values = frappe.parse_json(values) if isinstance(values, str) else values
    if not isinstance(values, dict) or not values.get("payroll_month"):
        frappe.throw(_("Indique el mes de cobranza."))
    month = getdate(values["payroll_month"]).replace(day=1)
    historical = is_historical_date(month)
    fields = {
        "doctype": "CN Reconciliation Period", "employer": source.employer,
        "payroll_month": month, "reconciliation_mode": "Historica" if historical else "Operativa",
        "status": "Borrador", "docstatus": 0, "remark": values.get("remark") or "",
    }
    if historical:
        scope = values.get("historical_scope") or "Mensual"
        fields["historical_scope"] = scope
        if scope == "Fecha exacta":
            fields["historical_application_date"] = values.get("historical_application_date")
        elif scope == "Rango de fechas":
            fields.update({key: values.get(key) for key in ("historical_start_date", "historical_end_date")})
    else:
        fields["application_basis"] = "Cobranza"
        fields["collection_cycle"] = values.get("collection_cycle") or (
            "Mensual" if employer.payroll_frequency != "Quincenal" else ""
        )
        if fields["collection_cycle"] == "Fecha exacta":
            fields["cutoff_date"] = values.get("cutoff_date")
    # Normal insertion enforces permissions, valid dates/cycles and duplicate cuts.
    # Do not copy accounting rows into payroll, reassign applications or reconcile.
    period = frappe.get_doc(fields).insert()
    return {"name": period.name, "status": period.status}
