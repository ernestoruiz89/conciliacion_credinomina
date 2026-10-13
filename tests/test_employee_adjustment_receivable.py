"""An individual deposit shortfall is collectible without becoming company debt."""
import io
from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation import complementary_subcategories as categories
from credinomina_reconciliation import deposit_adjustment_receivables as receivables
from credinomina_reconciliation import receivable_recovery as recovery, receivable_terms
from credinomina_reconciliation.client_position import build_position
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.control_export import build_control_workbook
from credinomina_reconciliation.follow_up_queue import build_follow_up, filter_work


def origin(**changes):
    return frappe._dict(dict(name='EMPLOYEE-CXC', docstatus=1, employer='E',
        category=categories.ADJUSTMENT, subcategory=categories.EMPLOYEE_RECEIVABLE,
        subcategory_effect=categories.EMPLOYEE_RECEIVABLE, amount=-10, amount_usd=-10,
        currency='USD', posting_date='2025-04-30', period='P', credit_client='CLIENT',
        client_number='123', client_name='Ana', loan_number='123-1',
        credit_assigned_to='Administrator', credit_commitment_date='2025-06-30',
        receivable_due_date='2025-05-15', accounting_status='Registrada') | changes)


class EmployeeAdjustmentTests(unittest.TestCase):
    def test_employee_identity_required_verified_and_frozen(self):
        client = Mock(name='CLIENT')
        client.name, client.client_number, client.client_name = 'CLIENT', '123', 'Ana'
        client.as_dict.return_value = dict(name='CLIENT', employer='E', client_number='123', client_name='Ana')
        with patch.object(frappe, 'db', Mock(get_value=Mock(return_value=categories.EMPLOYEE_RECEIVABLE))), \
                patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                patch.object(frappe, 'get_doc', return_value=client), \
                patch('credinomina_reconciliation.deposit_identity.load_detail_loan_clients', return_value={'123-1': {'CLIENT'}}):
            doc = origin(docstatus=0, client_name='Stale')
            categories.validate_subcategory(doc)
            self.assertEqual(doc.client_name, 'Ana')
            client.check_permission.assert_called_with('read')
            for changes in (dict(credit_client=None, client_number=None), dict(client_number='OTHER'),
                            dict(generic_distribution=1), dict(amount_usd=10), dict(accounting_source_key='CORE'),
                            dict(loan_number='UNVERIFIED')):
                with self.subTest(changes=changes), self.assertRaises(frappe.ValidationError):
                    categories.validate_subcategory(origin(**changes))
            for changes in (dict(client_number='OTHER'), dict(credit_client='OTHER'), dict(employer='OTHER'),
                            dict(loan_number='OTHER'), dict(amount=-11), dict(subcategory='OTHER')):
                with self.subTest(changes=changes), self.assertRaises(frappe.ValidationError):
                    categories.validate_subcategory(origin(**changes), origin())
            categories.validate_subcategory(origin(amount=-10.0), origin())

    def test_employee_has_own_due_date_and_required_management(self):
        with patch.object(frappe, 'has_permission', side_effect=AssertionError('Do not inherit employer deadlines')):
            doc = origin()
            receivable_terms.validate_terms(doc)
        self.assertEqual(str(doc.receivable_due_date), '2025-05-15')
        self.assertIn('indicado', doc.receivable_due_origin)
        for field in ('credit_assigned_to', 'credit_commitment_date', 'receivable_due_date'):
            with self.subTest(field=field), patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                    self.assertRaises(frappe.ValidationError):
                receivable_terms.validate_terms(origin(**{field: None}))

    def test_actual_distribution_partial_recovery_and_cancellation(self):
        item = origin()
        balances = {item.name: {'distributions': []}}
        self.assertTrue(recovery.is_receivable(item))
        self.assertEqual(receivables.build_receivables([item], balances), [])
        balances[item.name]['distributions'] = [dict(deposit='D', employer='E', amount_usd=-10)]
        cash = dict(name='CASH', receivable_origin=item.name, employer='E', docstatus=1, category='Cobro de CxC')
        paid = {'CASH': {'distributions': [dict(amount_usd=3)]}}
        rows = receivables.build_receivables([item], balances, settlements=[cash], settlement_balances=paid)
        self.assertEqual(rows[0]['receivable_usd'], 7)
        self.assertEqual(rows[0]['client_number'], '123')
        detail = build_detail([], [], [], [], adjustment_receivables=rows)
        self.assertEqual(summarize(detail)[0]['company_receivable_usd'], 0)
        self.assertEqual(summarize(detail)[0]['employee_receivable_usd'], 7)
        self.assertEqual(build_detail([], [], [], [], {'component': 'company_receivable_usd'}, adjustment_receivables=rows), [])
        position = build_position([], [], [], {}, [], {}, adjustment_receivables=rows)[0]
        self.assertEqual(position['company_receivable_usd'], 0)
        self.assertEqual(position['employee_receivable_usd'], 7)
        task = build_follow_up([], {}, [], adjustment_receivables=rows)[0]
        self.assertEqual(task['kind'], 'employee_receivable')
        self.assertEqual(task['client_name'], 'Ana')
        self.assertEqual(filter_work([task], kind='complements'), [task])
        workbook = load_workbook(io.BytesIO(build_control_workbook(
            dict(year=2025, periods=[], totals={}, adjustment_receivables=rows),
            exceptions=[], actions=[], employer_label='E', generated_at=datetime(2026, 10, 13))), data_only=True)
        self.assertEqual(workbook['Partidas y excepciones']['A5'].value, 'Saldo por cobrar al empleado')
        cash['docstatus'] = 2
        self.assertEqual(receivables.build_receivables([item], balances, settlements=[cash], settlement_balances=paid)[0]['receivable_usd'], 10)

    def test_aging_keeps_company_and_employee_debt_in_separate_groups(self):
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as detail
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa import antiguedad_de_saldos_por_empresa as grouped
        rows = [origin(receivable_usd=10, receivable_status='Pendiente de cobro'),
                origin(name='COMPANY-CXC', subcategory_effect=categories.RECEIVABLE,
                       receivable_usd=20, receivable_status='Pendiente de cobro')]
        with patch.object(detail, 'load_application_context', return_value=([], {}, {}, {}, {})), \
                patch.object(receivables, 'load_receivables', return_value=rows):
            result = grouped.execute({'balance_type': 'CxC por ajustes', 'as_of_date': '2025-05-20'})
        by_debtor = {row['debtor_type']: row for row in result[1]}
        self.assertEqual(by_debtor['Empleado']['amount_usd'], 10)
        self.assertEqual(by_debtor['Empresa']['amount_usd'], 20)
        self.assertEqual(by_debtor['Empleado']['days_1_15'], 10)

    def test_compensation_rejects_another_person_credit_or_unidentified_counterpart(self):
        recovery._check_employee_counterpart(origin(), origin(credit_client=None))
        for changes in (dict(client_number='OTHER'), dict(client_number=None), dict(loan_number='OTHER'),
                        dict(credit_client='OTHER')):
            with self.subTest(changes=changes), patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                    self.assertRaises(frappe.ValidationError):
                recovery._check_employee_counterpart(origin(), origin(**changes))

    def test_receipt_identity_cannot_be_reassigned(self):
        before = frappe._dict(receivable_origin='EMPLOYEE-CXC', category='Cobro de CxC',
            client_number='123', credit_client='CLIENT', loan_number='123-1')
        for field in ('client_number', 'credit_client', 'loan_number'):
            with self.subTest(field=field), patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                    self.assertRaises(frappe.ValidationError):
                recovery.guard_receipt(frappe._dict(before, **{field: 'OTHER'}), before)

    def test_migration_seeds_once_and_does_not_reclassify_old_items(self):
        from credinomina_reconciliation.patches.v1_0.add_employee_receivable_subcategory import execute
        existing = {name for name, _ in categories.DEFAULTS if name != categories.EMPLOYEE_RECEIVABLE}
        inserted = []
        def document(values):
            def insert(**kwargs):
                inserted.append(values)
                existing.add(values['subcategory_name'])
            return Mock(insert=insert)
        with patch.object(frappe, 'db', Mock(exists=lambda dt, name: name in existing)) as db, \
                patch.object(frappe, 'reload_doc'), patch.object(frappe, 'get_doc', side_effect=document):
            execute()
            execute()
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]['effect'], categories.EMPLOYEE_RECEIVABLE)
        db.set_value.assert_not_called()
