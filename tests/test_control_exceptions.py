import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import control_exceptions as api


class ControlExceptionTests(unittest.TestCase):
    def setUp(self):
        self.period = frappe._dict(name="P1", employer="EMP", status="Parcial", reconciliation_mode="Historica", check_permission=Mock())
        self.row = frappe._dict(name="R1", parent="IMP", source_row=2, historical_period="P1",
                               historical_balance_usd=30, effective=1, event_type="Aplicacion", match_status="Conciliado")
        self.values = {"period": "P1", "employer": "EMP", "related_case_type": "Aplicación", "related_case_id": "R1",
                       "client_name": "Ana", "client_number": "12", "loan_number": "100-1", "source_import": "IMP", "source_row": 2}
        self.created = Mock(name="document")
        self.created.name = "EXC1"
        self.created.insert.return_value = self.created
        self.db = Mock(get_value=Mock(return_value=self.row))
        self.documents = []
        self.exceptions = []
        self.permissions = set()
        for ctx in (
            patch.object(api.frappe, "db", self.db),
            patch.object(api.frappe, "get_doc", side_effect=self.get_doc),
            patch.object(api.frappe, "get_all", side_effect=lambda *a, **k: self.exceptions),
            patch.object(api.frappe, "get_list", side_effect=lambda *a, **k: self.exceptions),
            patch.object(api.frappe, "has_permission", side_effect=lambda dt, perm, **kw: perm not in self.permissions),
            patch.object(api.frappe, "throw", side_effect=ValueError),
            patch.object(api, "resolve_related_case", return_value=self.values),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)

    def get_doc(self, value, *args):
        if isinstance(value, dict):
            self.documents.append(value)
            return self.created
        return self.period

    def create(self, **kwargs):
        return api.create_application_exception("P1", "R1", kwargs.get("pending", 30), kwargs.get("description", "Pago parcial"))

    def test_creation_uses_pending_and_server_resolved_identity(self):
        self.assertEqual(self.create(), {"name": "EXC1", "created": True})
        self.assertEqual(self.documents[0]["amount_usd"], 30)
        self.assertEqual(self.documents[0]["client_number"], "12")
        self.assertEqual(self.documents[0]["related_case_id"], "R1")
        self.created.insert.assert_called_once_with()
        self.period.check_permission.assert_called_once_with("read")

    def test_cannot_register_without_create_permission(self):
        self.permissions.add("create")
        with self.assertRaises(ValueError):
            self.create()
        self.db.sql.assert_not_called()

    def test_closed_period_rejected(self):
        self.period.status = "Cerrado"
        with self.assertRaises(ValueError):
            self.create()
        self.created.insert.assert_not_called()

    def test_fully_paid_or_changed_pending_rejected(self):
        for amount in (0, -1, 20):
            self.row.historical_balance_usd = amount
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.create()
        self.created.insert.assert_not_called()

    def test_wrong_period_or_inactive_application_rejected(self):
        for field, value in (("historical_period", "P2"), ("effective", 0), ("match_status", "Duplicado")):
            old = self.row[field]
            self.row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.create()
            self.row[field] = old

    def test_description_required(self):
        with self.assertRaises(ValueError):
            self.create(description="   ")

    def test_duplicate_returns_existing_without_overwriting(self):
        self.exceptions = [frappe._dict(name="OLD", period="P1", related_case_type="Aplicación", related_case_id="R1", status="Resuelta")]
        self.assertEqual(self.create(), {"name": "OLD", "created": False})
        self.created.insert.assert_not_called()

    def test_duplicate_without_read_access_does_not_leak_name(self):
        self.exceptions = [frappe._dict(name="SECRET", period="P1", related_case_type="Aplicación", related_case_id="R1")]
        self.permissions.add("read")
        with self.assertRaises(ValueError) as error:
            self.create()
        self.assertNotIn("SECRET", str(error.exception))

    def test_annotations_prefer_active_exception_and_support_legacy_source_link(self):
        self.exceptions = [frappe._dict(name="CLOSED", period="P1", status="Resuelta", related_case_id="R1", related_case_type="Aplicación"),
                           frappe._dict(name="OPEN", period="P1", status="Abierta", source_import="IMP", source_row=2)]
        api.annotate_application_exceptions([self.row])
        self.assertEqual(self.row.exception_name, "OPEN")
        self.assertEqual(self.row.exception_status, "Abierta")

    def test_annotations_do_not_cross_periods_or_collection_cases(self):
        self.exceptions = [frappe._dict(name="WRONG", period="P2", status="Abierta", source_import="IMP", source_row=2),
                           frappe._dict(name="COLLECTION", period="P1", related_case_id="R1", related_case_type="Cobranza")]
        api.annotate_application_exceptions([self.row])
        self.assertFalse(self.row.exception_name)

    def test_annotations_respect_read_permission(self):
        self.exceptions = [frappe._dict(name="SECRET", period="P1", related_case_type="Aplicación", related_case_id="R1")]
        self.permissions.add("read")
        api.annotate_application_exceptions([self.row])
        self.assertFalse(self.row.exception_name)
