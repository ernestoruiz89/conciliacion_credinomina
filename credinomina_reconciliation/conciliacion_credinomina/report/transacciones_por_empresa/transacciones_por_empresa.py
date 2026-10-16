import frappe
from calendar import monthrange
from frappe import _
from frappe.utils import cint, nowdate

from credinomina_reconciliation.company_transactions import (
    IMPORTED, application_progress_index, deposit_state, monthly_counts, application_month_amounts, deposit_month_amounts,
)
from credinomina_reconciliation import complementary_transactions
from credinomina_reconciliation.application_context import import_company_scope, application_employer
from credinomina_reconciliation.report_records import records as readable_records, child_records

TRANSACTION_DOCTYPES = {"Aplicaciones": "CN Accounting Import", "Depósitos": "CN Remittance Allocation",
                       **{kind: "CN Complementary Item" for kind in complementary_transactions.KINDS}}
STATE_FILTERS = {"Conciliado", "Parcial", "Pendiente", "Revisar distribución", "Distribuciones inconsistentes"}


def normalize_state_filter(value):
    if value in (None, "", "Todos"):
        return ""
    if value == "Distribuciones inconsistentes":
        return "Revisar distribución"
    return value if value in STATE_FILTERS else None


def execute(filters=None):
    filters = frappe._dict(filters or {})
    raw_year = str(filters.get("year") or nowdate()[:4])
    if not raw_year.isdigit() or not 1900 <= int(raw_year) <= 9998:
        frappe.throw(_("Indique un año válido entre 1900 y 9998."))
    year = int(raw_year)
    kind = filters.get("transaction_type") or "Aplicaciones"
    if kind not in TRANSACTION_DOCTYPES:
        frappe.throw(_("Seleccione un tipo de transacción válido."))
    selected_state = normalize_state_filter(filters.get("state"))
    if selected_state is None:
        frappe.throw(_("Seleccione un estado de conciliación válido."))
    drafts = bool(cint(filters.get("include_drafts")))
    doctype = TRANSACTION_DOCTYPES[kind]
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("No tiene permiso para consultar estas transacciones."), frappe.PermissionError)
    dates = [f"{year}-01-01", f"{year}-12-31"]
    scoped = {"employer": filters.employer} if filters.get("employer") else {}
    records, sources, by_name = load_transactions(kind, drafts, scoped, dates)
    progress, collections = _application_progress(records, sources, by_name) if kind == "Aplicaciones" else ({}, {})
    if selected_state:
        selected_names = {record["name"] for record in records if record["state"] == selected_state}
        records = [record for record in records if record["name"] in selected_names]
        sources = [source for source in sources if source.get("name") in selected_names]
    amounts = (application_month_amounts(sources, by_name, collections, progress)
               if kind == "Aplicaciones" else complementary_transactions.month_amounts(sources)
               if kind in complementary_transactions.KINDS else deposit_month_amounts(sources))
    data = monthly_counts(records, year, amounts)
    for row in data:
        row["_detail_filters"] = {"year": year, "transaction_type": kind, "include_drafts": int(drafts), "employer": row["employer"]}
        if selected_state:
            row["_detail_filters"]["state"] = selected_state
    if data:
        data.append({"employer": _("Total"), "_is_total": True,
                     **{key: sum(row[key] for row in data) for key in ["total", *[f"m{month:02}" for month in range(1, 13)]]}})
    message = _("Verde: todas las transacciones conciliadas. Parciales: naranja si el importe conciliado es menor al 50%; amarillo desde el 50%. "
                "Rojo: todas pendientes. —: sin transacciones. Pulse una celda para ver sus transacciones. "
                "Use Estado de conciliación para limitar la matriz a un estado, por ejemplo Distribuciones inconsistentes. "
                "El porcentaje usa importes US$ del mes, no cantidades de transacciones. Si no se puede atribuir un pago compartido al mes "
                "o falta conversión, el porcentaje no está disponible y se mantiene naranja. "
                "Cada transacción permanece en el mes de su fecha y considera toda su cobertura, aunque los depósitos, aplicaciones o períodos relacionados sean de otros meses. "
                "El color refleja el estado actual, no un corte histórico; no se ejecuta ninguna conciliación.")
    if kind in complementary_transactions.KINDS:
        message += " " + _("Partidas complementarias: un documento por transacción y empresa titular. Las contables usan la fecha original del movimiento; las sin origen contable usan la fecha de la partida. "
            "La separación utiliza la identidad de importación contable, no el comprobante. Se excluyen canceladas y tolerancias revertidas. "
            "La cobertura usa importes absolutos para no compensar signos entre partidas; considera depósitos, ajustes y compensaciones confirmados de cualquier mes. "
            "No conciliatoria conserva su saldo pendiente hasta vincular una liquidación real. Registro contable verificado acredita un asiento, no efectivo recibido. "
            "Un saldo a favor documentado puede estar conciliado aunque su gestión siga pendiente.")
    else:
        message += " " + (_("Aplicaciones: una transacción por fila de aplicación, según Fecha del movimiento; no por documento ni por período. "
                        "Conciliar con la cobranza no equivale a conciliar el depósito. Importaciones fallidas y registros cancelados se excluyen.")
        if kind == "Aplicaciones" else _("Depósitos: un registro por depósito, según Fecha del depósito y empresa pagadora, aunque cubra otras empresas o períodos. "
            "Un saldo a favor completamente documentado puede estar conciliado aunque su devolución siga pendiente. Se excluyen los cancelados."))
    if drafts:
        message += " " + _("Los borradores sin tratamiento confirmado se incluyen como pendientes; en aplicaciones se utiliza el estado Borrador de la importación.")
    if kind in complementary_transactions.KINDS:
        message += " " + _("Compensaciones confirmadas y tratamientos ya documentados se incluyen aunque el documento permanezca en borrador.")
    summary = transaction_summary(records)
    return columns(), data, message, None, summary


def transaction_summary(records):
    return [{"label": _(label), "value": sum(record["state"] == state for record in records), "datatype": "Int", "indicator": color}
        for state, label, color in (("Conciliado", "Conciliadas", "Green"), ("Parcial", "Parcialmente conciliadas", "Orange"),
                                    ("Pendiente", "Pendientes", "Red"), ("Revisar distribución", "Distribuciones inconsistentes", "Red"))]


def load_transactions(kind, drafts, scoped, dates, detail=False):
    """Shared permission-scoped selection for both the grid and its drill-down."""
    if kind in complementary_transactions.KINDS:
        return complementary_transactions.load_transactions(kind, drafts, scoped, dates, detail)
    doctype = TRANSACTION_DOCTYPES[kind]
    records = []
    if kind == "Aplicaciones":
        # Accounting imports are not submittable; their business status defines drafts.
        statuses = sorted(IMPORTED | ({"Borrador"} if drafts else set()))
        company = scoped.get("employer")
        company_filter = import_company_scope(company) if isinstance(company, str) else {}
        parents = frappe.get_list(doctype, filters={"status": ["in", statuses], "docstatus": ["!=", 2]},
            **company_filter,
            fields=["name", "employer", "status", "historical_period", "historical_backfill"], limit_page_length=0)
        by_name = {parent.name: parent for parent in parents}
        # A legacy import can contain rows for several companies. Resolve each
        # row rather than changing its parent or allocating the entire file.
        legacy = any(not parent.get("employer") for parent in parents)
        periods = {row.name: row for row in readable_records("CN Reconciliation Period",
            fields=["name", "employer"])} if legacy and frappe.has_permission("CN Reconciliation Period", "read") else {}
        collections = {row.name: row for row in child_records("CN Collection Row", periods,
            "CN Reconciliation Period", "collection_rows", fields=["name", "parent"])} if legacy else {}
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
                        "client", "client_number", "national_id", "client_name", "loan_number", "portfolio_employer"]
                        + (["idx", "reference", "voucher", "receipt", "deposit_match_reason", "match_reason"] if detail else []),
                limit_page_length=0)
            for row in rows:
                parent = by_name[row.parent]
                if not parent.get("employer") and (row.get("historical_period") or parent.get("historical_period")) not in (None, "", *periods):
                    continue  # Do not expose an inaccessible legacy period.
                resolved = application_employer(row, parent, periods, collections)
                if (isinstance(company, str) and resolved != company) or (isinstance(company, list) and resolved):
                    continue
                row["report_employer"] = resolved
                sources.append(row)
                # State is resolved after loading the monetary evidence for ALL
                # rows, including those whose saved label says "conciliado".
                records.append({"name": row.name, "employer": resolved, "date": row.event_date, "state": "Pendiente"})
        return records, sources, by_name
    else:
        rows = frappe.get_list(doctype, filters={**scoped, "docstatus": ["in", [0, 1]] if drafts else 1,
            "deposit_date": ["between", dates]}, fields=["name", "employer", "deposit_date", "docstatus", "result",
                "amount_usd", "allocated_usd", "justified_surplus_usd", "unclassified_usd", "detail_status", "cash_identity_status"]
                + (["bank_account", "deposit_reference", "deposit_voucher", "deposit_currency", "deposit_amount"] if detail else []),
            limit_page_length=0)
        rows = [row for row in rows if row.get('cash_identity_status') != 'Origen vinculado']
        records = [{"name": row.name, "employer": row.employer, "date": row.deposit_date, "state": deposit_state(row)} for row in rows]
        return records, rows, {}


def load_application_collections(sources, imports):
    """Batch-read operative claims irrespective of their saved workflow label."""
    from credinomina_reconciliation.accounting_client_summary import collection_links
    from credinomina_reconciliation.reconciliation import converted_amount
    ids = set()
    for row in sources:
        parent = imports[row["parent"]]
        historical = row.get("historical_period") or parent.get("historical_period") or parent.get("historical_backfill") or row.get("processing_route") == "Historica"
        if (not historical and parent.get("status") in IMPORTED and row.get("effective")
                and row.get("match_status") != "Ignorado"):
            try:
                ids.update(link.get("collection_row_id") for link in collection_links(row, converted_amount(row, "USD")))
            except (ValueError, TypeError):
                continue  # Display the row as inconsistent rather than trusting its saved label.
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


def _application_progress(records, sources, imports):
    """One read-only monetary snapshot for filters, counts and displayed detail."""
    collections = load_application_collections(sources, imports)
    progress = application_progress_index(sources, imports, collections)
    for record in records:
        record["state"] = progress[record["name"]]["state"]
    return progress, collections


def columns():
    months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sept", "Oct", "Nov", "Dic"]
    return [{"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 240},
            *[{"fieldname": f"m{index:02}", "label": _(label), "fieldtype": "Int", "width": 70}
              for index, label in enumerate(months, 1)],
            {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 85}]


@frappe.whitelist()
def get_month_detail(year, month, employer, transaction_type="Aplicaciones", include_drafts=0, state="", search="", start=0,
                     only_differences=0):
    """Read-only detail of one cell, with the same inclusion rules as the report."""
    from credinomina_reconciliation.rounding import money, money_float

    if not str(year).isdigit() or not 1900 <= int(year) <= 9998 or not str(month).isdigit() or not 1 <= int(month) <= 12:
        frappe.throw(_("Indique un año y mes válidos."))
    normalized_state = normalize_state_filter(state)
    if transaction_type not in TRANSACTION_DOCTYPES or normalized_state is None:
        frappe.throw(_("Tipo de transacción o estado inválido."))
    state = normalized_state
    doctype = TRANSACTION_DOCTYPES[transaction_type]
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("No tiene permiso para consultar estas transacciones."), frappe.PermissionError)
    year, month = int(year), int(month)
    dates = [f"{year}-{month:02}-01", f"{year}-{month:02}-{monthrange(year, month)[1]}"]
    scope = {"employer": employer} if employer else {"employer": ["is", "not set"]}
    # Use the same annual application context as the calendar before filtering
    # the requested month. A shared claim's inconsistent links must not appear
    # settled merely because its other applications are in a different month.
    context_dates = [f"{year}-01-01", f"{year}-12-31"] if transaction_type == "Aplicaciones" else dates
    records, sources, parents = load_transactions(transaction_type, bool(cint(include_drafts)), scope, context_dates, detail=True)
    progress = _application_progress(records, sources, parents)[0] if transaction_type == "Aplicaciones" else {}
    if transaction_type == "Aplicaciones":
        sources = [row for row in sources if dates[0] <= str(row.get("event_date") or "")[:10] <= dates[1]]
        names = {row.name for row in sources}
        records = [record for record in records if record["name"] in names]
    states = {row["name"]: row["state"] for row in records}
    query = str(search or "").strip().casefold()
    fields = ("name", "parent", "client_name", "client_number", "loan_number", "reference", "voucher", "receipt",
              "deposit_reference", "deposit_voucher", "bank_account", "category", "source_voucher", "source_description", "description")
    selected = [row for row in sources if (not state or states[row.name] == state)
                and (not cint(only_differences) or states[row.name] in {"Parcial", "Pendiente", "Revisar distribución"})
                and (not query or query in " ".join(str(row.get(field) or "") for field in fields).casefold())]
    selected.sort(key=lambda row: (str(row.get("event_date") or row.get("deposit_date") or ""),
                                  row.get("parent") or "", cint(row.get("idx")), row.name))
    count = len(selected)
    start = max(cint(start), 0)
    amount_fields = (["original_usd", "adjustment_usd", "net_usd", "assigned_usd", "rounding_usd", "complementary_usd", "pending_usd"]
                     if transaction_type == "Aplicaciones" else ["original_usd", "resolved_usd", "pending_usd"]
                     if transaction_type in complementary_transactions.KINDS else ["original_usd", "assigned_usd", "surplus_usd", "pending_usd"])
    totals = {field: money(0) for field in amount_fields}
    details = []
    for index, row in enumerate(selected):
        item = {"name": row.name, "doctype": doctype, "state": states[row.name]}
        if transaction_type == "Aplicaciones":
            amounts = progress[row.name]
            item.update(document=row.parent, row_index=row.get("idx"), date=row.get("event_date"),
                        client_name=row.get("client_name"), client_number=row.get("client_number"), loan_number=row.get("loan_number"),
                        reference=row.get("reference"), voucher=row.get("voucher"), receipt=row.get("receipt"),
                        **{field: amounts[field] for field in ("original_usd", "adjustment_usd", "net_usd", "assigned_usd", "rounding_usd", "pending_usd")},
                        observations=" · ".join(amounts["observations"]),
                        status_detail=_("Resultado registrado: {0}").format(row.get("deposit_match_status") or "Pendiente"),
                        reason=row.get("deposit_match_reason") or row.get("match_reason") or "")
            item['complementary_usd'] = (None if amounts['adjustment_usd'] is None or amounts['rounding_usd'] is None
                else money_float(money(amounts['adjustment_usd']) - money(amounts['rounding_usd'])))
        elif transaction_type in complementary_transactions.KINDS:
            balance = row["_balance"]
            item.update(row["_progress"])
            item.update(document=row.name, date=row.get("event_date"), category=row.get("category"),
                        client_name=row.get("client_name"), client_number=row.get("client_number"), loan_number=row.get("loan_number"),
                        voucher=row.get("source_voucher") or row.get("voucher"), reference=row.get("accounting_reference") or row.get("reference"),
                        signed_usd=row.get("amount_usd"), used_label=balance["used_label"],
                        management_pending_usd=balance["management_pending_usd"], management_status=balance["management_status"],
                        reason=row.get("source_description") or row.get("description") or "", observations=row.get("review_notes") or "")
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
