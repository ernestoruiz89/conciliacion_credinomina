"""Safe bank-account merging without requiring a running Frappe site."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class BankAccountMergeTest(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.throw = lambda text: (_ for _ in ()).throw(ValueError(text))
        frappe.get_doc = Mock()
        frappe.db = Mock()
        document = types.ModuleType("frappe.model.document")
        document.Document = object
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_bank_account/cn_bank_account.py"
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model.document": document}):
            spec = importlib.util.spec_from_file_location("_bank_merge_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.source = module.CNBankAccount()
        self.source.doctype = "CN Bank Account"
        self.source.bank_name = " BAC "
        self.source.currency = "NIO"
        self.source.account_number = "001-234 567"
        self.source.check_permission = Mock()
        self.target = types.SimpleNamespace(bank_name="bac", currency="NIO", account_number="001234567", check_permission=Mock())
        frappe.get_doc.return_value = self.target
        self.frappe = frappe

    def test_merge_normalizes_format_and_checks_permissions(self):
        self.assertEqual("Destino", self.source.before_rename("Origen", " Destino ", True))
        self.frappe.get_doc.assert_called_once_with("CN Bank Account", "Destino")
        self.assertEqual([("write",), ("delete",)], [call.args for call in self.source.check_permission.call_args_list])
        self.target.check_permission.assert_called_once_with("write")

    def test_different_or_missing_identity_rejected(self):
        for field, values in {
            "bank_name": ["BANPRO", ""],
            "currency": ["USD", ""],
            "account_number": ["234567", "1234567", "001234568", ""],
        }.items():
            original = getattr(self.target, field)
            for value in values:
                with self.subTest(field=field, value=value):
                    setattr(self.target, field, value)
                    with self.assertRaises(ValueError):
                        self.source.before_rename("Origen", "Destino", True)
            setattr(self.target, field, original)

    def test_permission_failure_stops_merge(self):
        self.source.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.source.before_rename("Origen", "Destino", True)
        self.frappe.get_doc.assert_not_called()

    def test_destination_permission_required(self):
        self.target.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.source.before_rename("Origen", "Destino", True)

    def test_merge_does_not_overwrite_destination(self):
        self.source.after_rename("Origen", "Destino", True)
        self.frappe.db.set_value.assert_not_called()

    def test_normal_rename_unchanged(self):
        self.assertEqual("Nuevo", self.source.before_rename("Origen", " Nuevo "))
        self.frappe.get_doc.assert_not_called()
        self.source.after_rename("Origen", "Nuevo")
        self.frappe.db.set_value.assert_called_once_with("CN Bank Account", "Nuevo", "account_name", "Nuevo", update_modified=False)


if __name__ == "__main__":
    unittest.main()
