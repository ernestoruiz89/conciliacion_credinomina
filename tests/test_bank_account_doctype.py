"""The bank-account master is available for linking remittances."""

import json
import unittest
from pathlib import Path


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
        self.assertTrue({
            "account_name", "bank_name", "account_number", "currency", "active",
        } <= fields.keys())
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


if __name__ == "__main__":
    unittest.main()
