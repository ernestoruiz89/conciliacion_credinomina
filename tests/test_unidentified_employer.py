import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.employer_naming import ensure_unidentified_employer


class UnidentifiedEmployerTests(unittest.TestCase):
    def test_existing_company_is_reused_without_changes(self):
        document = Mock()
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value="NO IDENTIFICADA"))), \
             patch.object(frappe, "get_doc", return_value=document):
            self.assertEqual(ensure_unidentified_employer(), "NO IDENTIFICADA")
        document.check_permission.assert_called_once_with("read")
        document.insert.assert_not_called()
        document.save.assert_not_called()

    def test_only_fixed_company_is_created_in_authorized_workflow(self):
        document = Mock()
        document.name = "NO IDENTIFICADA"
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value=None))), \
             patch.object(frappe, "get_doc", return_value=document) as get_doc:
            self.assertEqual(ensure_unidentified_employer(), "NO IDENTIFICADA")
        values = get_doc.call_args.args[0]
        self.assertEqual(values["employer_name"], "NO IDENTIFICADA")
        self.assertEqual(values["employer_code"], "NO IDENTIFICADA")
        self.assertEqual(values["rounding_tolerance_usd"], 0)
        document.insert.assert_called_once_with(ignore_permissions=True)

    def test_permission_required_before_any_database_access(self):
        with patch.object(frappe, "has_permission", return_value=False), \
             patch.object(frappe, "throw", side_effect=PermissionError), \
             patch.object(frappe, "db", Mock()) as database:
            with self.assertRaises(PermissionError):
                ensure_unidentified_employer()
        database.get_value.assert_not_called()
