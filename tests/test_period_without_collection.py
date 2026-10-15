import unittest
from unittest.mock import patch

import frappe
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller
from credinomina_reconciliation.period_closure import validate_direct_applications
from credinomina_reconciliation.period_pending import application_issue


class PeriodWithoutCollectionTests(unittest.TestCase):
    def period(self, **values):
        return frappe._dict(dict(name='P', doctype='CN Reconciliation Period', employer='E',
            payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual',
            no_collection=1, collection_rows=[], get_doc_before_save=lambda: None), **values)

    def test_cannot_hide_current_or_removed_collection_evidence(self):
        validate = controller.CNReconciliationPeriod._validate_no_collection
        validate(self.period())
        for field, value in [('collection_rows', [dict(name='ROW')]), ('collection_file', '/private/files/base.csv'),
                             ('provisional_adjustments', [dict(name='ADJ')]), ('prepared_deposit', 'DEP')]:
            for old in (False, True):
                with self.subTest(field=field, previous=old), patch.object(frappe, 'throw', side_effect=ValueError):
                    period = self.period(**({} if old else {field: value}))
                    if old:
                        period.get_doc_before_save = lambda: frappe._dict({field: value})
                    with self.assertRaises(ValueError):
                        validate(period)

    def test_separate_period_can_coexist_but_cannot_duplicate_same_cycle(self):
        candidate = frappe._dict(name='REGULAR', reconciliation_mode='Operativa', collection_cycle='Mensual', no_collection=0)
        with patch.object(frappe, 'get_all', return_value=[candidate]), patch.object(frappe, 'throw', side_effect=ValueError):
            controller.CNReconciliationPeriod._validate_unique_period(self.period())
            candidate.no_collection = 1
            with self.assertRaises(ValueError):
                controller.CNReconciliationPeriod._validate_unique_period(self.period())

    def test_closure_requires_effective_application_and_full_funding(self):
        source = frappe._dict(name='APP', match_status='Conciliado', historical_balance_usd=0,
                             deposit_match_status='Depósito conciliado', quality_status='Sin cobranza vinculada')
        with patch.object(frappe, 'get_all', return_value=[source]):
            validate_direct_applications(self.period())
        for rows in ([], [frappe._dict(source, historical_balance_usd=.01)],
                     [frappe._dict(source, match_status='Excepcion')]):
            with patch.object(frappe, 'get_all', return_value=rows), patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
                validate_direct_applications(self.period())

    def test_first_stage_not_pending_but_unpaid_application_stays_visible(self):
        row = dict(name='APP', processing_route='Operativa', quality_status='Sin cobranza vinculada')
        settled = dict(state='Conciliado', net_usd=100, assigned_usd=100, pending_usd=0)
        self.assertIsNone(application_issue(row, settled, no_collection=True))
        self.assertEqual(application_issue(row, settled)['status'], 'Conciliación 1 pendiente')
        issue = application_issue(row, dict(state='Pendiente', net_usd=100, assigned_usd=0, pending_usd=100), no_collection=True)
        self.assertEqual(issue['pending'], 100)
        self.assertNotEqual(issue['status'], 'Conciliación 1 pendiente')

    def test_import_api_rejects_collection(self):
        with patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            controller._assert_editable(self.period())


if __name__ == '__main__':
    unittest.main()
