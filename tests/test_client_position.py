import unittest
from unittest.mock import patch

import frappe
from credinomina_reconciliation.client_position import build_position, summary
from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


class ClientPositionTests(unittest.TestCase):
    def test_collection_status_is_not_worker_debt(self):
        from credinomina_reconciliation.reconciliation import operational_status
        for status in ('Deduccion parcial', 'No deducido'):
            row = frappe._dict(deduction_status=status, expected_usd=100, deducted_usd=0)
            self.assertEqual(report.get_status(row), 'Cobranza no deducida; revisar primera conciliación')
        self.assertEqual(operational_status(expected=100, deducted=0, applied=0, remitted=0),
                         'Cobranza no deducida; revisar primera conciliación')

    def build(self, filters=None):
        collections = [dict(period='P', payroll_month='2026-09-01', employer='E', client_number='1',
            loan_number='L', expected_usd=100, deducted_usd=70, employee_pending_usd=30,
            pending_core_usd=0, applied_usd=70, remitted_usd=50, operational_status='Depósito parcial')]
        applications = [dict(period='P', source_import='I', employer='E', client_number='1', loan_number='L',
            application_date='2026-09-30', applied_usd=70, paid_usd=50, amount_usd=20, adjustment_usd=0)]
        items = [dict(name='C', category='Saldo a favor del cliente', docstatus=1, employer='E',
            client_number='1', credit_client='CLIENT', posting_date='2026-10-01', amount_usd=10,
            result='Saldo a favor documentado', credit_pending_usd=6, credit_resolved_usd=4,
            credit_management_status='Parcial', accounting_status='Asiento informado')]
        balances = {item['name']: financial_balance(item) for item in items}
        return build_position(collections, applications, items, balances,
            [dict(name='CLIENT', employer='E', client_number='1', client_name='Ana', national_id='ID')], filters or {})

    def test_position_does_not_duplicate_application_or_net_customer_credit(self):
        rows = self.build()
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0]['applied_usd'])
        self.assertIsNone(rows[0]['remitted_usd'])
        self.assertEqual(rows[1]['applied_usd'], 70)
        self.assertEqual(rows[1]['applied_pending_usd'], 20)
        self.assertEqual(rows[2]['credit_pending_usd'], 6)
        self.assertEqual(rows[2]['credit_resolved_usd'], 4)
        self.assertEqual([entry['value'] for entry in summary(rows)], [20, 6])
        self.assertEqual(rows[2]['client_name'], 'Ana')
        self.assertEqual(rows[2]['source_document'], 'C')
        self.assertTrue(rows[2]['is_open'])

    def test_client_company_date_kind_and_open_filters(self):
        self.assertEqual(len(self.build({'client_number': '1'})), 3)
        self.assertEqual(self.build({'client_number': '2'}), [])
        self.assertEqual(self.build({'employer': 'OTHER'}), [])
        self.assertEqual(len(self.build({'position_type': 'Partida complementaria'})), 1)
        self.assertEqual(len(self.build({'from_month': '2026-10-01', 'to_month': '2026-10-31'})), 1)
        self.assertEqual(len(self.build({'only_open': 1})), 3)

    def test_reconciliation_status_matches_displayed_state_and_combines_with_type(self):
        for state, kind in [('Depósito parcial', 'Cobranza'), ('Pendiente', 'Aplicación'), ('Documentado', 'Partida complementaria')]:
            with self.subTest(state=state):
                rows = self.build({'operational_status': state, 'position_type': kind, 'employer': 'E', 'only_open': 1})
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['operational_status'], state)
                self.assertEqual(rows[0]['position_type'], kind)
                totals = summary(rows)
                self.assertEqual(totals[0]['value'], 20 if kind == 'Aplicación' else 0)
                self.assertEqual(totals[1]['value'], 6 if kind == 'Partida complementaria' else 0)
        self.assertEqual(self.build({'operational_status': 'Pendiente', 'position_type': 'Cobranza'}), [])
        self.assertEqual(self.build({'operational_status': 'Parcial'}), [])  # Exact, not substring matching.
        self.assertEqual(len(self.build({'operational_status': '', 'position_type': ''})), 3)

    def test_extended_collection_state_can_be_filtered_verbatim(self):
        status = 'Conciliado con movimiento de conciliación -0.0100 US$ · deducción inferida por depósito, sin detalle de planilla'
        collections = [dict(period='P', payroll_month='2025-04-01', operational_status=status)]
        rows = build_position(collections, [], [], {}, [], {'operational_status': status})
        self.assertEqual(len(rows), 1)
        self.assertEqual(build_position(collections, [], [], {}, [], {'operational_status': 'Conciliado'}), [])

    def test_company_credit_not_assigned_to_person_and_accounting_task_stays_open(self):
        item = dict(name='C', category='Saldo a favor de la empresa', docstatus=1, employer='E',
            client_number='1', loan_number='L', posting_date='2025-01-01', amount_usd=10,
            result='Saldo a favor documentado', credit_pending_usd=0, credit_management_status='Resuelto',
            accounting_status='Asiento informado')
        balances = {'C': financial_balance(item)}
        self.assertEqual(build_position([], [], [item], balances, [], {'client_number': '1'}), [])
        rows = build_position([], [], [item], balances, [], {'only_open': 1})
        self.assertEqual(rows[0]['client_name'], 'Sin cliente individual')
        self.assertEqual(rows[0]['loan_number'], '')
        self.assertEqual(rows[0]['credit_pending_usd'], 0)

    def test_history_unlinked_and_fully_adjusted_applications_remain_traceable(self):
        sources = [dict(name='A1', parent='I', currency='USD', amount=100, event_type='Aplicacion',
            event_date='2025-04-30', effective=1, match_status='Conciliado', historical_period='H',
            processing_route='Historica', historical_remitted_usd=60),
            dict(name='A2', parent='I', currency='USD', amount=90, event_type='Aplicacion',
                event_date='2025-05-30', effective=1, application_adjustment_usd=90, processing_route='Historica'),
            dict(name='A3', parent='I', currency='USD', amount=20, event_type='Aplicacion',
                event_date='2025-06-30', effective=1, processing_route='Operativa')]
        apps = application_balances(sources, {'I': {'employer': 'E'}}, {'H': {'name': 'H', 'employer': 'E'}},
            {}, {'E': {'grace_days': 10}}, '2026-10-03', include_settled=True)
        rows = build_position([], apps, [], {}, [], {})
        self.assertEqual(len(rows), 3)
        self.assertEqual([row['applied_pending_usd'] for row in rows], [40, 0, 20])
        self.assertEqual([row['source_rows'] for row in rows], ['A1', 'A2', 'A3'])
        self.assertEqual(rows[1]['operational_status'], 'Conciliado')
        self.assertEqual(len(build_position([], apps, [], {}, [], {'only_open': 1})), 2)

    def test_no_period_does_not_hide_apps_or_credits_and_columns_are_scoped(self):
        with patch.object(report, '_collection_report', return_value=([], [])), \
             patch.object(report, 'load_position', return_value=self.build()), patch.object(report, '_', side_effect=lambda x: x):
            columns, data, message, _, totals = report.execute()
        self.assertEqual(len(data), 3)
        self.assertIn('no saldo contractual', message)
        self.assertEqual(next(c for c in columns if c['fieldname'] == 'source_document')['fieldtype'], 'Dynamic Link')
        app_fields = {c['fieldname'] for c in report.get_columns({'position_type': 'Aplicación'})}
        self.assertIn('applied_pending_usd', app_fields)
        self.assertNotIn('employee_pending_usd', app_fields)


if __name__ == '__main__':
    unittest.main()
