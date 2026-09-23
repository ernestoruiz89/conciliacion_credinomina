"""Static checks for the standard Frappe Workspace shipped with the app."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "credinomina_reconciliation" / "credinomina_reconciliation"
WORKSPACE = APP / "workspace" / "conciliacion_credinomina" / "conciliacion_credinomina.json"


class WorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
        cls.blocks = json.loads(cls.workspace["content"])

    def test_workspace_is_public_but_limited_to_app_roles(self):
        self.assertEqual("Workspace", self.workspace["doctype"])
        self.assertEqual("Conciliacion Credinomina", self.workspace["name"])
        self.assertEqual(1, self.workspace["public"])
        self.assertEqual(0, self.workspace["is_hidden"])
        self.assertEqual(
            {"System Manager", "Supervisor Credinomina", "Operador Credinomina"},
            {item["role"] for item in self.workspace["roles"]},
        )

    def test_blocks_refer_to_existing_cards_and_shortcuts(self):
        ids = [block["id"] for block in self.blocks]
        self.assertEqual(len(ids), len(set(ids)))
        cards = {
            item["label"] for item in self.workspace["links"]
            if item["type"] == "Card Break"
        }
        shortcuts = {item["label"] for item in self.workspace["shortcuts"]}
        self.assertEqual(
            cards,
            {block["data"]["card_name"] for block in self.blocks if block["type"] == "card"},
        )
        self.assertEqual(
            shortcuts,
            {block["data"]["shortcut_name"] for block in self.blocks if block["type"] == "shortcut"},
        )

    def test_each_card_count_and_destination_is_valid(self):
        definitions = {}
        for kind, folder in (("DocType", "doctype"), ("Report", "report"), ("Page", "page")):
            definitions[kind] = {
                json.loads(path.read_text(encoding="utf-8"))["name"]
                for path in (APP / folder).rglob("*.json")
            }
        current_card = None
        actual_counts = {}
        for item in self.workspace["links"]:
            if item["type"] == "Card Break":
                current_card = item["label"]
                actual_counts[current_card] = 0
                continue
            self.assertIsNotNone(current_card)
            actual_counts[current_card] += 1
            self.assertIn(item["link_to"], definitions[item["link_type"]])
        for item in self.workspace["links"]:
            if item["type"] == "Card Break":
                self.assertEqual(item["link_count"], actual_counts[item["label"]])
        for item in self.workspace["shortcuts"]:
            self.assertIn(item["link_to"], definitions[item["type"]])


if __name__ == "__main__":
    unittest.main()
