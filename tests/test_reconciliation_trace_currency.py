import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.patches.v1_0 import set_reconciliation_trace_currency


class TraceCurrencyTests(unittest.TestCase):
    def test_backfill_only_labels_existing_usd_amounts_without_modifying_audit_metadata(self):
        with patch.object(frappe, 'reload_doc') as schema, patch.object(frappe.db, 'set_value') as write:
            set_reconciliation_trace_currency.execute()
        schema.assert_called_once_with('conciliacion_credinomina', 'doctype', 'cn_reconciliation_trace')
        write.assert_called_once_with('CN Reconciliation Trace', {'usd_currency': ['is', 'not set']},
            'usd_currency', 'USD', update_modified=False)
