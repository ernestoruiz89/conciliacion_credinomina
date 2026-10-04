import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from credinomina_reconciliation import company_statement as statement
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


class CompanyStatementTests(unittest.TestCase):
    def app(self, **changes):
        return dict(dict(employer='REPSA', source_import='I', source_rows='R',
            application_date='2025-11-30', amount_usd=4474.07), **changes)

    def core(self, **changes):
        item = dict(dict(name='C', employer='REPSA', category='Por clasificar', docstatus=0,
            amount_usd=7.35, source_credit=269.19, source_debit=0, source_date='2025-11-30',
            accounting_source_key='KEY', source_voucher='00101522'), **changes)
        item['_balance'] = financial_balance(item)
        item['_signed_pending_usd'] = signed_pending(item, item['_balance'])
        return item

    def credit(self, **changes):
        return dict(dict(name='SF', employer='REPSA', category='Saldo a favor del cliente', docstatus=1,
            result='Saldo a favor documentado', amount_usd=164.28, credit_pending_usd=154.28,
            credit_management_status='Parcial', posting_date='2025-11-30', client_number='1'), **changes)

    def deposit(self, **changes):
        return dict(dict(name='D', employer='REPSA', docstatus=1, amount_usd=3500,
            allocated_usd=161.73, justified_surplus_usd=164.46, deposit_date='2025-12-30'), **changes)

    def example(self):
        return statement.build_detail([self.app()], [self.core()],
            [self.credit(), self.credit(name='SFE', category='Saldo a favor de la empresa',
                amount_usd=.18, credit_pending_usd=.18)], [self.deposit()])

    def test_repsa_example_and_detail_same_financial_columns(self):
        detail = self.example()
        summary = statement.summarize(detail)
        self.assertEqual(summary, [dict(employer='REPSA', usd_currency='USD',
            application_pending_usd=4474.07, core_pending_usd=-7.35,
            company_credit_usd=-.18, client_credit_usd=-154.28,
            deposit_pending_usd=-3173.81, balance_usd=1138.45)])
        for row in detail:
            self.assertEqual(len([field for field in statement.FIELDS if row[field]]), 1)
            self.assertIn('source_document', row)
        compact = [c for c in report.get_columns() if c['fieldtype'] == 'Currency']
        expanded = [c for c in report.get_columns({'view_mode': 'Detalle'}) if c['fieldtype'] == 'Currency']
        self.assertEqual(compact, expanded)

    def test_summary_default_detail_uses_identical_source_and_filters(self):
        data = self.example()
        with patch.object(report, 'load_detail', return_value=data) as load:
            result = report.execute({'employer': 'REPSA'})
            load.assert_called_once_with({'employer': 'REPSA'})
            self.assertEqual(len(result[1]), 1)
            self.assertEqual(result[1][0]['balance_usd'], 1138.45)
            self.assertEqual(report.execute({'view_mode': 'Detalle'})[1], data)

    def test_no_duplicate_credits_or_offsets_and_no_canceled_items(self):
        core = [self.core(category='Ajuste de aplicación', docstatus=1, related_application='R',
                          application_adjustment_usd=7.35), self.core(name='CANCELED', docstatus=2),
                self.core(name='EVIDENCE', registration_exception='EXC')]
        shared_credit = self.core(category='Saldo a favor del cliente', docstatus=1,
                                 result='Saldo a favor documentado')
        detail = statement.build_detail([self.app(amount_usd=10)], core + [shared_credit],
            [self.credit(name='C', amount_usd=7.35, credit_pending_usd=2)],
            [self.deposit(amount_usd=17.35, allocated_usd=10, justified_surplus_usd=7.35)])
        summary = statement.summarize(detail)[0]
        self.assertEqual(summary['core_pending_usd'], 0)
        self.assertEqual(summary['deposit_pending_usd'], 0)
        self.assertEqual(summary['client_credit_usd'], -2)
        self.assertEqual(summary['balance_usd'], 8)

    def test_fully_resolved_credits_do_not_reappear_as_unused_cash(self):
        detail = statement.build_detail([], [], [self.credit(credit_pending_usd=0)],
            [self.deposit(amount_usd=164.28, allocated_usd=0, justified_surplus_usd=164.28)])
        self.assertEqual(detail, [])
        self.assertEqual(statement.summarize(detail), [])

    def test_core_draft_is_included_manual_draft_and_cancelled_deposits_are_not(self):
        detail = statement.build_detail([], [], [], [
            self.deposit(name='CORE', docstatus=0, accounting_source_key='KEY', amount_usd=100),
            self.deposit(name='MANUAL', docstatus=0), self.deposit(name='CANCEL', docstatus=2)])
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]['deposit_pending_usd'], -100)
        self.assertEqual(detail[0]['status'], 'Importado del core; sin confirmar')

    def test_partial_core_uses_only_remaining_with_source_debit_sign(self):
        item = self.core(category='Compensación entre partidas', compensated_usd=5,
                         source_debit=269.19, source_credit=0)
        rows = statement.build_detail([], [item], [], [])
        self.assertEqual(rows[0]['core_pending_usd'], 2.35)

    def test_unknown_amount_propagates_to_company_net_not_to_other_companies(self):
        rows = statement.build_detail([self.app(amount_usd=None), self.app(employer='B', amount_usd=20)],
                                      [], [], [self.deposit(amount_usd=5, allocated_usd=0, justified_surplus_usd=0)])
        totals = {row['employer']: row for row in statement.summarize(rows)}
        self.assertIsNone(totals['REPSA']['application_pending_usd'])
        self.assertIsNone(totals['REPSA']['balance_usd'])
        self.assertEqual(totals['B']['balance_usd'], 20)

    def test_date_scope_and_company_apply_to_origin_not_settlement_date(self):
        detail = statement.build_detail([self.app(amount_usd=20)], [], [],
            [self.deposit(amount_usd=10, allocated_usd=0, justified_surplus_usd=0)],
            {'employer': 'REPSA', 'from_date': '2025-11-01', 'to_date': '2025-11-30'})
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]['position_type'], 'Aplicación')
        self.assertEqual(statement.build_detail([self.app()], [], [], [], {'employer': 'OTHER'}), [])

    def test_unconfirmed_credit_is_not_a_confirmed_beneficiary_balance(self):
        self.assertEqual(statement.build_detail([], [], [self.credit(docstatus=0)], []), [])

    def test_denied_permission_fails_instead_of_showing_an_incomplete_net(self):
        with patch.object(frappe, 'has_permission', return_value=False), \
             patch.object(frappe, 'throw', side_effect=frappe.PermissionError), \
             patch.object(statement, 'load_application_context') as context:
            with self.assertRaises(frappe.PermissionError):
                statement.load_detail({})
            context.assert_not_called()

    def test_loaders_use_permission_scoped_parents_and_no_date_cut_on_settlement(self):
        def read(doctype, **kwargs):
            self.assertEqual(kwargs['filters']['employer'], 'REPSA')
            self.assertNotIn('deposit_date', kwargs['filters'])
            self.assertNotIn('posting_date', kwargs['filters'])
            return [self.credit()] if doctype == 'CN Complementary Item' else [self.deposit()]
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(statement, 'load_application_context', return_value=([], {}, {}, {}, {})), \
             patch.object(statement, 'application_balances', return_value=[self.app()]), \
             patch.object(statement, 'load_core_items', return_value=[self.core()]) as core, \
             patch.object(statement, 'records', side_effect=read):
            data = statement.load_detail({'employer': 'REPSA', 'from_date': '2025-11-01'})
        core.assert_called_once_with(employer='REPSA')
        self.assertEqual(len(data), 4)

    def test_workspace_roles_and_migration(self):
        root = Path(__file__).resolve().parents[1] / 'credinomina_reconciliation'
        meta = json.loads((root / 'conciliacion_credinomina/report/estado_de_cuenta_por_empresa/estado_de_cuenta_por_empresa.json').read_text(encoding='utf-8'))
        workspace = json.loads((root / 'conciliacion_credinomina/workspace/conciliacion_credinomina/conciliacion_credinomina.json').read_text(encoding='utf-8'))
        self.assertEqual({r['role'] for r in meta['roles']}, {'System Manager', 'Supervisor Credinomina', 'Operador Credinomina'})
        self.assertEqual(sum(row.get('link_to') == meta['name'] for row in workspace['links']), 1)
        from credinomina_reconciliation.patches.v1_0.add_company_statement_report import execute
        with patch.object(frappe, 'reload_doc') as reload, \
             patch('credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow.execute') as refresh:
            execute()
        reload.assert_called_once_with('conciliacion_credinomina', 'report', 'estado_de_cuenta_por_empresa')
        refresh.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
