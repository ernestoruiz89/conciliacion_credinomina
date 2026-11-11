from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import frappe
import pytest
from openpyxl import load_workbook

from credinomina_reconciliation import operational_statement_export as export
from credinomina_reconciliation.client_position import FINANCIAL_VIEW
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


@pytest.fixture(autouse=True)
def date_formats():
    with patch('frappe.utils.xlsxutils.get_excel_date_format', return_value=('DD/MM/YYYY', 'HH:MM:SS')):
        yield


def data():
    rows = [dict(position_type='Aplicación', client_name='Cliente', source_document='APP',
                 applied_usd=204689.22, assigned_usd=191178.74, remitted_usd=191178.74, applied_pending_usd=13510.48),
            dict(position_type='Depósito con saldo pendiente', source_document='DEP', deposit_pending_usd=7331.70),
            dict(position_type='Partida complementaria', source_document='D', complementary_pending_usd=54.27,
                 _signed_complementary_pending_usd=54.27, complementary_pending_net_usd=-54.27),
            dict(position_type='Partida complementaria', source_document='C', complementary_pending_usd=8.51,
                 _signed_complementary_pending_usd=-8.51, complementary_pending_net_usd=8.51),
            dict(position_type='', client_name='Total', is_total_row=1, applied_pending_usd=13510.48)]
    return frappe._dict(columns=report.get_columns(), result=rows)


def build(source=None, **values):
    from frappe.desk import query_report as native
    source = data() if source is None else source
    before = deepcopy(source)
    params = frappe._dict(filters={'position_type': FINANCIAL_VIEW, 'employer': 'IMMSA', 'cutoff_date': '2026-08-31'},
                          visible_idx=[], ignore_visible_idx=True, **values)
    with patch.object(native, 'run', return_value=source) as run:
        name, content = export.build_export(params)
    assert run.call_args.args[1] == params.filters
    # Formatting and worksheet generation must not mutate the report's rows.
    assert source.result == before.result
    return load_workbook(BytesIO(content), data_only=True), name


def summary(book):
    return {a: b for a, b in book['Resumen del saldo'].values if a}


def test_excel_summary_matches_immsa_and_keeps_detail_columns_and_values():
    book, _ = build()
    values = summary(book)
    assert book.sheetnames == ['Resumen del saldo', export.REPORT]
    assert values['Aplicaciones pendientes US$'] == 13510.48
    assert values['Depósitos pendientes US$ (se restan)'] == 7331.70
    assert values['Complementarias pendientes de débito US$'] == -54.27
    assert values['Complementarias pendientes de crédito US$'] == 8.51
    assert values['Complementarias pendientes netas US$'] == -45.76
    assert values['Saldo neto de conciliación US$'] == 6224.54
    sheet = book[export.REPORT]
    assert [cell.value for cell in sheet[1]] == [c['label'] for c in report.get_columns() if not c.get('hidden')]
    assert sheet.max_row == 6
    header = [cell.value for cell in sheet[1]]
    assert 'Depósito asignado US$' not in header and 'Pendiente según detalle US$' not in header
    assert sheet.cell(2, header.index('Asignado US$') + 1).value == 191178.74
    signed_column = header.index('Complementaria pendiente US$') + 1
    assert [sheet.cell(row, signed_column).value for row in (4, 5, 6)] == [-54.27, 8.51, -45.76]
    assert all(book['Resumen del saldo'].cell(row, 2).data_type == 'n' for row in range(4, 10))


def test_search_selection_recomputes_summary_footer_and_preserves_display_order():
    source = data()
    from frappe.desk import query_report as native
    params = frappe._dict(filters={'position_type': FINANCIAL_VIEW}, visible_idx=[3, 0], ignore_visible_idx=False)
    with patch.object(native, 'run', return_value=source):
        _, content = export.build_export(params)
    book = load_workbook(BytesIO(content), data_only=True)
    assert summary(book)['Saldo neto de conciliación US$'] == 13501.97
    sheet = book[export.REPORT]
    header = [c.value for c in sheet[1]]
    doc = header.index('Documento de origen') + 1
    assert [sheet.cell(i, doc).value for i in [2, 3]] == ['C', 'APP']
    assert sheet.cell(4, header.index('Aplicado pendiente US$') + 1).value == 13510.48


@pytest.mark.parametrize('field', ['_signed_complementary_pending_usd', 'complementary_pending_usd'])
def test_unknown_sign_or_balance_never_exports_a_numeric_net(field):
    source = data()
    source.result[2][field] = None
    if field == 'complementary_pending_usd':
        source.result[2]['_signed_complementary_pending_usd'] = None
    book, _ = build(source)
    assert summary(book)['Saldo neto de conciliación US$'] == 'No determinable'


def test_empty_search_exports_no_documents_and_zero_net():
    source = data()
    assert export.selected_rows(source.result, dict(visible_idx=[], ignore_visible_idx=False)) == []
    assert len(export.selected_rows(source.result, dict(visible_idx=[], ignore_visible_idx=True))) == 4


def test_completed_prepared_report_keeps_its_snapshot_and_net_summary():
    source = data()
    source.prepared_report = True
    book, _ = build(source)
    assert summary(book)['Saldo neto de conciliación US$'] == 6224.54


def test_other_reports_and_csv_delegate_to_native_export_unchanged():
    from frappe.desk import query_report as native
    for fields in [dict(report_name='Otro reporte', file_format_type='Excel'),
                   dict(report_name=export.REPORT, file_format_type='CSV')]:
        with patch.object(frappe.local, 'form_dict', frappe._dict(fields)), patch.object(native, 'export_query', return_value='native') as call:
            assert export.export_query() == 'native'
            call.assert_called_once_with()


def test_export_permission_is_checked_before_reading_or_building():
    with patch.object(frappe.local, 'form_dict', frappe._dict(report_name=export.REPORT, file_format_type='Excel')), \
            patch.object(frappe, 'get_cached_value', return_value='CN Reconciliation Period'), \
            patch('frappe.permissions.can_export', side_effect=frappe.PermissionError), \
            patch.object(export, 'build_export') as build:
        with pytest.raises(frappe.PermissionError):
            export.export_query()
        build.assert_not_called()


def test_background_export_uses_same_builder_as_immediate_download():
    with patch.object(frappe.local, 'form_dict', frappe._dict(
            report_name=export.REPORT, file_format_type='Excel', export_in_background=1, filters='{}')), \
            patch.object(frappe, 'get_cached_value', return_value='allowed'), \
            patch('frappe.permissions.can_export'), patch.object(frappe, 'enqueue') as enqueue, \
            patch.object(frappe, 'msgprint'), patch.object(export, 'build_export') as build:
        export.export_query()
    assert enqueue.call_args.args[0] == 'credinomina_reconciliation.operational_statement_export.export_job'
    build.assert_not_called()
    with patch.object(export, 'build_export', return_value=(export.REPORT, b'xlsx')) as build, \
            patch('frappe.desk.utils.send_report_email') as send:
        export.export_job('user@example.test', {'filters': {}})
    build.assert_called_once()
    assert send.call_args.args[:4] == ('user@example.test', export.REPORT, 'xlsx', b'xlsx')


def test_summary_is_unavailable_for_single_type_and_filter_text_is_not_formula():
    book, _ = build(data())
    rows = data().result[:-1]
    from credinomina_reconciliation.client_position import summarize_selection, net_balance_summary
    filters = dict(position_type='Aplicación')
    amounts = summarize_selection(rows, filters)[1]
    figures = net_balance_summary(rows, amounts, filters)
    content = BytesIO()
    book.remove(book['Resumen del saldo'])
    book.save(content)
    content = export.append_summary(content.getvalue(), rows, amounts, figures, filters, {'Cliente': '=1+1'})
    book = load_workbook(BytesIO(content))
    values = summary(book)
    assert 'Saldo neto de conciliación US$' not in values
    cell = next(row[1] for row in book['Resumen del saldo'] if row[0].value == 'Cliente')
    assert cell.value == '=1+1' and cell.data_type == 's'
