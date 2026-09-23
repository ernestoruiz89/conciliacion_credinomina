"""Exercise the Frappe controller's deposit matcher without a Frappe site."""

import ast
import unittest
from collections import defaultdict
from pathlib import Path

from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING, SOURCE_DEPOSITS, clean_text


SOURCE_FILE = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation"
    / "conciliacion_credinomina"
    / "doctype"
    / "cn_source_import"
    / "cn_source_import.py"
)


class DepositRow(dict):
    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value

    def as_dict(self):
        return dict(self)


def load_matcher():
    tree = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))
    definition = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_match_deposits"
    )
    module = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))
    namespace = {
        "defaultdict": defaultdict,
        "clean_text": clean_text,
        "SOURCE_ACCOUNTING": SOURCE_ACCOUNTING,
        "_": lambda message: message,
        "narrow_deposit_candidates_by_date": lambda _source, candidates: candidates,
        "deposit_pair_result": lambda _left, _right: (True, "Depósitos coincidentes"),
    }
    exec(compile(module, str(SOURCE_FILE), "exec"), namespace)
    return namespace["_match_deposits"]


class DepositMatcherTests(unittest.TestCase):
    def test_missing_reference_uses_translation_function(self):
        row = DepositRow(
            event_type="Deposito", effective=1,
            _source_type=SOURCE_ACCOUNTING, reference="",
        )
        self.assertEqual(load_matcher()([row]), [])
        self.assertIn("referencia bancaria", row.match_reason)

    def test_matched_pair_uses_translation_function(self):
        accounting = DepositRow(
            event_type="Deposito", effective=1,
            _source_type=SOURCE_ACCOUNTING, reference="R1",
        )
        bank = DepositRow(
            event_type="Deposito", effective=1,
            _source_type=SOURCE_DEPOSITS, reference="R1",
        )
        self.assertEqual(load_matcher()([accounting, bank]), [(accounting, bank)])
        self.assertEqual(accounting.match_reason, "Depósitos coincidentes")
        self.assertEqual(bank.match_status, "Conciliado")


if __name__ == "__main__":
    unittest.main()
