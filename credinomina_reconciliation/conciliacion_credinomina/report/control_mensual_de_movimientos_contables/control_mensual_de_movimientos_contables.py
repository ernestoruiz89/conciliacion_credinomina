from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, getdate, get_last_day, nowdate

from credinomina_reconciliation.accounting_control import build_rows, summarize
from credinomina_reconciliation.rounding import money, money_float, sum_money


def execute(filters=None):
    filters = frappe._dict(filters or {})
    for doctype in ("CN Accounting Import", "CN Complementary Item"):
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(_("Se requiere permiso de lectura de importaciones y partidas complementarias para este control."), frappe.PermissionError)
    start = getdate(filters.get("month") or nowdate()).replace(day=1)
    dates = [start, get_last_day(start)]
    imports = {row.name: row for row in frappe.get_list("CN Accounting Import",
        fields=["name", "employer", "currency", "manual_fx_rate", "creation", "status", "source_file", "file_hash", "bulk_source_file", "bulk_source_hash"],
        limit_page_length=0)}
    sources = []
    names = list(imports)
    for offset in range(0, len(names), 500):
        sources.extend(frappe.get_all("CN Source Row", filters={"parent": ["in", names[offset:offset + 500]],
            "parenttype": "CN Accounting Import", "parentfield": "rows", "event_date": ["between", dates]},
            fields=["name", "parent", "idx", "event_date", "source_account", "source_currency", "source_debit", "source_credit",
                "source_description", "description", "source_fx_rate", "manual_fx_rate", "fx_rate", "accounting_source_key", "source_key",
                "complementary_item", "client_name", "client_number", "loan_number", "portfolio_client_name", "portfolio_employer",
                "voucher", "event_type", "accounting_classification", "match_status", "deposit_match_status", "application_adjustment_status"], limit_page_length=0))
    items = frappe.get_list("CN Complementary Item", filters={"accounting_source_key": ["is", "set"], "source_date": ["between", dates]},
        fields=["name", "docstatus", "source_date", "posting_date", "source_account", "source_currency", "source_debit", "source_credit", "source_description",
            "description", "source_fx_rate", "accounting_source_key", "source_client_name", "client_number", "loan_number", "employer", "source_voucher",
            "voucher", "category", "amount_usd", "accounting_classification", "review_status", "compensation_status", "result", "source_file", "source_file_hash"], limit_page_length=0)
    if items and frappe.has_permission("CN Remittance Allocation", "read"):
        assigned = defaultdict(lambda: money(0))
        for deposit in frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1}, fields=["name", "allocation_detail"], limit_page_length=0):
            entries = frappe.parse_json(deposit.allocation_detail or "[]")
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and entry.get("partida"):
                    assigned[entry["partida"]] += money(entry.get("importe_usd"))
        for item in items:
            item.cash_assigned_usd = money_float(assigned[item.name])
    # Resolve client names only through readable registry records, never unscoped child data.
    client_numbers = list({row.client_number for row in items if row.client_number})
    clients = []
    if client_numbers and frappe.has_permission("CN Client", "read"):
        for offset in range(0, len(client_numbers), 500):
            clients.extend(frappe.get_list("CN Client", filters={"client_number": ["in", client_numbers[offset:offset + 500]]},
                fields=["name", "client_number", "client_name", "employer"], limit_page_length=0))
    rows, duplicates = build_rows(sources, imports, items, clients)
    for field in ("employer", "source_account", "source_currency"):
        if filters.get(field):
            rows = [row for row in rows if row.get(field) == filters[field]]
    summary = [{"label": _(label), "value": money_float(sum_money(row.get(field) for row in rows)),
                "datatype": "Currency", "currency": currency, "indicator": "Blue"}
               for label, field, currency in [("Débitos C$", "debit_nio", "NIO"), ("Créditos C$", "credit_nio", "NIO"),
                                               ("Débitos US$", "debit_usd", "USD"), ("Créditos US$", "credit_usd", "USD")]]
    summary.append({"label": _("Movimientos"), "value": len(rows), "datatype": "Int", "indicator": "Blue"})
    missing = sum(row["missing_conversion"] for row in rows)
    message = _("Control de movimientos importados visibles para su usuario; compare externamente con la balanza. "
                "Las compensaciones no reducen los débitos/créditos originales. C$ muestra solo moneda original NIO; "
                "US$ incluye USD original y conversiones. El estado de conciliación es actual, no un corte histórico. "
                "No certifica carga completa: los movimientos que no llegaron a guardarse no aparecen aquí.")
    if duplicates:
        message += " " + _("Se omitieron {0} posibles repeticiones entre importaciones por identidad contable y ocurrencia; revise los archivos si son asientos distintos.").format(duplicates)
    if missing:
        message += " " + _("ATENCIÓN: {0} movimientos sin evidencia o conversión completa; los totales solo suman los importes disponibles.").format(missing)
    pending = sum(bool(row["import_status"] and row["import_status"] not in {"Importado", "Importado con excepciones"}) for row in rows)
    if pending:
        message += " " + _("Hay {0} movimientos en importaciones no finalizadas correctamente; revise su origen.").format(pending)
    return columns(bool(cint(filters.get("summary")))), summarize(rows) if cint(filters.get("summary")) else rows, message, None, summary


def columns(summary=False):
    result = [
        {"fieldname": "month" if summary else "event_date", "label": _("Mes" if summary else "Fecha"), "fieldtype": "Data" if summary else "Date", "width": 105},
        {"fieldname": "source_account", "label": _("Cuenta contable"), "fieldtype": "Data", "width": 140},
        {"fieldname": "source_currency", "label": _("Moneda original"), "fieldtype": "Data", "width": 115},
    ]
    if not summary:
        result += [{"fieldname": field, "label": _(label), "fieldtype": "Data", "width": width} for field, label, width in [
            ("voucher", "Asiento", 120), ("client_name", "Nombre del cliente", 220), ("client_number", "Nro. Cliente", 110),
            ("loan_number", "Nro. Crédito", 120), ("employer", "Empresa", 170), ("movement_type", "Tipo de movimiento", 175), ("state", "Estado", 185)]]
    for currency, label in (("nio", "C$"), ("usd", "US$")):
        for kind, title in (("debit", "Débitos"), ("credit", "Créditos"), ("net", "Neto")):
            result.append({"fieldname": f"{kind}_{currency}", "label": _(f"{title} {label}"), "fieldtype": "Currency", "options": f"{currency}_currency", "precision": 2, "width": 130})
    if summary:
        result += [{"fieldname": "movement_count", "label": _("Movimientos"), "fieldtype": "Int", "width": 100},
                   {"fieldname": "missing_conversion", "label": _("Sin evidencia/conversión"), "fieldtype": "Int", "width": 165}]
    else:
        result += [
            {"fieldname": "fx_rate", "label": _("Tasa C$/US$"), "fieldtype": "Float", "precision": 8, "width": 125},
            {"fieldname": "description", "label": _("Descripción completa"), "fieldtype": "Long Text", "width": 440},
            {"fieldname": "accounting_import", "label": _("Importación contable"), "fieldtype": "Link", "options": "CN Accounting Import", "width": 200},
            {"fieldname": "complementary_item", "label": _("Partida complementaria"), "fieldtype": "Link", "options": "CN Complementary Item", "width": 190},
            {"fieldname": "source_file", "label": _("Archivo de origen"), "fieldtype": "Data", "width": 220},
            {"fieldname": "source_hash", "label": _("Huella del archivo original"), "fieldtype": "Data", "width": 200},
            {"fieldname": "warning", "label": _("Advertencia"), "fieldtype": "Data", "width": 300},
        ]
    return result
