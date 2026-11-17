import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.application_deposit_status import RENAMED, SETTLED, current
from credinomina_reconciliation.patches.v1_0 import simplify_application_deposit_status as migration
from credinomina_reconciliation.period_closure import validate_direct_applications


class ApplicationDepositStatusTests(unittest.TestCase):
    def test_only_requested_labels_change_and_unknown_statuses_are_preserved(self):
        self.assertEqual(RENAMED, {'Depósito conciliado': 'Conciliado', 'Sin deposito': 'No conciliado',
            'Depósito parcial': 'Conciliación parcial', 'Aplicación compensada': 'Compensada'})
        for old, new in RENAMED.items():
            self.assertEqual(current(old), new)
            self.assertEqual(current(new), new)
        for status in ('Pendiente', 'Conciliada: depósito + ajuste', 'Diferencia de importe',
                       'Falta tipo de cambio', 'Ambiguo', '', None):
            self.assertEqual(current(status), status)

    def test_migration_updates_only_the_status_in_import_children_without_save_or_reconcile(self):
        with patch.object(frappe, 'db', Mock()) as db, patch.object(frappe, 'get_doc') as load:
            migration.execute()
        self.assertEqual(db.set_value.call_count, 4)
        for old, new in RENAMED.items():
            db.set_value.assert_any_call('CN Source Row', {'parenttype': 'CN Accounting Import',
                'parentfield': 'rows', 'deposit_match_status': old}, 'deposit_match_status', new,
                update_modified=False)
        load.assert_not_called()
        db.commit.assert_not_called()

    def test_schema_has_new_options_and_keeps_all_remaining_options(self):
        root = Path(__file__).resolve().parents[1] / 'credinomina_reconciliation'
        schema = json.loads((root / 'conciliacion_credinomina/doctype/cn_source_row/cn_source_row.json').read_text())
        field = next(field for field in schema['fields'] if field['fieldname'] == 'deposit_match_status')
        self.assertEqual(set(field['options'].splitlines()), set(RENAMED.values()) | {
            'Pendiente', 'Conciliada: depósito + ajuste', 'Diferencia de importe', 'Falta tipo de cambio', 'Ambiguo'})
        self.assertEqual(field['read_only'], 1)
        self.assertIn('credinomina_reconciliation.patches.v1_0.simplify_application_deposit_status',
                      (root / 'patches.txt').read_text())

    def test_old_and_new_settled_labels_still_require_real_funding_before_close(self):
        for status in SETTLED:
            compensated = current(status) == 'Compensada'
            for shortfall in (False, True):
                with self.subTest(status=status, shortfall=shortfall):
                    row = frappe._dict(name='APP', event_type='Aplicacion', currency='USD', amount=100,
                        application_adjustment_usd=100 if compensated else 0, match_status='Conciliado',
                        historical_remitted_usd=0 if compensated else (99 if shortfall else 100),
                        historical_balance_usd=1 if shortfall else 0, historical_detail='[]',
                        deposit_match_status=status)
                    with patch.object(frappe, 'get_all', return_value=[row]):
                        if shortfall:
                            with self.assertRaises(frappe.ValidationError):
                                validate_direct_applications(frappe._dict(name='P'))
                        else:
                            validate_direct_applications(frappe._dict(name='P'))
