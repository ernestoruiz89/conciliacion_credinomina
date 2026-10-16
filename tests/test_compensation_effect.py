import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import credit_usage as usage, credit_effect_migration as migration
from credinomina_reconciliation.complementary_balances import financial_balance


class CompensationEffectTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(frappe, 'throw', side_effect=lambda message: (_ for _ in ()).throw(ValueError(message)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.refund = dict(operation_id='REFUND', importe_usd=19.25, fecha='2025-08-20', tratamiento='Devolución',
            referencia='001011316', observaciones='Realizado el 20/08/2025\nCONCEPTO:\nC$704.89 TRANSFERENCIA A/F DE DELVIN EN CONCEPTO DE REEMBOLSO DE CREDITO CANCELADO N°109586')
        self.offset = frappe._dict(operation_id='OFFSET', amount_usd=19.25, compensation_date='2025-11-21', counterpart='CORE',
            reason='Saldo a favor transferido:\nFECHA APLIC.: 20/08/2025\nCONCEPTO: C$704.89 TRANSFERENCIA A/F DE DELVIN EN CONCEPTO DE REEMBOLSO DE CREDITO CANCELADO N°109586\nCuenta contable afectada: 260199013001')
        self.item = frappe._dict(name='CREDIT', amount_usd=19.25, credit_history=json.dumps([self.refund]), compensations=[self.offset])

    def link(self):
        self.offset.update(credit_effect=usage.DOCUMENTARY, credit_management_item='CREDIT', credit_management_entry='REFUND')

    def test_refund_and_documentary_offset_consume_once(self):
        self.link()
        self.assertEqual(usage.totals(self.item)['credit_resolved_usd'], 19.25)
        self.assertEqual(usage.resolved_amount(self.item), 19.25)
        self.assertEqual(usage.totals(self.item)['credit_pending_usd'], 0)
        self.offset.credit_effect = usage.CONSUME
        self.offset.credit_management_entry = self.offset.credit_management_item = ''
        with self.assertRaises(ValueError):
            usage.totals(self.item)

    def test_migration_uses_concept_and_date_not_just_equal_amount(self):
        result = migration.proposed_links(self.item)
        self.assertEqual(result['OFFSET']['credit_management_entry'], 'REFUND')
        self.offset.reason = 'Reclasificación de otro saldo por 19.25'
        self.assertEqual(migration.proposed_links(self.item), {})
        self.offset.reason = 'Cliente 001011316'
        self.assertEqual(migration.proposed_links(self.item), {})
        self.offset.reason = 'Reclasificación comprobante 001011316'
        self.assertTrue(migration.proposed_links(self.item))

    def test_explicit_decisions_and_ambiguous_matches_are_not_overwritten(self):
        self.offset.credit_effect = usage.CONSUME
        self.assertEqual(migration.proposed_links(self.item), {})
        self.offset.credit_effect = ''
        self.item.credit_history = json.dumps([self.refund, dict(self.refund, operation_id='OTHER')])
        self.assertEqual(migration.proposed_links(self.item), {})

    def test_documentary_reverse_does_not_restore_already_refunded_credit(self):
        self.link()
        self.item.compensations.append(frappe._dict(self.offset, operation_id='REV', amount_usd=-19.25, compensation_date='2025-11-22'))
        self.assertEqual(usage.totals(self.item)['credit_pending_usd'], 0)

    def test_link_cannot_precede_management_or_exceed_its_capacity(self):
        self.link()
        self.offset.compensation_date = '2025-08-19'
        with self.assertRaises(ValueError):
            usage.totals(self.item)
        self.offset.compensation_date = '2025-11-21'
        self.item.compensations.append(frappe._dict(self.offset, operation_id='OTHER'))
        with self.assertRaises(ValueError):
            usage.totals(self.item)

    def test_deleting_or_reversing_a_linked_management_requires_releasing_link(self):
        self.link()
        for rows in ([], [self.refund, dict(operation_id='REV', reverses='REFUND', fecha='2025-12-01', importe_usd=-19.25)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                usage.totals(self.item, history=rows)

    def test_unresolved_migration_issue_blocks_new_usage_and_is_not_a_known_zero(self):
        self.item.update(docstatus=1, category='Saldo a favor del cliente', result='Saldo a favor documentado',
            credit_usage_issue='Vínculo ambiguo', credit_pending_usd=0, credit_management_status='Revisar vínculo')
        with self.assertRaises(ValueError):
            usage.totals(self.item)
        balance = financial_balance(self.item)
        self.assertEqual(balance['financial_status'], 'Revisar distribución')
        self.assertIsNone(balance['management_pending_usd'])
        self.assertEqual(balance['amount_issue'], 'Vínculo ambiguo')

    def test_effect_review_requires_permissions_before_returning_journals(self):
        from credinomina_reconciliation import compensation_effect
        self.item.check_permission = Mock(side_effect=PermissionError)
        with patch.object(frappe, 'get_doc', return_value=self.item):
            with self.assertRaises(PermissionError):
                compensation_effect.options('CREDIT')
            with self.assertRaises(PermissionError):
                compensation_effect.preview('CREDIT', 'OFFSET', usage.DOCUMENTARY, 'REFUND')
