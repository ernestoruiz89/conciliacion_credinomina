import unittest
from datetime import date
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.application_deadlines import freeze_deadlines, deadline_values, RECORDED, CORRECTED, MIGRATED
from credinomina_reconciliation.application_aging import application_balances


class ApplicationDeadlineTests(unittest.TestCase):
    def source(self, **values):
        return frappe._dict(name="ROW", event_type="Aplicacion", event_date="2025-04-30", **values)

    def document(self, rows, previous=None, employer="E"):
        return frappe._dict(employer=employer, rows=rows, get_doc_before_save=lambda: previous)

    def test_capture_restore_and_reimport_preserve_original_term(self):
        row = self.source(payment_due_date="2099-01-01")
        doc = self.document([row])
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=10))):
            freeze_deadlines(doc)
        self.assertEqual(row.payment_due_date, date(2025, 5, 10))
        self.assertEqual(row.payment_term_origin, RECORDED)
        # Reimport retains the stable row name but provides no deadline fields.
        reimported = self.source()
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=20))) as db:
            freeze_deadlines(self.document([reimported], doc))
            db.get_value.assert_not_called()
        self.assertEqual(reimported.payment_due_date, date(2025, 5, 10))

    def test_identity_correction_recalculates_and_records_basis(self):
        old = self.document([self.source(payment_due_date="2025-05-10")])
        changed = self.source()
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=15))):
            freeze_deadlines(self.document([changed], old, "OTHER"))
        self.assertEqual(changed.payment_due_date, date(2025, 5, 15))
        self.assertEqual(changed.payment_term_origin, CORRECTED)

    def test_migration_does_not_claim_original_contract_or_assign_unknown_company(self):
        old = self.document([self.source()])
        current = self.source()
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=10))):
            freeze_deadlines(self.document([current], old))
        self.assertEqual(current.payment_term_origin, MIGRATED)
        self.assertIsNone(deadline_values(self.source(), "NO IDENTIFICADA", 10)["payment_due_date"])

    def test_aging_uses_frozen_term_even_if_company_changes(self):
        source = dict(self.source(payment_due_date="2025-05-10", payment_term_origin=MIGRATED),
            parent="I", effective=1, amount=100, currency="USD")
        row, = application_balances([source], {"I": {"employer": "E"}}, {}, {}, {"E": {"grace_days": 20}}, "2025-05-11")
        self.assertEqual(row["age_days"], 1)
        self.assertEqual(row["payment_term_origin"], MIGRATED)
