import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_employer import cn_employer as api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


class EmployerAliasSaveTests(unittest.TestCase):
    def test_alias_only_save_does_not_query_imports_or_reconcile(self):
        previous = frappe._dict(rounding_tolerance_usd=0.01, employer_code="A", aliases=[])
        document = frappe._dict(name="A", rounding_tolerance_usd=0.01, employer_code="A",
            aliases=[frappe._dict(alias_name="Nombre nuevo")], get_doc_before_save=lambda: previous)
        with patch.object(api.frappe, "db", Mock()) as db, patch.object(accounting, "_reconcile_sources") as reconcile:
            api.CNEmployer.on_update(document)
        db.exists.assert_not_called()
        reconcile.assert_not_called()

    def test_tolerance_change_still_refreshes_existing_accounting(self):
        document = frappe._dict(name="A", rounding_tolerance_usd=0.02, employer_code="A",
            get_doc_before_save=lambda: frappe._dict(rounding_tolerance_usd=0.01, employer_code="A"))
        with patch.object(api.frappe, "db", Mock(exists=Mock(return_value=True))), patch.object(accounting, "_reconcile_sources") as reconcile:
            api.CNEmployer.on_update(document)
        reconcile.assert_called_once_with("A")

    def document(self, aliases=()):
        document = Mock(employer_name="Empresa A",
            aliases=[frappe._dict(alias_name=value) for value in aliases])
        document.name = "A"
        return document

    def call(self, document, alias, companies=()):
        with patch.object(api.frappe, "get_doc", return_value=document), \
             patch.object(api.frappe, "get_all", return_value=list(companies)), \
             patch("credinomina_reconciliation.employer_naming.attach_employer_aliases"), \
             patch.object(api.frappe, "throw", side_effect=frappe.ValidationError):
            return api.add_employer_alias("A", alias)

    def test_save_uses_parent_permissions_and_normal_validation(self):
        document = self.document()
        result = self.call(document, "  Convenio   Ágil  ")
        document.check_permission.assert_called_once_with("write")
        document.append.assert_called_once_with("aliases", {"alias_name": "Convenio Ágil"})
        document.save.assert_called_once_with()
        self.assertTrue(result["added"])

    def test_existing_normalized_alias_is_idempotent(self):
        document = self.document(["CONVENIO AGIL"])
        self.assertFalse(self.call(document, "Convenio Ágil")["added"])
        document.save.assert_not_called()
        document.append.assert_not_called()

    def test_blank_long_and_unidentified_are_rejected(self):
        for alias in ("  ", "x" * 141):
            document = self.document()
            with self.assertRaises(frappe.ValidationError):
                self.call(document, alias)
            document.save.assert_not_called()
        document = self.document()
        document.name = "NO IDENTIFICADA"
        with self.assertRaises(frappe.ValidationError):
            self.call(document, "Alias")
        document.save.assert_not_called()

    def test_conflicting_alias_name_or_code_is_rejected(self):
        for field in ("name", "employer_name", "employer_code", "aliases"):
            company = frappe._dict(name="B", employer_name="Empresa B", employer_code="B", aliases=[])
            company[field] = ["OTRA"] if field == "aliases" else "OTRA"
            document = self.document()
            with self.assertRaises(frappe.ValidationError):
                self.call(document, "otra", [company])
            document.save.assert_not_called()

    def test_permission_failure_precedes_alias_lookup(self):
        document = self.document()
        document.check_permission.side_effect = frappe.PermissionError
        with patch.object(api.frappe, "get_doc", return_value=document), patch.object(api.frappe, "get_all") as lookup:
            with self.assertRaises(frappe.PermissionError):
                api.add_employer_alias("A", "Alias")
        lookup.assert_not_called()
        document.save.assert_not_called()
