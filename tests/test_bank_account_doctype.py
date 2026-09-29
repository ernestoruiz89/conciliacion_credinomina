"""The bank-account master is available for linking remittances."""

import json
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "credinomina_reconciliation" / "conciliacion_credinomina"


class BankAccountDocTypeTest(unittest.TestCase):
    def test_bank_account_has_minimal_spanish_master_fields_and_roles(self):
        metadata = json.loads((
            APP / "doctype" / "cn_bank_account" / "cn_bank_account.json"
        ).read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in metadata["fields"]}
        self.assertEqual("CN Bank Account", metadata["name"])
        self.assertEqual("Conciliacion Credinomina", metadata["module"])
        self.assertEqual("field:account_name", metadata["autoname"])
        self.assertEqual("By fieldname", metadata["naming_rule"])
        self.assertTrue({
            "account_name", "bank_name", "account_number", "currency", "active",
        } <= fields.keys())
        self.assertNotIn("naming_series", fields)
        self.assertEqual("USD\nNIO", fields["currency"]["options"])
        self.assertEqual(
            {"System Manager", "Supervisor Credinomina", "Operador Credinomina"},
            {permission["role"] for permission in metadata["permissions"]},
        )

    def test_remittance_bank_account_link_is_optional_and_uses_master(self):
        metadata = json.loads((
            APP / "doctype" / "cn_remittance_allocation"
            / "cn_remittance_allocation.json"
        ).read_text(encoding="utf-8"))
        field = next(field for field in metadata["fields"] if field["fieldname"] == "bank_account")
        self.assertEqual("Link", field["fieldtype"])
        self.assertEqual("CN Bank Account", field["options"])
        self.assertFalse(field.get("reqd"))

    def test_migration_renames_existing_accounts_and_preserves_links(self):
        patch_path = (
            ROOT / "credinomina_reconciliation" / "patches" / "v1_0"
            / "rename_cn_bank_accounts_by_name.py"
        )
        accounts = {
            "CN-CTA-00001": {
                "account_name": "Cuenta USD", "bank_name": "Banco A",
                "account_number": "001",
            },
            "CN-CTA-00002": {
                "account_name": "Cuenta NIO", "bank_name": "Banco B",
                "account_number": "002",
            },
        }
        remittance = {"bank_account": "CN-CTA-00001"}

        class Row(dict):
            __getattr__ = dict.__getitem__

        def rename_doc(_doctype, old, new, **_kwargs):
            account = accounts.pop(old)
            account["account_name"] = new
            accounts[new] = account
            if remittance["bank_account"] == old:
                remittance["bank_account"] = new

        db = types.SimpleNamespace(
            table_exists=lambda _doctype: True,
            get_value=lambda _doctype, name, field: accounts[name].get(field),
        )
        frappe_stub = types.ModuleType("frappe")
        frappe_stub.db = db
        frappe_stub.get_meta = lambda _doctype: types.SimpleNamespace(autoname="field:account_name")
        frappe_stub.get_all = lambda *_args, **_kwargs: [
            Row(name=name, account_name=values["account_name"])
            for name, values in accounts.items()
        ]
        frappe_stub.rename_doc = rename_doc
        frappe_stub.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe_stub._ = lambda message: message
        model = types.ModuleType("frappe.model")
        naming = types.ModuleType("frappe.model.naming")
        naming.validate_name = lambda _doctype, name: name
        with patch.dict(sys.modules, {
            "frappe": frappe_stub,
            "frappe.model": model,
            "frappe.model.naming": naming,
        }):
            spec = importlib.util.spec_from_file_location("_test_bank_account_rename", patch_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.execute()

        self.assertEqual({"Cuenta USD", "Cuenta NIO"}, set(accounts))
        self.assertEqual("Cuenta USD", remittance["bank_account"])
        self.assertEqual("001", accounts["Cuenta USD"]["account_number"])


if __name__ == "__main__":
    unittest.main()
