import unittest
from unittest.mock import Mock, patch
import frappe
from credinomina_reconciliation.patches.v1_0 import initialize_company_credit_follow_up as migration


class CreditFollowUpMigrationTests(unittest.TestCase):
    def test_initializes_only_unknown_follow_up_and_is_idempotent(self):
        old = frappe._dict(name="OLD", amount_usd=20)
        partial = frappe._dict(name="PARTIAL", amount_usd=20, credit_management_status="Parcialmente resuelto", credit_resolved_usd=5)
        history = frappe._dict(name="HISTORY", amount_usd=20, credit_history='[{"importe_usd":2}]')
        database = Mock()
        database.set_value.side_effect = lambda dt, name, values, **kw: old.update(values)
        with patch.object(migration.frappe, "get_all", return_value=[old, partial, history]), patch.object(migration.frappe, "db", database):
            migration.execute()
            migration.execute()
        database.set_value.assert_called_once()
        values = database.set_value.call_args.args[2]
        self.assertEqual(old.credit_pending_usd, 20)
        self.assertNotIn("credit_assigned_to", values)
        self.assertNotIn("credit_commitment_date", values)
        self.assertNotIn("amount_usd", values)
