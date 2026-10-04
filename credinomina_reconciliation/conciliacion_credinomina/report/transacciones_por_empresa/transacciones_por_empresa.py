import frappe
from calendar import monthrange
from frappe import _
from frappe.utils import cint, nowdate

from credinomina_reconciliation.company_transactions import (
    IMPORTED, application_state, deposit_state, monthly_counts, application_month_amounts, deposit_month_amounts,
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
    records, sources, by_name = load_transactions(kind, drafts, scoped, dates)
    amounts = (application_month_amounts(sources, by_name, load_partial_collections(sources, by_name))
               if kind == "Aplicaciones" else deposit_month_amounts(sources))
    data = monthly_counts(records, year, amounts)
    for row in data:
        row["_detail_filters"] = {"year": year, "transaction_type": kind, "include_drafts": int(drafts), "employer": row["employer"]}
    if data:
        data.append({"employer": _("Total"), "_is_total": True,
                     **{key: sum(row[key] for row in data) for key in ["total", *[f"m{month:02}" for month in range(1, 13)]]}})
    message = _("Verde: todas las transacciones conciliadas. Parciales: naranja si el importe conciliado es menor al 50%; amarillo desde el 50%. "
                "Rojo: todas pendientes. —: sin transacciones. Pulse una celda para ver sus transacciones. "
                "El porcentaje usa importes US$ del mes, no cantidades de transacciones. Si no se puede atribuir un pago compartido al mes "
                "o falta conversión, el porcentaje no está disponible y se mantiene naranja. "
                "Cada transacción permanece en el mes de su fecha y considera toda su cobertura, aunque los depósitos, aplicaciones o períodos relacionados sean de otros meses. "
                "El color refleja el estado actual, no un corte histórico; no se ejecuta ninguna conciliación.")
    message += " " + (_("Aplicaciones: una transacción por fila de aplicación, según Fecha del movimiento; no por documento ni por período. "
                        "Conciliar con la cobranza no equivale a conciliar el depósito. Importaciones fallidas y registros cancelados se excluyen.")
        if kind == "Aplicaciones" else _("Depósitos: un registro por depósito, según Fecha del depósito y empresa pagadora, aunque cubra otras empresas o períodos. "
            "Un saldo a favor completamente documentado puede estar conciliado aunque su devolución siga pendiente. Se excluyen los cancelados."))
    if drafts:
        message += " " + _("Los borradores se incluyen como pendientes; en aplicaciones se utiliza el estado Borrador de la importación.")
    summary = transaction_summary(records)
    return columns(), data, message, None, summary


def transaction_summary(records):
    return [{"label": _(label), "value": sum(record["state"] == state for record in records), "datatype": "Int", "indicator": color}
        for state, label, color in (("Conciliado", "Conciliadas", "Green"), ("Parcial", "Parcialmente conciliadas", "Orange"),
                                    ("Pendiente", "Pendientes", "Red"))]


def load_transactions(kind, drafts, scoped, dates, detail=False):
    """Shared permission-scoped selection for both the grid and its drill-down."""
    doctype = "CN Accounting Import" if kind == "Aplicaciones" else "CN Remittance Allocation"
    records = []
    if kind == "Aplicaciones":
        # Accounting imports are not submittable; their business status defines drafts.
        statuses = sorted(IMPORTED | ({"Borrador"} if drafts else set()))
        parents = frappe.get_list(doctype, filters={**scoped, "status": ["in", statuses], "docstatus": ["!=", 2]},
            fields=["name", "employer", "status", "historical_period", "historical_backfill"], limit_page_length=0)
        by_name = {parent.name: parent for parent in parents}
        names = list(by_name)
        sources = []
        for offset in range(0, len(names), 500):
            # Access child rows only through permission-filtered parent records.
            rows = frappe.get_all("CN Source Row", filters={"parent": ["in", names[offset:offset + 500]],
                "parenttype": doctype, "parentfield": "rows", "event_type": "Aplicacion",
                "event_date": ["between", dates], "docstatus": ["!=", 2]},
                fields=["name", "parent", "event_date", "effective", "match_status", "deposit_match_status",
                        "historical_remitted_usd", "application_adjustment_usd", "event_type", "amount", "currency",
                        "equivalent_amount", "equivalent_currency", "fx_basis", "manual_fx_rate",
                        "historical_period", "historical_detail", "processing_route", "collection_row_id", "application_allocation_detail",
                        "client", "client_number", "national_id", "client_name", "loan_number"]
                        + (["idx", "reference", "voucher", "receipt", "deposit_match_reason", "match_reason"] if detail else []),
                limit_page_length=0)
            sources.extend(rows)
            for row in rows:
                parent = by_name[row.parent]
                records.append({"name": row.name, "employer": parent.employer, "date": row.event_date, "state": application_state(row, parent)})
        return records, sources, by_name
    else:
        rows = frappe.get_list(doctype, filters={**scoped, "docstatus": ["in", [0, 1]] if drafts else 1,
            "deposit_date": ["between", dates]}, fields=["name", "employer", "deposit_date", "docstatus", "result",
                "amount_usd", "allocated_usd", "justified_surplus_usd", "unclassified_usd"]
                + (["bank_account", "deposit_reference", "deposit_voucher", "deposit_currency", "deposit_amount"] if detail else []),
            limit_page_length=0)
        records = [{"name": row.name, "employer": row.employer, "date": row.deposit_date, "state": deposit_state(row)} for row in rows]
        return records, rows, {}


def load_partial_collections(sources, imports, partial_only=True):
    """Read only the collection claims needed for partial operative applications."""
    from credinomina_reconciliation.accounting_client_summary import collection_links
    from credinomina_reconciliation.reconciliation import converted_amount
    ids = set()
    for row in sources:
        parent = imports[row["parent"]]
        historical = row.get("historical_period") or parent.get("historical_period") or parent.get("historical_backfill") or row.get("processing_route") == "Historica"
        if not historical and (not partial_only or application_state(row, parent) == "Parcial"):
            ids.update(link.get("collection_row_id") for link in collection_links(row, converted_amount(row, "USD")))
    ids.discard(None)
    ids.discard("")
    if not ids or not frappe.has_permission("CN Reconciliation Period", "read"):
        return {}
    parents = frappe.get_list("CN Reconciliation Period", pluck="name", limit_page_length=0)
    if not parents:
        return {}
    result = {}
    names = sorted(ids)
    for offset in range(0, len(names), 500):
        result.update({row.name: row for row in frappe.get_all("CN Collection Row",
            filters={"name": ["in", names[offset:offset + 500]], "parent": ["in", parents],
                     "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows"},
            fields=["name", "parent", "applied_usd", "remittance_detail", "rounding_adjustment_usd"], limit_page_length=0)})
    return result


def columns():
    months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sept", "Oct", "Nov", "Dic"]
    return [{"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 240},
            *[{"fieldname": f"m{index:02}", "label": _(label), "fieldtype": "Int", "width": 70}
              for index, label in enumerate(months, 1)],
            {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 85}]


@frappe.whitelist()
def get_month_detail(year, month, employer, transaction_type="Aplicaciones", include_drafts=0, state="", search="", start=0):
    """Read-only detail of one cell, with the same inclusion rules as the report."""
    from credinomina_reconciliation.accounting_client_summary import build_summary
    from credinomina_reconciliation.reconciliation import converted_amount
    from credinomina_reconciliation.rounding import money, money_float

    if not str(year).isdigit() or not 1900 <= int(year) <= 9998 or not str(month).isdigit() or not 1 <= int(month) <= 12:
        frappe.throw(_("Indique un año y mes válidos."))
    if transaction_type not in {"Aplicaciones", "Depósitos"} or state not in {"", "Conciliado", "Parcial", "Pendiente"}:
        frappe.throw(_("Tipo de transacción o estado inválido."))
    doctype = "CN Accounting Import" if transaction_type == "Aplicaciones" else "CN Remittance Allocation"
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("No tiene permiso para consultar estas transacciones."), frappe.PermissionError)
    year, month = int(year), int(month)
    dates = [f"{year}-{month:02}-01", f"{year}-{month:02}-{monthrange(year, month)[1]}"]
    scope = {"employer": employer} if employer else {"employer": ["is", "not set"]}
    records, sources, parents = load_transactions(transaction_type, bool(cint(include_drafts)), scope, dates, detail=True)
    states = {row["name"]: row["state"] for row in records}
    query = str(search or "").strip().casefold()
    fields = ("name", "parent", "client_name", "client_number", "loan_number", "reference", "voucher", "receipt",
              "deposit_reference", "deposit_voucher", "bank_account")
    selected = [row for row in sources if (not state or states[row.name] == state)
                and (not query or query in " ".join(str(row.get(field) or "") for field in fields).casefold())]
    selected.sort(key=lambda row: (str(row.get("event_date") or row.get("deposit_date") or ""),
                                  row.get("parent") or "", cint(row.get("idx")), row.name))
    count = len(selected)
    start = max(cint(start), 0)
    collections = load_partial_collections(selected, parents, partial_only=False) if transaction_type == "Aplicaciones" else {}
    amount_fields = (["original_usd", "adjustment_usd", "net_usd", "assigned_usd", "rounding_usd", "pending_usd"]
                     if transaction_type == "Aplicaciones" else ["original_usd", "assigned_usd", "surplus_usd", "pending_usd"])
    totals = {field: money(0) for field in amount_fields}
    details = []
    for index, row in enumerate(selected):
        item = {"name": row.name, "doctype": doctype, "state": states[row.name]}
        if transaction_type == "Aplicaciones":
            parent = parents[row.parent]
            original = converted_amount({**row, "application_adjustment_usd": 0}, "USD")
            eligible = parent.status in IMPORTED and row.get("effective") and row.get("match_status") != "Ignorado"
            summary = build_summary({**parent, "rows": [row]}, collections) if eligible else []
            amounts = summary[0] if summary else {"applied_usd": original, "assigned_usd": 0,
                "rounding_usd": 0, "balance_usd": original, "observations": [_("Borrador o fila no efectiva para conciliación.")]}
            item.update(document=row.parent, row_index=row.get("idx"), date=row.get("event_date"),
                        client_name=row.get("client_name"), client_number=row.get("client_number"), loan_number=row.get("loan_number"),
                        reference=row.get("reference"), voucher=row.get("voucher"), receipt=row.get("receipt"),
                        original_usd=original, adjustment_usd=money_float(row.get("application_adjustment_usd")) if eligible else 0,
                        net_usd=amounts["applied_usd"], assigned_usd=amounts["assigned_usd"],
                        rounding_usd=amounts["rounding_usd"], pending_usd=amounts["balance_usd"],
                        observations=" · ".join(amounts["observations"]),
                        status_detail=row.get("deposit_match_status") or "Pendiente",
                        reason=row.get("deposit_match_reason") or row.get("match_reason") or "")
        else:
            confirmed = row.get("docstatus") == 1
            assigned = money(row.get("allocated_usd")) if confirmed else money(0)
            surplus = money(row.get("justified_surplus_usd")) if confirmed else money(0)
            item.update(document=row.name, date=row.get("deposit_date"), reference=row.get("deposit_reference"),
                        voucher=row.get("deposit_voucher"), bank_account=row.get("bank_account"),
                        original_currency=row.get("deposit_currency"), original_amount=row.get("deposit_amount"),
                        original_usd=row.get("amount_usd"), assigned_usd=money_float(assigned), surplus_usd=money_float(surplus),
                        pending_usd=money_float(max(money(row.get("amount_usd")) - assigned - surplus, 0)),
                        status_detail=row.get("result") if confirmed else "Borrador")
        for field in amount_fields:
            # Unknown attribution/conversion must not produce a misleading partial total.
            totals[field] = None if totals[field] is None or item.get(field) is None else totals[field] + money(item[field])
        if start <= index < start + 100:
            details.append(item)
    return {"rows": details, "total": len(records), "filtered_count": count, "start": start, "page_length": 100,
            "summary": transaction_summary(records),
            "totals": {field: None if value is None else money_float(value) for field, value in totals.items()}}
