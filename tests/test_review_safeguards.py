"""Financial evidence, classification and compact views must not invent balances."""
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import exception_policy as policy
from credinomina_reconciliation import receivable_recovery as recovery
from credinomina_reconciliation.complementary_balances import _distribution_index
from credinomina_reconciliation.deposit_adjustment_receivables import load_settlements
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement


def reject(message, error=frappe.ValidationError, **kwargs):
    raise error(message)


class ReviewSafeguards(unittest.TestCase):
    def test_hidden_related_distribution_never_becomes_zero(self):
        hidden = frappe._dict(name='SECRET-DEPOSIT', allocation_detail=json.dumps([
            dict(tipo='Partida complementaria', partida='A', importe_usd=-10)]))
        with patch('credinomina_reconciliation.report_records.records', return_value=[]), \
                patch.object(frappe, 'get_all', return_value=[hidden]), \
                patch.object(frappe, 'throw', side_effect=reject):
            with self.assertRaises(frappe.PermissionError) as error:
                _distribution_index({'A'})
            self.assertIn('Saldo no determinable', str(error.exception))
            self.assertNotIn(hidden.name, str(error.exception))
            self.assertNotIn('-10', str(error.exception))
            self.assertEqual(_distribution_index({'UNRELATED'}), {})

    def test_hidden_recovery_cannot_reopen_receivable(self):
        with patch('credinomina_reconciliation.deposit_adjustment_receivables.records', return_value=[]), \
                patch.object(frappe, 'get_all', return_value=[frappe._dict(name='HIDDEN-RECEIPT')]), \
                patch.object(frappe, 'throw', side_effect=reject), self.assertRaises(frappe.PermissionError):
            load_settlements([dict(name='ORIGIN')])

    def test_readable_dependency_keeps_distribution(self):
        deposit = frappe._dict(name='D', deposit_date='2025-01-01', allocation_detail=json.dumps([
            dict(tipo='Partida complementaria', partida='A', importe_usd=10)]))
        with patch('credinomina_reconciliation.report_records.records', return_value=[deposit]), \
                patch.object(frappe, 'get_all', return_value=[deposit]):
            self.assertEqual(_distribution_index({'A'})['A'][0]['amount_usd'], 10)

    def test_supervisor_reason_and_append_only_audit_for_reclassification(self):
        previous = frappe._dict(control_area='Financiera', blocks_closure=1)
        doc = frappe._dict(control_area='Calidad de cobranza', blocks_closure=0, append=Mock())
        with patch.object(frappe, 'get_roles', return_value=['Operador Credinomina']), \
                patch.object(frappe, 'throw', side_effect=reject), self.assertRaises(frappe.PermissionError):
            policy.validate_policy(doc, previous)
        with patch.object(frappe, 'get_roles', return_value=['Supervisor Credinomina']), \
                patch.object(frappe, 'throw', side_effect=reject):
            with self.assertRaises(frappe.ValidationError):
                policy.validate_policy(doc, previous)
            doc.classification_reason = 'Diferencia informativa; aplicación íntegramente cubierta'
            policy.validate_policy(doc, previous)
        self.assertEqual(doc.append.call_args.args[0], 'follow_up_actions')
        self.assertIn(doc.classification_reason, doc.append.call_args.args[1]['details'])

    def test_closed_nonblocking_case_allows_only_management_not_financial_edits(self):
        previous = frappe._dict(period='CLOSED', employer='E', amount_usd=20,
                                control_area='Calidad de cobranza', blocks_closure=0)
        doc = frappe._dict(previous, next_action='Consultar al core', status='En revision')
        self.assertTrue(policy.can_follow_up_closed(doc, previous))
        for field, value in [('amount_usd', 21), ('period', 'OTHER'), ('blocks_closure', 1), ('employer', 'OTHER')]:
            with self.subTest(field=field):
                self.assertFalse(policy.can_follow_up_closed(frappe._dict(doc, **{field: value}), previous))
        self.assertFalse(policy.can_follow_up_closed(doc, None))

    def test_legacy_case_keeps_block_until_explicitly_classified(self):
        doc = frappe._dict(control_area='Legado por revisar', blocks_closure=0,
                           classification_reason='Sin revisar')
        with patch.object(frappe, 'get_roles', return_value=['Supervisor Credinomina']), \
                patch.object(frappe, 'throw', side_effect=reject), self.assertRaises(frappe.ValidationError):
            policy.validate_policy(doc)

    def test_accounting_link_cannot_bypass_existing_financial_block(self):
        previous = frappe._dict(control_area='Financiera', blocks_closure=1)
        doc = frappe._dict(previous, complementary_item='COMP')
        with patch.object(frappe, 'throw', side_effect=reject), self.assertRaises(frappe.ValidationError):
            policy.validate_policy(doc, previous)
        self.assertEqual(doc.blocks_closure, 1)

    def test_closed_management_normalizes_empty_fields_without_changing_money(self):
        previous = frappe._dict(period='CLOSED', blocks_closure=0, client_number=None,
                                amount_usd=20.0, amount_nio=None)
        doc = frappe._dict(previous, client_number='', amount_usd='20.00', amount_nio=0)
        self.assertTrue(policy.can_follow_up_closed(doc, previous))
        doc.amount_usd = '20.01'
        self.assertFalse(policy.can_follow_up_closed(doc, previous))

    def test_cash_selector_respects_reserved_targets_and_documented_credit(self):
        deposit = frappe._dict(amount_usd=100, allocated_usd=40, justified_surplus_usd=10,
            targets=[dict(amount_usd=40, result='Aplicada'), dict(amount_usd=20), dict(amount_usd=-10)])
        self.assertEqual(recovery.cash_available(deposit), 30)

    def test_destination_preview_is_bounded_by_receivable_and_cash(self):
        doc = frappe._dict(name='D')
        with patch.object(recovery, '_recovery_scope', return_value=dict(available_usd=10)), \
                patch.object(recovery, '_candidate_value', return_value=7), \
                patch.object(frappe, 'get_doc', return_value=doc):
            value = recovery.get_recovery_destination('ORIGIN', 'E', 'Depósito', 'D')
        self.assertEqual(value, dict(name='D', available_usd=7, receivable_usd=10, suggested_usd=7))

    def test_compact_detail_preserves_full_matrix_and_unknown_balances(self):
        compact = statement.get_columns(dict(view_mode='Detalle', compact_view=1))
        full = statement.get_columns(dict(view_mode='Detalle', compact_view=0))
        self.assertEqual({c['fieldname'] for c in compact}, {c['fieldname'] for c in full})
        self.assertEqual([c['fieldname'] for c in compact if not c.get('hidden')],
            ['employer', 'client_name', 'position_type', 'balance_usd', 'status', 'source_document'])
        rows = [dict(employer='E', balance_usd=None, status='Sin conversión US$')]
        self.assertEqual(statement._presentation(rows, dict(view_mode='Detalle', compact_view=1))[0]['balance_usd'], None)

    def test_financial_status_migration_is_idempotent_and_preserves_engine_result(self):
        from credinomina_reconciliation.patches.v1_0 import backfill_deposit_financial_status as migration
        row = frappe._dict(name='D', docstatus=1, result='Parcial con saldo a favor', amount_usd=100,
            allocated_usd=80, justified_surplus_usd=20, unclassified_usd=0)
        writes = []
        def write(doctype, name, field, value, **kwargs):
            writes.append((field, value)); row[field] = value
            self.assertFalse(kwargs['update_modified'])
        with patch.object(migration, '_pages', side_effect=lambda *a, **k: [row]), \
                patch.object(frappe.db, 'set_value', side_effect=write):
            migration.execute(); migration.execute()
        self.assertEqual(writes, [('financial_status', 'Conciliado con saldo a favor')])
        self.assertEqual(row.result, 'Parcial con saldo a favor')
        self.assertEqual(row.amount_usd, 100)

    def test_exception_migration_preserves_old_cases_and_closed_state(self):
        from credinomina_reconciliation.patches.v1_0 import classify_exception_closure as migration
        row = frappe._dict(name='X', exception_type='No deducido', status='Abierta', amount_usd=20)
        writes = []
        def write(doctype, name, values, **kwargs):
            writes.append(values); row.update(values)
        with patch.object(migration, '_pages', side_effect=lambda *a, **k: [row]), \
                patch.object(frappe.db, 'set_value', side_effect=write):
            migration.execute(); migration.execute()
        self.assertEqual(writes, [dict(control_area='Legado por revisar', blocks_closure=1)])
        self.assertEqual((row.status, row.amount_usd), ('Abierta', 20))
