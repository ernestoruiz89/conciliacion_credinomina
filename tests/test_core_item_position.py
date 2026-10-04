import io
import unittest
from datetime import datetime
from unittest.mock import patch

from openpyxl import load_workbook

from credinomina_reconciliation import core_item_position as position
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.control_export import build_control_workbook, _core_item_needs_follow_up


class CoreItemPositionTests(unittest.TestCase):
    def item(self, **changes):
        return dict(dict(name='CN-COMP-2026-00477', employer='REPSA', docstatus=0,
            category='Por clasificar', amount_usd=7.35, source_debit=0, source_credit=269.19,
            source_currency='NIO', source_date='2025-11-30', source_voucher='00101522',
            source_description='Ajuste por aplicación duplicada', client_number='2627',
            loan_number='108803-1', accounting_source_key='ledger-key'), **changes)

    def test_credit_direction_is_not_the_positive_imported_amount(self):
        item = self.item()
        self.assertEqual(position.signed_pending(item, financial_balance(item)), -7.35)
        debit = self.item(source_debit=269.19, source_credit=0)
        self.assertEqual(position.signed_pending(debit, financial_balance(debit)), 7.35)

    def test_partial_compensation_only_remaining_and_closed_not_counted_again(self):
        item = self.item(category='Compensación entre partidas', compensated_usd=5)
        self.assertEqual(position.signed_pending(item, financial_balance(item)), -2.35)
        for changes in [dict(docstatus=2), dict(registration_exception='EXC'),
                        dict(review_action='No conciliatoria'),
                        dict(category='Compensación entre partidas', compensated_usd=7.35)]:
            item = self.item(**changes)
            self.assertEqual(position.signed_pending(item, financial_balance(item)), 0)

    def test_unknown_sign_is_not_zero(self):
        item = self.item(source_credit=0)
        self.assertIsNone(position.signed_pending(item, financial_balance(item)))

    def test_loader_is_scoped_but_balance_can_use_deposits_in_other_years(self):
        item = self.item()
        with patch.object(position, 'records', return_value=[item]) as read, \
             patch.object(position, 'load_balances', return_value={item['name']: financial_balance(item)}) as balances:
            result = position.load_core_items(2025, 'REPSA')
        filters = read.call_args.kwargs['filters']
        self.assertEqual(filters['docstatus'], ['!=', 2])
        self.assertEqual(filters['accounting_source_key'], ['is', 'set'])
        self.assertEqual(filters['employer'], 'REPSA')
        self.assertEqual(filters['source_date'], ['between', ['2025-01-01', '2025-12-31']])
        self.assertEqual(balances.call_args.args, ([item],))
        self.assertEqual(result[0]['_signed_pending_usd'], -7.35)

    def test_export_includes_unclassified_core_item_and_ledger_trace(self):
        item = self.item()
        item['_balance'] = financial_balance(item)
        item['_signed_pending_usd'] = position.signed_pending(item, item['_balance'])
        data = dict(year=2025, periods=[], totals={}, core_complementary_items=[item])
        content = build_control_workbook(data, exceptions=[], actions=[], employer_label='REPSA',
                                         generated_at=datetime(2026, 10, 4))
        sheet = load_workbook(io.BytesIO(content), data_only=True)['Partidas y excepciones']
        self.assertEqual(sheet['A5'].value, 'Partida complementaria del core')
        self.assertEqual(sheet['G5'].value, item['name'])
        self.assertEqual(sheet['H5'].value, -7.35)
        self.assertEqual(sheet['I5'].value, 'Pendiente')
        self.assertEqual(sheet['J5'].value, 'Por clasificar')
        self.assertEqual(sheet['O5'].value, '00101522')
        self.assertEqual(sheet['P5'].value, item['source_description'])
        self.assertEqual(sheet['U5'].value, 269.19)
        self.assertEqual(sheet['V5'].value, 'NIO')
        self.assertEqual(sheet['W5'].value, 0)

    def test_settled_core_item_is_omitted_only_from_actionable_sheet(self):
        item = self.item(category='Otros ingresos', docstatus=1, accounting_status='Importada del core')
        item['_balance'] = financial_balance(item, [dict(amount_usd=7.35)])
        item['_signed_pending_usd'] = 0
        self.assertEqual(item['_balance']['financial_status'], 'Conciliada')
        data = dict(year=2025, periods=[dict(name='P', month='2025-11', rows=[dict(
            client_name='Cliente', client_number='2627', complementary_usd=7.35,
            remittance_detail=[dict(destino='Partida complementaria', importe_usd=7.35)])])],
            totals={}, core_complementary_items=[item], cash_deposits=[dict(
                name='DEP', employer='REPSA', total_usd=7.35, other_usd=7.35,
                destinations=[dict(type='Partida complementaria', label=item['name'], amount_usd=7.35)])])
        args = dict(exceptions=[], actions=[], employer_label='REPSA', generated_at=datetime(2026, 10, 4))
        book = load_workbook(io.BytesIO(build_control_workbook(data, **args)), data_only=True)
        self.assertNotIn(item['name'], [row[6] for row in book['Partidas y excepciones'].iter_rows(min_row=5, values_only=True)])
        self.assertEqual(data['core_complementary_items'], [item])  # Do not remove audit evidence from the source.
        baseline = load_workbook(io.BytesIO(build_control_workbook(data | {'core_complementary_items': []}, **args)), data_only=True)
        for name in ('Detalle cliente', 'Depósitos', 'Distribución depósitos', 'Cruces'):
            self.assertEqual(list(book[name].values), list(baseline[name].values))
        self.assertEqual(book['Detalle cliente']['Q5'].value, 7.35)
        self.assertEqual(book['Depósitos']['J5'].value, 7.35)
        self.assertEqual(book['Distribución depósitos']['F5'].value, item['name'])
        with_exception = load_workbook(io.BytesIO(build_control_workbook(data, **(args | {
            'exceptions': [dict(name='EXC', complementary_item=item['name'], status='En revision',
                amount_usd=1, employer='REPSA')]}))), data_only=True)
        cases = list(with_exception['Partidas y excepciones'].iter_rows(min_row=5, values_only=True))
        self.assertTrue(any(row[0] == 'Excepción' and row[6] == 'EXC' for row in cases))
        self.assertFalse(any(row[0] == 'Partida complementaria del core' for row in cases))

    def test_pending_unknown_or_independent_work_stays_visible(self):
        settled = dict(financial_status='Conciliada', pending_usd=0,
                       management_pending_usd=None, accounting_status='Importada del core')
        self.assertFalse(_core_item_needs_follow_up(settled))
        for changes in [dict(pending_usd=.01), dict(pending_usd=None), dict(management_pending_usd=1),
                        dict(financial_status='Parcial'), dict(financial_status='Revisar distribución'),
                        dict(accounting_status='Pendiente de registro'), dict(accounting_status='Asiento informado')]:
            with self.subTest(changes=changes):
                self.assertTrue(_core_item_needs_follow_up(settled | changes))
