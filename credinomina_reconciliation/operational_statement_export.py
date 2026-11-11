"""Native Excel export plus a selection-scoped net reconciliation summary."""
from copy import deepcopy
from inspect import signature
from io import BytesIO
from math import ceil

import frappe
from frappe import _
from frappe.utils import cint, getdate, sbool

from credinomina_reconciliation.client_position import net_balance_summary, summarize_selection, row_net_balance
from credinomina_reconciliation.rounding import money_float, sum_money

REPORT = 'Estado de Cuenta Operativo'


@frappe.whitelist()
def export_query():
    from frappe.desk import query_report as native
    if (frappe.form_dict.get('report_name') != REPORT
            or frappe.form_dict.get('file_format_type') != 'Excel'):
        return native.export_query()
    params = frappe._dict(frappe.form_dict)
    from frappe.desk.utils import pop_csv_params
    pop_csv_params(params)
    native.clean_params(params)
    native.parse_json(params)
    frappe.permissions.can_export(frappe.get_cached_value('Report', REPORT, 'ref_doctype'), raise_exception=True)
    if cint(params.export_in_background):
        email = frappe.get_cached_value('User', frappe.session.user, 'email')
        frappe.enqueue('credinomina_reconciliation.operational_statement_export.export_job',
                       user_email=email, params=params, queue='long', now=frappe.flags.in_test)
        frappe.msgprint(_('El Excel se está generando en segundo plano. Recibirá el enlace de descarga por correo.'))
        return
    name, content = build_export(params)
    from frappe.desk.utils import provide_binary_file
    provide_binary_file(_(name), 'xlsx', content)


def export_job(user_email, params):
    from frappe.desk.utils import send_report_email
    name, content = build_export(frappe._dict(params))
    send_report_email(user_email, name, 'xlsx', content, attached_to_name=REPORT)


def selected_rows(rows, params):
    """Retain native display order; summaries never include the table footer."""
    indexes = frappe.parse_json(params.get('visible_idx') or '[]')
    if (indexes or 'ignore_visible_idx' in params) and not sbool(params.get('ignore_visible_idx')):
        rows = [rows[index] for index in indexes if isinstance(index, int) and 0 <= index < len(rows)]
    return [row for row in rows if not row.get('is_total_row')]


def build_export(params):
    from frappe.desk import query_report as native
    from frappe.utils.xlsxutils import make_xlsx
    filters = frappe.parse_json(params.get('filters') or '{}')
    # Native run enforces report permissions and linked-document row permissions.
    data = frappe._dict(native.run(REPORT, filters,
        custom_columns=frappe.parse_json(params.get('custom_columns') or '[]'), are_default_filters=False))
    if not data.get('columns'):
        frappe.throw(_('Espere a que termine el reporte preparado y vuelva a exportarlo.'))
    rows = selected_rows(data.result, params)
    amounts = summarize_selection(rows, filters)[1]
    figures = net_balance_summary(rows, amounts, filters)
    # A search-filtered table must not retain the original, broader total row.
    monetary = {col['fieldname']: amounts.get(col['fieldname']) for col in data.columns if col.get('fieldtype') == 'Currency'}
    data.result = deepcopy(rows)
    for row in data.result:
        row['net_balance_usd'] = row_net_balance(row)
    if rows:
        data.result.append(dict(monetary, client_name=_('Total'), is_total_row=1))
    data.update(report_name=REPORT, filters=filters, applied_filters=params.get('applied_filters'), add_total_row=False)
    if 'file_format_type' in signature(native.format_fields).parameters:
        native.format_fields(data, 'Excel')
    else:
        native.format_fields(data)
    options = dict(visible_idx=[], include_indentation=params.get('include_indentation'),
                   include_filters=params.get('include_filters'), include_hidden_columns=params.get('include_hidden_columns'))
    if 'build_styles' in signature(native.build_xlsx_data).parameters:
        options['build_styles'] = True
    table, widths, *styles = native.build_xlsx_data(data, **options)
    extra = {'styles': styles[0]} if styles else {}
    content = make_xlsx(table, REPORT, column_widths=widths, **extra).getvalue()
    content = append_summary(content, rows, amounts, figures, filters, params.get('applied_filters'))
    name = REPORT
    if cint(params.get('include_filters')):
        for value in filters.values():
            suffix = ('_' + ','.join(map(str, value)) if isinstance(value, list)
                      else '_' + value if isinstance(value, str) and value not in {'Yes', 'No'} else '')
            if native.valid_report_name(name, suffix):
                name += suffix
    return name, content


def append_summary(content, rows, amounts, figures, filters, applied_filters=None):
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    book = load_workbook(BytesIO(content))
    sheet = book.create_sheet('Resumen del saldo', 0)
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions['A'].width = 64
    sheet.column_dimensions['B'].width = 27
    sheet.append([_('Resumen del saldo neto'), 'USD'])
    sheet.append([_('Fecha de corte histórico'), getdate(filters['cutoff_date']) if filters.get('cutoff_date') else _('Saldos actuales')])
    sheet['B2'].number_format = 'dd/mm/yyyy'
    sheet.append([])

    def append(label, value):
        sheet.append([_(label), _('No determinable') if value is None else value])
        sheet.cell(sheet.max_row, 2).number_format = '#,##0.00;[Red](#,##0.00);0.00'

    if not figures:
        sheet.append([_('Seleccione Aplicaciones, depósitos y partidas o todos los tipos para calcular el saldo neto.')])
    else:
        append('Aplicaciones pendientes US$', amounts.get('applied_pending_usd', 0))
        append('Depósitos pendientes US$ (se restan)', amounts.get('deposit_pending_usd', 0))
        items = [row for row in rows if row['position_type'] == 'Partida complementaria']
        unknown = any(row.get('_signed_complementary_pending_usd') is None
                      or row.get('operational_status') == 'Revisar distribución' for row in items)
        for label, positive in [('Complementarias pendientes de crédito US$', True),
                                ('Complementarias pendientes de débito US$', False)]:
            values = [-row['_signed_complementary_pending_usd'] for row in items] if not unknown else []
            value = None if unknown else money_float(sum_money(v for v in values if (v >= 0) == positive))
            append(label, value)
        for label in ('Complementarias pendientes netas US$', 'Saldo neto de conciliación US$'):
            figure = next((row for row in figures if row['label'] == label), None)
            append(label, figure['value'] if figure else None)
    sheet.append([])
    sheet.append([_('Saldo neto = aplicaciones pendientes − depósitos pendientes − complementarias pendientes netas.')])
    sheet.append([_('Complementarias: créditos positivos y débitos negativos. El saldo corresponde a la selección exportada.')])
    if applied_filters:
        sheet.append([])
        sheet.append([_('Filtros aplicados')])
        for label, value in applied_filters.items():
            sheet.append([str(label), str(value)])
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            # User-authored notes/filter labels remain literal text in Excel.
            if isinstance(cell.value, str):
                cell.data_type = 's'
        lines = max(ceil(len(str(row[0].value or '')) / 60), ceil(len(str(row[1].value or '')) / 24), 1)
        sheet.row_dimensions[row[0].row].height = max(28, lines * 16 + 8)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='204D74')
    for row in sheet:
        if row[0].value == _('Saldo neto de conciliación US$'):
            for cell in row:
                cell.font = Font(bold=True)
                cell.fill = PatternFill('solid', fgColor='EAF1F7')
    output = BytesIO()
    book.save(output)
    return output.getvalue()
