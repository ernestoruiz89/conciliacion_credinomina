"""Static checks for the standard Frappe Workspace shipped with the app."""

import csv
import json
import unittest
from copy import deepcopy
from pathlib import Path

from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import build_layout


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "credinomina_reconciliation" / "conciliacion_credinomina"
WORKSPACE = APP / "workspace" / "conciliacion_credinomina" / "conciliacion_credinomina.json"


class WorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
        cls.blocks = json.loads(cls.workspace["content"])

    def test_workspace_is_public_but_limited_to_app_roles(self):
        self.assertEqual("Workspace", self.workspace["doctype"])
        self.assertEqual("Conciliacion Credinomina", self.workspace["name"])
        self.assertEqual(self.workspace["name"], self.workspace["title"])
        self.assertEqual(self.workspace["name"], self.workspace["label"])
        self.assertEqual(1, self.workspace["public"])
        self.assertEqual(0, self.workspace["is_hidden"])
        self.assertEqual(
            {"System Manager", "Supervisor Credinomina", "Operador Credinomina"},
            {item["role"] for item in self.workspace["roles"]},
        )

    def test_workspace_has_spanish_display_translation(self):
        translations = dict(csv.reader(
            (ROOT / "credinomina_reconciliation" / "translations" / "es.csv")
            .read_text(encoding="utf-8").splitlines()
        ))
        self.assertEqual("Conciliación Credinómina", translations[self.workspace["title"]])

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

    def test_client_catalog_is_in_preparation_card(self):
        links = self.workspace["links"]
        start = next(index for index, item in enumerate(links)
                     if item["type"] == "Card Break" and item["label"] == "1. Preparación")
        end = next((index for index in range(start + 1, len(links))
                    if links[index]["type"] == "Card Break"), len(links))
        self.assertIn("CN Client", {item.get("link_to") for item in links[start + 1:end]})

    def test_bank_accounts_are_in_preparation_card(self):
        links = self.workspace["links"]
        start = next(index for index, item in enumerate(links)
                     if item["type"] == "Card Break" and item["label"] == "1. Preparación")
        end = next((index for index in range(start + 1, len(links))
                    if links[index]["type"] == "Card Break"), len(links))
        self.assertIn("CN Bank Account", {item.get("link_to") for item in links[start + 1:end]})

    def test_workflow_order_and_primary_actions(self):
        self.assertEqual([
            "1. Preparación", "2. Cobranza y deducciones", "3. Aplicaciones del core",
            "4. Depósitos y distribución", "5. Diferencias y seguimiento", "6. Control y reportes",
        ], [block["data"]["card_name"] for block in self.blocks if block["type"] == "card"])
        self.assertEqual([
            "CN Credit Portfolio Snapshot", "CN Reconciliation Period",
            "CN Source Import", "CN Remittance Allocation",
        ], [row["link_to"] for row in self.workspace["shortcuts"]])
        self.assertTrue(all(row["doc_view"] == "New" for row in self.workspace["shortcuts"]))
        self.assertNotIn("Importar fuentes", self.workspace["content"])

    def test_migration_keeps_custom_navigation_and_is_idempotent(self):
        current = deepcopy(self.workspace)
        current["links"].insert(1, {
            "type": "Link", "label": "Ayuda local", "link_to": "Custom Help", "link_type": "DocType",
        })
        current["links"] += [
            {"type": "Card Break", "label": "Mi equipo", "link_count": 1},
            {"type": "Link", "label": "Otro reporte", "link_to": "Custom Report", "link_type": "Report"},
        ]
        current["shortcuts"].append({"label": "Ayuda local", "link_to": "Custom Help", "type": "DocType"})
        content = json.loads(current["content"]) + [
            {"id": "custom_card", "type": "card", "data": {"card_name": "Mi equipo", "col": 4}},
            {"id": "custom_shortcut", "type": "shortcut", "data": {"shortcut_name": "Ayuda local", "col": 3}},
            {"id": "custom_text", "type": "paragraph", "data": {"text": "Ayuda del sitio", "col": 12}},
        ]
        current["content"] = json.dumps(content)
        result = build_layout(current, self.workspace)
        self.assertEqual(result, build_layout(result, self.workspace))
        self.assertTrue({"Custom Help", "Custom Report"} <= {row.get("link_to") for row in result["links"]})
        self.assertIn("Ayuda del sitio", result["content"])
        self.assertEqual({"links", "shortcuts", "content"}, set(result))

    def test_migration_replaces_old_navigation_without_removing_destinations(self):
        current = deepcopy(self.workspace)
        current["links"][0]["label"] = "Preparación y cobranza"
        current["content"] = json.dumps([{
            "id": "cn_preparacion", "type": "card",
            "data": {"card_name": "Preparación y cobranza", "col": 4},
        }])
        result = build_layout(current, self.workspace)
        self.assertEqual(self.blocks, json.loads(result["content"]))
        self.assertEqual(self.workspace["links"], result["links"])


if __name__ == "__main__":
    unittest.main()
