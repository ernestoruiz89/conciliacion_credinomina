import frappe
from html import escape
from frappe import _
from frappe.utils import cint, getdate, get_last_day, nowdate

from credinomina_reconciliation.accounting_control import build_rows, summarize, monetary_totals, MONEY_FIELDS
from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.complementary_balances import FIELDS, load_balances


def _date_range(filters):
    start, end = filters.get("from_date"), filters.get("to_date")
    if bool(start) != bool(end):
        frappe.throw(_("Indique ambas fechas: Desde y Hasta."))
    if not start and not end:
        # Keep existing saved links/API calls using month; explicit ranges win.
        start = getdate(filters.get("month") or nowdate()).replace(day=1)
        end = get_last_day(start)
    start, end = getdate(start), getdate(end)
    if start > end:
        frappe.throw(_("La fecha Desde no puede ser posterior a la fecha Hasta."))
    return [start, end]


def execute(filters=None):
    filters = frappe._dict(filters or {})
    for doctype in ("CN Accounting Import", "CN Complementary Item", "CN Remittance Allocation"):
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(_("Se requiere permiso de lectura de importaciones y partidas complementarias para este control."), frappe.PermissionError)
    dates = _date_range(filters)
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
                "complementary_item", "remittance_allocation", "source_row", "client_name", "client_number", "loan_number", "portfolio_client_name", "portfolio_employer",
                "voucher", "tmov", "tdoc", "accounting_reference", "event_type", "accounting_classification", "match_status", "deposit_match_status", "difference_reason", "application_adjustment_status"], limit_page_length=0))
    from credinomina_reconciliation.report_records import records
    items = list(records("CN Complementary Item", filters={"accounting_source_key": ["is", "set"], "source_date": ["between", dates]},
        fields=list(dict.fromkeys([*FIELDS, "name", "docstatus", "source_date", "posting_date", "source_account", "source_currency", "source_debit", "source_credit", "source_description",
            "description", "source_fx_rate", "accounting_source_key", "source_row", "source_client_name", "client_number", "loan_number", "employer", "source_voucher", "credit_management_status",
            "voucher", "tmov", "tdoc", "accounting_reference", "category", "subcategory", "reason_type", "amount_usd", "accounting_classification", "review_status", "compensation_status", "result", "source_file", "source_file_hash", "registration_exception"]))))
    if items and frappe.has_permission("CN Remittance Allocation", "read"):
        balances = load_balances(items)
        for item in items:
            item._financial_balance = balances[item.name]
    # Resolve client names only through readable registry records, never unscoped child data.
    client_numbers = list({row.client_number for row in items if row.client_number})
    clients = []
    if client_numbers and frappe.has_permission("CN Client", "read"):
        for offset in range(0, len(client_numbers), 500):
            clients.extend(frappe.get_list("CN Client", filters={"client_number": ["in", client_numbers[offset:offset + 500]]},
                fields=["name", "client_number", "client_name", "employer"], limit_page_length=0))
    from credinomina_reconciliation.deposit_accounting_sources import load_ledger_deposits
    deposits = load_ledger_deposits(dates,
        ["name", "docstatus", "result", "amount_usd", "allocated_usd", "justified_surplus_usd", "unclassified_usd", "detail_status",
                "employer", "bank_account", "accounting_source_key", "source_date", "source_voucher",
                "source_account", "source_currency", "source_debit", "source_credit", "source_fx_rate", "source_description",
                "source_file", "source_file_hash", "source_row", "tmov", "tdoc", "accounting_reference", "accounting_classification", "source_client_name", "source_loan_number", "cash_identity_status", "cash_identity_reason", "consolidated_deposit"])
    progress = {}
    if any(row.event_type == 'Aplicacion' for row in sources):
        if frappe.has_permission('CN Reconciliation Period', 'read'):
            from credinomina_reconciliation.application_context import load_application_context, application_employer
            from credinomina_reconciliation.company_transactions import application_progress_index
            context_sources, context_imports, _periods, collections, _employers = load_application_context()
            progress = application_progress_index(context_sources, context_imports, collections)
            for source in context_sources:
                progress[source['name']]['employer'] = application_employer(source, context_imports[source['parent']], _periods, collections)
        for row in sources:
            if row.event_type == 'Aplicacion' and row.name not in progress:
                progress[row.name] = dict(state='Revisar distribución', net_usd=None, assigned_usd=None,
                    pending_usd=None, observations=[_('Cobertura financiera no disponible; revise el origen y sus permisos.')])
    rows, duplicates = build_rows(sources, imports, items, clients, deposits, progress=progress)
    rows = [row for row in rows if row['event_date'] and dates[0] <= getdate(row['event_date']) <= dates[1]]
    # Filter the displayed classification/current state, after resolving mirrors
    # and financial statuses, and before both KPI totals and account summaries.
    for field in ("employer", "source_account", "source_currency", "movement_type", "state", "category",
                  "reason_type", "accounting_status", "management_status", "treatment_status"):
        if filters.get(field):
            rows = [row for row in rows if row.get(field) == filters[field]]
    totals, summary, known_subtotals = monetary_totals(rows), [], []
    for label, field, currency in [("Débitos C$", "debit_nio", "NIO"), ("Créditos C$", "credit_nio", "NIO"),
                                   ("Débitos US$", "debit_usd", "USD"), ("Créditos US$", "credit_usd", "USD")]:
        unknown = totals[field] is None
        summary.append({'label': _(label), 'value': _('No determinado') if unknown else totals[field],
                        'datatype': 'Data' if unknown else 'Currency', 'currency': currency,
                        'indicator': 'Orange' if unknown else 'Blue'})
        if unknown:
            known_subtotals.append((_(label), currency, totals[field + '_known'], totals[field + '_missing']))
    summary.append({"label": _("Movimientos"), "value": len(rows), "datatype": "Int", "indicator": "Blue"})
    missing = sum(row["missing_conversion"] for row in rows)
    message = _("Control de movimientos importados visibles para su usuario; compare externamente con la balanza. "
                "Las compensaciones no reducen los débitos/créditos originales. C$ muestra solo moneda original NIO; "
                "US$ incluye USD original y conversiones. El estado de conciliación es actual, no un corte histórico. "
                "No certifica carga completa: los movimientos que no llegaron a guardarse no aparecen aquí.")
    if duplicates:
        message += " " + _("Se detectaron {0} posibles repeticiones contables. Se conservaron y están incluidas en los totales; revise su evidencia original.").format(duplicates)
    if missing:
        message += " " + _("ATENCIÓN: {0} movimientos sin evidencia o conversión completa. Los totales afectados están sin determinar; los subtotales conocidos NO son el total del período.").format(missing)
    if known_subtotals:
        message += '<details class="mt-2"><summary>' + escape(_('Ver subtotales conocidos')) + '</summary>'
        message += '<table class="table table-bordered mt-2"><thead><tr>' + ''.join(
            '<th>' + escape(_(label)) + '</th>' for label in ['Concepto', 'Subtotal conocido', 'Filas sin determinar']) + '</tr></thead><tbody>'
        message += ''.join('<tr><td>' + escape(label) + '</td><td>' + escape(f'{currency} {value:,.2f}')
                           + '</td><td>' + str(count) + '</td></tr>' for label, currency, value, count in known_subtotals)
        message += '</tbody></table></details>'
    conflicts = sum(row["identity_conflict"] for row in rows)
    if conflicts:
        message += " " + _("ATENCIÓN: {0} filas con un vínculo de origen contable inconsistente. No se mezclaron los datos de sus depósitos; revise la advertencia de cada fila y ejecute la reparación de orígenes.").format(conflicts)
    pending = sum(bool(row["import_status"] and row["import_status"] not in {"Importado", "Importado con excepciones"}) for row in rows)
    if pending:
        message += " " + _("Hay {0} movimientos en importaciones no finalizadas correctamente; revise su origen.").format(pending)
    if not cint(filters.get('summary')) and not cint(filters.get('audit_columns')):
        message += ' ' + _('Vista operativa: active Ver auditoría para consultar descripción completa, fila original, documentos y huella del archivo.')
    unknown_fields = [field for field in MONEY_FIELDS if totals[field] is None]
    return columns(bool(cint(filters.get("summary"))), bool(cint(filters.get('audit_columns'))), unknown_fields), summarize(rows) if cint(filters.get("summary")) else rows, message, None, summary


def columns(summary=False, audit=False, unknown_fields=()):
    if not summary and not audit:
        compact = [
            {'fieldname': 'event_date', 'label': _('Fecha'), 'fieldtype': 'Date', 'width': 105},
            {'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link', 'options': 'CN Employer', 'width': 170},
            {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 210},
            {'fieldname': 'movement_type', 'label': _('Tipo de movimiento'), 'fieldtype': 'Data', 'width': 170},
            *[{'fieldname': field, 'label': _(label), 'fieldtype': 'Currency', 'options': 'usd_currency', 'precision': 2, 'width': 115}
              for field, label in [('debit_usd', 'Débitos US$'), ('credit_usd', 'Créditos US$'), ('pending_usd', 'Pendiente US$')]],
            {'fieldname': 'state', 'label': _('Conciliación'), 'fieldtype': 'Data', 'width': 180},
            {'fieldname': 'difference_reason', 'label': _('Razón de la diferencia'), 'fieldtype': 'Data', 'width': 190},
            {'fieldname': 'audit_action', 'label': _('Origen / auditoría'), 'fieldtype': 'Data', 'width': 125},
        ]
        visible = {column['fieldname'] for column in compact}
        # Preserve every original column in the export; hide it only in the
        # default operational grid, not in the report data or audit evidence.
        return compact + [dict(column, hidden=1) for column in columns(False, True)
                          if column['fieldname'] not in visible]
    result = [
        {"fieldname": "month" if summary else "event_date", "label": _("Mes" if summary else "Fecha"), "fieldtype": "Data" if summary else "Date", "width": 105},
        {"fieldname": "source_account", "label": _("Cuenta contable"), "fieldtype": "Data", "width": 140},
        {"fieldname": "source_currency", "label": _("Moneda original"), "fieldtype": "Data", "width": 115},
    ]
    if not summary:
        result += [{"fieldname": field, "label": _(label), "fieldtype": "Data", "width": width} for field, label, width in [
            ("voucher", "Asiento", 120), ("client_name", "Nombre del cliente", 220), ("client_number", "Nro. Cliente", 110),
            ("loan_number", "Nro. Crédito", 120), ("employer", "Empresa", 170), ("movement_type", "Tipo de movimiento", 175), ("state", "Conciliación", 185),
            ("category", "Categoría", 180), ("subcategory", "Subcategoría", 170), ("reason_type", "Motivo", 170),
            ("difference_reason", "Razón de la diferencia", 190),
            ("treatment_status", "Tratamiento", 200), ("accounting_status", "Registro contable", 170), ("management_status", "Gestión del saldo a favor", 170)]]
    for currency, label in (("nio", "C$"), ("usd", "US$")):
        for kind, title in (("debit", "Débitos"), ("credit", "Créditos"), ("net", "Neto")):
            result.append({"fieldname": f"{kind}_{currency}", "label": _(f"{title} {label}"), "fieldtype": "Currency", "options": f"{currency}_currency", "precision": 2, "width": 130})
    if summary:
        result += [{"fieldname": "movement_count", "label": _("Movimientos"), "fieldtype": "Int", "width": 100},
                   {"fieldname": "missing_conversion", "label": _("Sin evidencia/conversión"), "fieldtype": "Int", "width": 165}]
        for field in unknown_fields:
            title = next(column['label'] for column in result if column['fieldname'] == field)
            result += [{'fieldname': field + '_known', 'label': _('Subtotal conocido {0}').format(title),
                        'fieldtype': 'Currency', 'options': 'nio_currency' if field.endswith('_nio') else 'usd_currency', 'precision': 2, 'width': 170},
                       {'fieldname': field + '_missing', 'label': _('Sin determinar {0}').format(title), 'fieldtype': 'Int', 'width': 145}]
    else:
        result += [
            {"fieldname": "recorded_deposit_result", "label": _("Resultado registrado en última conciliación"),
             "fieldtype": "Data", "hidden": 1, "width": 210},
            {"fieldname": "fx_rate", "label": _("Tasa C$/US$"), "fieldtype": "Float", "precision": 8, "width": 125},
            {"fieldname": "description", "label": _("Descripción completa"), "fieldtype": "Long Text", "width": 440},
            {"fieldname": "accounting_import", "label": _("Importación contable"), "fieldtype": "Link", "options": "CN Accounting Import", "width": 200},
            {"fieldname": "complementary_item", "label": _("Partida complementaria"), "fieldtype": "Link", "options": "CN Complementary Item", "width": 190},
            {"fieldname": "remittance_allocation", "label": _("Depósito"), "fieldtype": "Link", "options": "CN Remittance Allocation", "width": 190},
            {"fieldname": "bank_account", "label": _("Cuenta bancaria"), "fieldtype": "Link", "options": "CN Bank Account", "width": 190},
            {"fieldname": "source_file", "label": _("Archivo de origen"), "fieldtype": "Data", "width": 220},
            {"fieldname": "source_hash", "label": _("Huella del archivo original"), "fieldtype": "Data", "width": 200},
            {'fieldname': 'source_row', 'label': _('Fila original'), 'fieldtype': 'Int', 'width': 105},
            {'fieldname': 'source_row_id', 'label': _('ID de fila contable'), 'fieldtype': 'Data', 'width': 160},
            {'fieldname': 'recorded_application_state', 'label': _('Estado guardado (auditoría)'), 'fieldtype': 'Data', 'width': 195},
            {'fieldname': 'audit_action', 'label': _('Origen / auditoría'), 'fieldtype': 'Data', 'width': 125},
            {"fieldname": "warning", "label": _("Advertencia"), "fieldtype": "Data", "width": 300},
        ]
    return result
