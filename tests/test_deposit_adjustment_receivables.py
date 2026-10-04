import io
import json
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, Mock

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation import complementary_subcategories as categories
from credinomina_reconciliation import deposit_adjustment_receivables as receivables
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.control_export import build_control_workbook
from credinomina_reconciliation.follow_up_queue import build_follow_up, filter_work


class AdjustmentReceivableTests(unittest.TestCase):
    def item(self, **changes):
        return frappe._dict(dict(name='CN-COMP-2026-00544', category=categories.ADJUSTMENT,
            subcategory=categories.RECEIVABLE, subcategory_effect=categories.RECEIVABLE,
            employer='REPSA', docstatus=1, amount_usd=-.01, description='CxC a la Empresa',
            accounting_status='Pendiente de registro', posting_date='2025-09-30', **changes))

    def build(self, item=None, distributions=None, **filters):
        item = item or self.item()
        return receivables.build_receivables([item], {item.name: {'distributions': distributions if distributions is not None else [
            dict(deposit='DEP-9-2025-0060', amount_usd=-.01, employer='REPSA')]}}, **filters)

    def test_only_actual_negative_distribution_creates_debt(self):
        self.assertEqual(self.build()[0]['receivable_usd'], .01)
        self.assertEqual(self.build(distributions=[]), [])  # Selected targets are not cash evidence.
        self.assertEqual(self.build(distributions=[dict(amount_usd=.01)]), [])
        for changes in [dict(docstatus=0), dict(docstatus=2), dict(accounting_source_key='CORE'),
                        dict(amount_usd=.01), dict(subcategory_effect=categories.OTHER),
                        dict(subcategory_effect=None), dict(review_action='No conciliatoria')]:
            item = self.item()
            item.update(changes)
            self.assertEqual(self.build(item), [])

    def test_registration_does_not_settle_receivable(self):
        item = self.item()
        item.update(accounting_status='Registrada', accounting_exception='EXC')
        row = self.build(item)[0]
        self.assertEqual(row['receivable_usd'], .01)
        self.assertEqual(row['receivable_status'], 'Pendiente de cobro')

    def test_partial_and_cross_company_uses_are_attributed_once(self):
        item = self.item()
        item.amount_usd = -5
        distributions = [dict(deposit='D1', amount_usd=-1.25, employer='REPSA', period='P1'),
                         dict(deposit='D2', amount_usd=-2, employer='B', period='P2')]
        values = self.build(item, distributions)
        self.assertEqual({row['employer']: row['receivable_usd'] for row in values}, {'B': 2, 'REPSA': 1.25})
        self.assertEqual(self.build(item, distributions, employer='B')[0]['related_deposits'], 'D2')
        self.assertEqual(self.build(item, distributions, year=2026), [])

    def test_decimal_net_and_company_statement_do_not_count_application_twice(self):
        rows = self.build(distributions=[dict(deposit='D', amount_usd=-.03), dict(deposit='D', amount_usd=.02)])
        detail = build_detail([], [], [], [], adjustment_receivables=rows)
        self.assertEqual(summarize(detail)[0]['company_receivable_usd'], .01)
        self.assertEqual(summarize(detail)[0]['balance_usd'], .01)
        self.assertEqual(detail[0]['related_deposits'], 'D')

    def test_work_queue_remains_visible_without_accounting_exception(self):
        task = build_follow_up([], {}, [], adjustment_receivables=self.build())[0]
        self.assertEqual(task['kind'], 'company_receivable')
        self.assertEqual(task['amount_usd'], .01)
        self.assertEqual(task['target_name'], 'CN-COMP-2026-00544')
        self.assertEqual(filter_work([task], kind='exceptions'), [task])

    def test_export_contains_positive_company_receivable_and_trace(self):
        content = build_control_workbook(dict(year=2025, periods=[], totals={}, adjustment_receivables=self.build()),
            exceptions=[], actions=[], employer_label='REPSA', generated_at=datetime(2026, 10, 4))
        sheet = load_workbook(io.BytesIO(content), data_only=True)['Partidas y excepciones']
        self.assertEqual(sheet['A5'].value, 'Saldo por cobrar a la empresa')
        self.assertEqual(sheet['H5'].value, .01)
        self.assertIn('DEP-9-2025-0060', sheet['P5'].value)

    def test_loader_filters_explicit_classification_and_uses_all_time_deposits(self):
        item = self.item()
        with patch.object(receivables, 'records', return_value=[item]) as read, \
             patch.object(receivables, 'load_balances', return_value={item.name: {'distributions': []}}) as balances:
            self.assertEqual(receivables.load_receivables('REPSA', 2025), [])
        self.assertEqual(read.call_args.kwargs['filters']['subcategory_effect'], categories.RECEIVABLE)
        self.assertNotIn('employer', read.call_args.kwargs['filters'])
        balances.assert_called_once_with([item])


class SubcategoryTests(unittest.TestCase):
    def test_required_in_server_and_no_trust_in_client_effect(self):
        doc = frappe._dict(category=categories.ADJUSTMENT, amount_usd=-1)
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                categories.validate_subcategory(doc)
        doc.subcategory = 'Custom'
        doc.subcategory_effect = categories.RECEIVABLE
        with patch.object(frappe, 'db', Mock(get_value=Mock(return_value=categories.OTHER))):
            categories.validate_subcategory(doc)
        self.assertEqual(doc.subcategory_effect, categories.OTHER)

    def test_cxc_requires_manual_negative_and_cannot_be_cleared_after_confirmation(self):
        for changes in [dict(amount_usd=1), dict(amount_usd=-1, accounting_source_key='CORE')]:
            doc = frappe._dict(category=categories.ADJUSTMENT, subcategory='CxC', **changes)
            with patch.object(frappe, 'db', Mock(get_value=Mock(return_value=categories.RECEIVABLE))), \
                 patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    categories.validate_subcategory(doc)
        previous = frappe._dict(docstatus=1, subcategory='CxC', subcategory_effect=categories.RECEIVABLE, category=categories.ADJUSTMENT)
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                categories.validate_subcategory(frappe._dict(previous, subcategory='Other'), previous)

    def test_other_categories_do_not_require_or_preserve_an_effect(self):
        doc = frappe._dict(category='Otros ingresos', subcategory='CxC', subcategory_effect=categories.RECEIVABLE)
        categories.validate_subcategory(doc)
        self.assertIsNone(doc.subcategory)
        self.assertIsNone(doc.subcategory_effect)

    def test_migration_does_not_guess_from_negative_sign_or_partial_description(self):
        explicit = dict(description=' CxC a la Empresa ', amount_usd=-.01)
        self.assertEqual(categories.legacy_subcategory(explicit), (categories.RECEIVABLE, categories.RECEIVABLE))
        for changes in [dict(description='Posible CxC a la Empresa'), dict(amount_usd=.01), dict(accounting_source_key='CORE')]:
            self.assertEqual(categories.legacy_subcategory(explicit | changes)[0], categories.UNCLASSIFIED)

    def test_catalog_and_conditional_required_metadata(self):
        root = Path(__file__).resolve().parents[1] / 'credinomina_reconciliation/conciliacion_credinomina/doctype'
        meta = json.loads((root / 'cn_complementary_item/cn_complementary_item.json').read_text(encoding='utf-8'))
        field = next(f for f in meta['fields'] if f['fieldname'] == 'subcategory')
        self.assertEqual(field['fieldtype'], 'Link')
        self.assertIn('Ajuste de conciliación', field['mandatory_depends_on'])
        catalog = json.loads((root / 'cn_complementary_subcategory/cn_complementary_subcategory.json').read_text(encoding='utf-8'))
        self.assertEqual(field['options'], catalog['name'])
        self.assertEqual({r['role'] for r in catalog['permissions']}, {'System Manager', 'Supervisor Credinomina', 'Operador Credinomina'})

    def test_patch_preserves_existing_classifications_and_is_repeatable(self):
        from credinomina_reconciliation.patches.v1_0.add_complementary_subcategories import execute
        item = frappe._dict(name='C', description='CxC a la Empresa', amount_usd=-.01)
        db = Mock(exists=Mock(return_value=True))
        with patch.object(frappe, 'db', db), patch.object(frappe, 'reload_doc'), \
             patch.object(frappe, 'get_all', side_effect=[[item], []]) as query, \
             patch.object(frappe, 'clear_document_cache'), \
             patch('credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow.execute'):
            execute()
            execute()
        db.set_value.assert_called_once_with('CN Complementary Item', 'C',
            {'subcategory': categories.RECEIVABLE, 'subcategory_effect': categories.RECEIVABLE}, update_modified=False)
        self.assertEqual(query.call_args.kwargs['filters']['subcategory'], ['is', 'not set'])
