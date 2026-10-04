import frappe
from frappe import _
from frappe.utils import flt

from credinomina_reconciliation.aging import collection_shortfall_usd, deduction_pending_type
from credinomina_reconciliation.reconciliation import AMOUNT_TOLERANCE
from credinomina_reconciliation.rounding import MONEY_EPSILON, decimal_value, money, money_float
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.client_position import company_balance_rows, company_position, load_position, summary


def execute(filters=None):
    filters = frappe._dict(filters or {})
    collection_columns, collections = _collection_report(filters)
    data = load_position(collections, filters)
    is_summary = filters.get('view_mode') != 'Detalle'
    totals = summary(company_balance_rows(data) if is_summary else data)
    if is_summary:
        message = _("Resumen actual en US$, no saldo contractual del préstamo. "
            "Pendiente: aplicado neto menos depósitos y compensaciones confirmadas, sin duplicar ajustes. "
            "Saldos a favor: documentados y aún por gestionar, con signo negativo. "
            "Saldo suma las tres columnas: es un neto informativo, no una compensación entre deudas o clientes. "
            "Cobranza, saldos a favor sin confirmar y otras complementarias se consultan en Detalle; no integran este neto. ")
    else:
        message = _("Posición actual de conciliación, no saldo contractual del préstamo. "
            "La cobranza no genera CxC. La CxC nace de lo aplicado en el core menos depósitos y compensaciones confirmadas. "
            "El aplicado neto ya descuenta los ajustes confirmados vinculados; no se descuentan otra vez. "
            "Las partidas complementarias separan distribución, registro contable y gestión de saldos a favor. ")
    message += _("Los filtros se aplican a los movimientos antes de agrupar. Las fechas seleccionan movimientos, "
                 "no reconstruyen saldos históricos. Solo se incluyen documentos visibles para su usuario.")
    return get_columns(filters), company_position(data) if is_summary else data, message, None, totals


def _collection_report(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_collection_columns()
    periods = get_periods(filters)
    if not periods:
        return columns, []

    period_map = {row.name: row for row in periods}
    row_filters = {"parent": ["in", list(period_map)]}
    if filters.client_number:
        row_filters["client_number"] = filters.client_number
    if filters.national_id:
        row_filters["national_id"] = filters.national_id
    if filters.loan_number:
        row_filters["loan_number"] = filters.loan_number

    rows = child_records(
        "CN Collection Row", period_map, "CN Reconciliation Period", "collection_rows",
        filters=row_filters,
        fields=[
            "name",
            "parent",
            "client_number",
            "client_name",
            "national_id",
            "loan_number",
            "installment_number",
            "expected_usd",
            "expected_nio",
            "deducted_usd",
            "deducted_nio",
            "applied_usd",
            "applied_nio",
            "complementary_usd",
            "remitted_usd",
            "remitted_nio",
            "fx_variance_usd",
            "rounding_adjustment_usd",
            "deduction_status",
            "application_status",
        ],
        order_by="parent asc, idx asc",
    )
    data = []
    for row in rows:
        period = period_map[row.parent]
        rate = (
            decimal_value(row.expected_nio) / decimal_value(row.expected_usd)
            if money(row.expected_usd) > decimal_value(AMOUNT_TOLERANCE) else 0
        )
        classified_usd = money(row.complementary_usd) + max(money(row.fx_variance_usd), 0)
        rounding = money(row.rounding_adjustment_usd)
        classified_nio = money(classified_usd * decimal_value(rate))
        employee_pending = collection_shortfall_usd(row)
        item = frappe._dict(
            {
                **row,
                "usd_currency": "USD",
                "nio_currency": "NIO",
                "period": period.name,
                "payroll_month": period.payroll_month,
                "collection_cycle": period.collection_cycle or "Mensual",
                "deduction_basis": period.deduction_basis or "",
                "employer": period.employer,
                "source_rows": row.get("name"),
                "employee_pending_usd": employee_pending,
                "employee_pending_nio": (
                    money_float(decimal_value(employee_pending) * decimal_value(rate))
                    if employee_pending is not None else None
                ),
                "pending_core_usd": money_float(max(
                    money(row.deducted_usd) - money(row.applied_usd) - classified_usd
                    - max(rounding, 0), 0
                )),
                "pending_core_nio": money_float(max(
                    money(row.deducted_nio) - money(row.applied_nio) - classified_nio
                    - max(rounding, 0) * decimal_value(rate), 0
                )),
                "employer_receivable_usd": money_float(max(
                    money(row.deducted_usd) - money(row.remitted_usd)
                    - max(money(row.fx_variance_usd), 0) - max(-rounding, 0), 0
                )),
                "employer_receivable_nio": money_float(max(
                    money(row.deducted_nio) - money(row.remitted_nio)
                    - (max(money(row.fx_variance_usd), 0) + max(-rounding, 0))
                    * decimal_value(rate), 0
                )),
                "operational_status": get_status(row),
            }
        )
        if deduction_pending_type(row):
            for field in ("pending_core_usd", "pending_core_nio", "employer_receivable_usd", "employer_receivable_nio"):
                item[field] = None
        if filters.only_open and _base_status(row) == "Conciliado":
            continue
        data.append(item)
    return columns, data


def get_periods(filters):
    conditions = {"reconciliation_mode": "Operativa"}
    if filters.employer:
        conditions["employer"] = filters.employer
    if filters.from_month and filters.to_month:
        conditions["payroll_month"] = ["between", [filters.from_month, filters.to_month]]
    elif filters.from_month:
        conditions["payroll_month"] = [">=", filters.from_month]
    elif filters.to_month:
        conditions["payroll_month"] = ["<=", filters.to_month]
    periods = records(
        "CN Reconciliation Period",
        filters=conditions,
        fields=["name", "employer", "payroll_month", "collection_cycle", "deduction_basis"],
        order_by="payroll_month desc",
    )
    return [
        period for period in periods
        if not filters.collection_cycle
        or (period.collection_cycle or "Mensual") == filters.collection_cycle
    ]


def get_status(row):
    status = _base_status(row)
    if status == "Conciliado" and abs(money(row.rounding_adjustment_usd)) > MONEY_EPSILON:
        status = "Conciliado con movimiento de conciliación {0:+.4f} US$".format(
            flt(row.rounding_adjustment_usd)
        )
    if row.deduction_status == "Inferida por depósito":
        return status + " · deducción inferida por depósito, sin detalle de planilla"
    return status


def _base_status(row):
    pending_detail = deduction_pending_type(row)
    if pending_detail:
        return pending_detail
    if row.application_status == "Diferencia aplicacion vs deposito":
        return "Diferencia entre aplicación y depósito; requiere revisión"
    if abs(flt(row.fx_variance_usd)) > AMOUNT_TOLERANCE:
        return "Diferencia cambiaria en revisión"
    if not row.deduction_status or row.deduction_status == "Pendiente de detalle":
        return "Pendiente de detalle de la empresa"
    if row.deduction_status in {
        "Deduccion parcial",
        "No deducido",
        "Moneda no coincide",
        "Importes inconsistentes",
        "Importe invalido",
    }:
        return "Cobranza no deducida; revisar primera conciliación"
    if row.application_status == "Aplicado y remitido":
        return "Conciliado"
    if row.application_status == "Remitido, aplicacion parcial":
        return "Depositado por la empresa; aplicación parcial en core"
    if row.application_status == "Depósito parcial":
        return "Depósito parcial"
    if row.application_status == "Aplicacion parcial":
        return "Aplicación parcial en core; depósito pendiente"
    if row.application_status == "Aplicacion encontrada":
        return "Aplicado en core; depósito pendiente"
    if max(
        flt(row.deducted_usd) - flt(row.applied_usd) - flt(row.complementary_usd),
        flt(row.deducted_nio) - flt(row.applied_nio),
    ) > AMOUNT_TOLERANCE:
        return "Deducido; pendiente de aplicar en core"
    return "Pendiente de conciliacion"


def get_collection_columns():
    return [
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 95},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 140},
        {"fieldname": "deduction_basis", "label": _("Origen de deducción"), "fieldtype": "Data", "width": 160},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "client_number", "label": _("Nro. Cliente"), "fieldtype": "Data", "width": 110},
        {"fieldname": "client_name", "label": _("Cliente"), "fieldtype": "Data", "width": 220},
        {"fieldname": "national_id", "label": _("Cedula"), "fieldtype": "Data", "width": 135},
        {"fieldname": "loan_number", "label": _("Credito"), "fieldtype": "Data", "width": 100},
        {"fieldname": "installment_number", "label": _("Cuota"), "fieldtype": "Data", "width": 70},
        {"fieldname": "expected_usd", "label": _("Cobranza enviada US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "deducted_usd", "label": _("Deducido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 110},
        {"fieldname": "applied_usd", "label": _("Aplicado al crédito US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "complementary_usd", "label": _("Partida complementaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "remitted_usd", "label": _("Remitido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 115},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 150},
        {"fieldname": "rounding_adjustment_usd", "label": _("Movimiento de conciliación US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 170},
        {"fieldname": "employee_pending_usd", "label": _("Cobranza no deducida US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "pending_core_usd", "label": _("Pendiente core US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "employer_receivable_usd", "label": _("Deducido sin depósito asignado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 225},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 105},
        {"fieldname": "deducted_nio", "label": _("Deducido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 110},
        {"fieldname": "remitted_nio", "label": _("Remitido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 115},
        {"fieldname": "employee_pending_nio", "label": _("Cobranza no deducida equivalente C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 205},
        {"fieldname": "pending_core_nio", "label": _("Pendiente core C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 125},
        {"fieldname": "employer_receivable_nio", "label": _("Deducido sin depósito asignado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 225},
        {"fieldname": "operational_status", "label": _("Estado operativo"), "fieldtype": "Data", "width": 230},
    ]


def get_columns(filters=None):
    if (filters or {}).get('view_mode') != 'Detalle':
        return [
            {'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link', 'options': 'CN Employer', 'width': 240},
            *[{'fieldname': field, 'label': _(label), 'fieldtype': 'Currency',
               'options': 'usd_currency', 'precision': 2, 'width': width}
              for field, label, width in (
                  ('pending_usd', 'Pendiente US$', 150),
                  ('company_credit_usd', 'Saldo a favor empresa US$', 205),
                  ('client_credit_usd', 'Saldo a favor clientes US$', 205),
                  ('balance_usd', 'Saldo US$', 150))],
        ]
    view = (filters or {}).get('position_type')
    columns = [
        {'fieldname': 'event_date', 'label': _('Fecha'), 'fieldtype': 'Date', 'width': 105},
        {'fieldname': 'position_type', 'label': _('Tipo'), 'fieldtype': 'Data', 'width': 175},
        {'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link', 'options': 'CN Employer', 'width': 155},
        {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 220},
        {'fieldname': 'client_number', 'label': _('Nro. Cliente'), 'fieldtype': 'Data', 'width': 110},
        {'fieldname': 'loan_number', 'label': _('Nro. Crédito'), 'fieldtype': 'Data', 'width': 120},
        {'fieldname': 'source_doctype', 'label': _('Tipo de origen'), 'fieldtype': 'Data', 'hidden': 1},
        {'fieldname': 'source_document', 'label': _('Documento de origen'), 'fieldtype': 'Dynamic Link', 'options': 'source_doctype', 'width': 190},
    ]
    metrics = []
    if not view or view == 'Cobranza':
        metrics.extend([('expected_usd', 'Cobranza enviada US$'), ('deducted_usd', 'Deducido US$'),
                        ('employee_pending_usd', 'Cuota no deducida US$'), ('pending_core_usd', 'Pendiente core US$')])
    if not view or view == 'Aplicación':
        metrics.extend([('applied_usd', 'Aplicado neto US$'), ('remitted_usd', 'Depósito asignado US$'),
                        ('rounding_adjustment_usd', 'Tolerancia US$'), ('applied_pending_usd', 'Aplicado pendiente US$')])
    if not view or view == 'Partida complementaria':
        metrics.extend([('complementary_usd', 'Complementaria original US$'), ('complementary_used_usd', 'Utilizado US$'),
                        ('complementary_pending_usd', 'Complementaria pendiente US$'), ('credit_resolved_usd', 'Gestionado US$'),
                        ('credit_pending_usd', 'Saldo a favor por gestionar US$')])
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Currency', 'options': 'usd_currency', 'width': 150}
                   for field, label in metrics)
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
                   for field, label, width in [('category', 'Categoría', 180), ('operational_status', 'Estado de conciliación', 210),
                       ('accounting_status', 'Registro contable', 170), ('management_status', 'Gestión externa', 170),
                       ('observation', 'Observación', 350), ('source_rows', 'Filas de origen', 170)])
    return columns
