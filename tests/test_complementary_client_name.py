import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item import cn_complementary_item as module


class ComplementaryClientNameTests(unittest.TestCase):
    def test_name_is_visible_for_all_concepts_and_can_be_filled_after_submit(self):
        schema = json.loads(Path(module.__file__).with_suffix(".json").read_text(encoding="utf-8"))
        field = next(field for field in schema["fields"] if field["fieldname"] == "client_name")
        self.assertFalse(field.get("depends_on"))
        self.assertFalse(field.get("hidden"))
        self.assertEqual((field["read_only"], field["allow_on_submit"]), (1, 1))
        order = schema["field_order"]
        self.assertEqual(order[order.index("client_name") + 1], "client_number")

    def test_opening_existing_import_shows_source_name_without_saving(self):
        doc = frappe._dict(category="Otros ingresos", docstatus=1, amount_usd=0.29,
                           source_client_name="Ana Pérez", source_debit=10.62)
        before = dict(doc)
        with patch.object(module.frappe, "db", Mock()) as db:
            module.CNComplementaryItem.onload(doc)
        self.assertEqual(doc.client_name, "Ana Pérez")
        self.assertEqual({key: doc[key] for key in before}, before)
        db.get_value.assert_not_called()
        db.set_value.assert_not_called()

    def test_exact_client_lookup_is_scoped_and_preferred_over_source_name(self):
        doc = frappe._dict(category="Otros ingresos", client_number="123", credit_client="123",
                           employer="EMP", source_client_name="Nombre abreviado")
        with patch.object(module.frappe, "db", Mock()) as db:
            db.get_value.return_value = "Ana Pérez"
            module._complete_client_name(doc)
        db.get_value.assert_called_once_with("CN Client", {"name": "123", "client_number": "123", "employer": "EMP"}, "client_name")
        self.assertEqual(doc.client_name, "Ana Pérez")

    def test_preserves_existing_names_and_does_not_invent_generic_beneficiaries(self):
        for values in ({"client_name": "Nombre confirmado", "client_number": "123"},
                       {"category": "Saldo a favor de la empresa", "source_client_name": "Ana"},
                       {"generic_distribution": 1, "source_client_name": "Ana"},
                       {"source_client_name": "0"}, {"source_client_name": "N/A"}, {}):
            with self.subTest(values=values), patch.object(module.frappe, "db", Mock()) as db:
                doc = frappe._dict(values)
                module._complete_client_name(doc)
                self.assertEqual(doc.client_name or "", values.get("client_name", ""))
                db.get_value.assert_not_called()

    def test_changing_identity_never_keeps_the_previous_customer_name(self):
        previous = frappe._dict(client_name="Ana", client_number="123", employer="EMP", source_client_name="Ana")
        for changes in ({"client_number": "456"}, {"client_number": ""}, {"employer": "OTHER"}):
            with self.subTest(changes=changes), patch.object(module.frappe, "db", Mock()) as db:
                db.get_value.return_value = None
                doc = frappe._dict(dict(previous, **changes))
                module._complete_client_name(doc, previous)
                self.assertEqual(doc.client_name, "")
                self.assertEqual(doc.source_client_name, "Ana")
