import json
import unittest
from pathlib import Path

from credinomina_reconciliation.deposit_scoping import (
    deposit_scope,
    duplicate_deposit_key,
    resolved_deposit_employer,
)


class DepositScopingTest(unittest.TestCase):
    def test_exact_carryover_is_duplicate_but_another_deposit_is_not(self):
        original = {
            "event_date": "2025-04-15", "reference": "R-1", "voucher": "BAC 6906 C$",
            "currency": "NIO", "amount": 1000, "description": "Transferencia",
            "client_name": "CONVENIO ACME", "loan_number": "",
        }
        self.assertEqual(duplicate_deposit_key(original), duplicate_deposit_key(dict(original)))
        self.assertNotEqual(
            duplicate_deposit_key(original),
            duplicate_deposit_key({**original, "amount": 1001}),
        )
        self.assertNotEqual(
            duplicate_deposit_key(original),
            duplicate_deposit_key({**original, "event_date": "2025-04-16"}),
        )
        self.assertIsNone(duplicate_deposit_key({**original, "reference": ""}))

    def test_convenio_and_known_employer_are_in_scope(self):
        aliases = {"acme": "ACME"}
        self.assertEqual(
            "Conciliar", deposit_scope({"client_name": "CONVENIO ACME"}, None, aliases)
        )
        self.assertEqual(
            "Conciliar", deposit_scope({"client_name": "ACME"}, None, aliases)
        )
        self.assertEqual(
            "Conciliar", deposit_scope(
                {"client_name": "Cliente personal"},
                {"employer_text": "ACME"}, aliases,
            )
        )

    def test_personal_is_excluded_unknown_is_reviewed_and_override_wins(self):
        aliases = {"acme": "ACME"}
        self.assertEqual(
            "Excluir", deposit_scope({"client_name": "Cliente personal"}, None, aliases)
        )
        self.assertEqual(
            "Revisar", deposit_scope({"client_name": "NO IDENTIFICADO"}, None, aliases)
        )
        self.assertEqual(
            "Revisar", deposit_scope({"client_name": "Etiqueta ambigua"}, None,
                                      aliases, {"etiqueta ambigua"})
        )
        self.assertEqual(
            "Conciliar", deposit_scope(
                {"client_name": "Cliente personal", "deposit_scope": "Conciliar"},
                None, aliases,
            )
        )
        self.assertEqual(
            "Excluir", deposit_scope(
                {"client_name": "CONVENIO ACME", "deposit_scope": "Excluir"},
                None, aliases,
            )
        )

    def test_unknown_or_conflicting_employer_cannot_auto_allocate(self):
        aliases = {"acme": "ACME", "beta": "BETA"}
        self.assertEqual(
            ("ACME", False),
            resolved_deposit_employer(
                {"employer_text": ""}, {"employer_text": "ACME"}, aliases, set()
            ),
        )
        self.assertEqual(
            (None, True),
            resolved_deposit_employer(
                {"employer_text": ""}, {"employer_text": "DESCONOCIDA"},
                aliases, set(),
            ),
        )
        self.assertEqual(
            (None, True),
            resolved_deposit_employer(
                {"employer_text": "ACME"}, {"employer_text": "BETA"},
                aliases, set(),
            ),
        )

    def test_source_row_exposes_manual_bank_scope(self):
        root = Path(__file__).resolve().parents[1]
        path = (
            root / "credinomina_reconciliation" / "conciliacion_credinomina"
            / "doctype" / "cn_source_row" / "cn_source_row.json"
        )
        fields = {
            field["fieldname"]: field
            for field in json.loads(path.read_text(encoding="utf-8"))["fields"]
        }
        self.assertIn("Conciliar", fields["deposit_scope"]["options"])
        self.assertIn("Excluir", fields["deposit_scope"]["options"])


if __name__ == "__main__":
    unittest.main()
