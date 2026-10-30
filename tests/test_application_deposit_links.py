import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import application_deposit_links as links
from credinomina_reconciliation import accounting_related_records as related


def application(name="APP", **values):
    return frappe._dict(dict(name=name, historical_period="PER", match_status="Conciliado",
                             amount_usd=100, net_applied_usd=100) | values)


def deposit(name, entries, **values):
    return frappe._dict(dict(name=name, allocation_detail=json.dumps(entries),
        deposit_date="2026-02-01", deposit_reference="SAME-REFERENCE") | values)


class ApplicationDepositLinksTests(unittest.TestCase):
    def test_multiple_partial_deposits_and_repeated_entries_give_unique_exact_links(self):
        sources = [application(), application("OTHER")]
        deposits = [deposit("D2", [{"aplicacion_id": "APP", "importe_usd": 30}], deposit_date="2026-03-01"),
                    deposit("D1", [{"aplicacion_id": "APP", "importe_usd": 10},
                                   {"aplicacion_id": "APP", "importe_usd": 20}]),
                    deposit("WRONG", [{"aplicacion_id": "NOT-APP", "importe_usd": 40}]),
                    deposit("OTHER-D", [{"aplicacion_id": "OTHER", "importe_usd": 1}])]
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(links, "records", return_value=deposits) as read:
            result = links.load_links(sources)
        self.assertEqual([row["document"] for row in result["APP"]], ["D1", "D2"])
        self.assertEqual([row["document"] for row in result["OTHER"]], ["OTHER-D"])
        self.assertTrue(all(not row["via_collection"] for row in result["APP"]))
        self.assertEqual(read.call_args.kwargs["filters"], {"docstatus": 1})
        self.assertNotIn("employer", read.call_args.kwargs["filters"], "Allow actual authorized cross-company assignments")

    def test_no_permission_or_no_applications_does_not_read_deposits(self):
        with patch.object(frappe, "has_permission", return_value=False), patch.object(links, "records") as read:
            self.assertEqual(links.load_links([application()]), {})
            self.assertEqual(links.load_links([]), {})
            read.assert_not_called()

    def test_only_real_positive_cash_and_fifo_application_slices_are_linked(self):
        deposits = [deposit("FIFO", [{"periodo": "PER", "fila_id": "KEY", "importe_usd": 50,
            "aplicaciones_fifo": [{"application_id": "APP", "amount_usd": 30},
                                  {"application_id": "OTHER", "amount_usd": 20}]}]),
            deposit("ZERO", [{"aplicacion_id": "APP", "importe_usd": 0}]),
            deposit("NEGATIVE", [{"aplicacion_id": "APP", "importe_usd": -1}]),
            deposit("DUPLICATE-ORIGIN", [{"aplicacion_id": "APP", "importe_usd": 3}], cash_identity_status="Origen vinculado"),
            deposit("CREDIT-ITEM", [{"partida": "APP", "importe_usd": 3}]),
            deposit("INSTRUCTION-ONLY", [])]
        with patch.object(frappe, "has_permission", return_value=True), patch.object(links, "records", return_value=deposits):
            result = links.load_links([application()])
        self.assertEqual([row["document"] for row in result["APP"]], ["FIFO"])

    def test_legacy_collection_links_require_readable_period_and_exact_row_key(self):
        sources = [application(historical_period="", collection_row_id="COL"),
                   application("SECOND", historical_period="", application_allocation_detail='[{"collection_row_id":"COL","amount_usd":15}]')]
        collection = frappe._dict(name="COL", parent="PER", row_key="KEY")
        deposits = [deposit("LEGACY", [{"periodo": "PER", "fila_id": "KEY", "importe_usd": 20}]),
                    deposit("WRONG-PERIOD", [{"periodo": "ELSE", "fila_id": "KEY", "importe_usd": 20}])]
        def read(doctype, **kwargs):
            return [frappe._dict(name="PER")] if doctype == "CN Reconciliation Period" else deposits
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "get_all", return_value=[collection]), patch.object(links, "records", side_effect=read):
            result = links.load_links(sources)
        for name in ("APP", "SECOND"):
            self.assertEqual([row["document"] for row in result[name]], ["LEGACY"])
            self.assertTrue(result[name][0]["via_collection"], "Do not imply an attributable amount for shared cash")
            self.assertNotIn("amount_usd", result[name][0])
        with patch.object(frappe, "has_permission", side_effect=lambda dt, perm: dt != "CN Reconciliation Period"), \
             patch.object(links, "records", return_value=deposits):
            self.assertEqual(links.load_links(sources), {})

    def test_related_modal_preserves_pending_and_only_enriches_visible_pending_applications(self):
        candidate = application(parent="IMPORT", event_date="2026-02-15", historical_balance_usd=.01,
            deposit_match_status="Depósito parcial", client_name="Persona", client_number="1")
        identity = dict(numbers={"1"}, clients=set(), national_ids=set(), names=set(),
                        number_values={"1"}, national_values=set(), name_values=set())
        assigned = [dict(document="D", date="2026-02-01", reference="REF", via_collection=False)]
        with patch.object(frappe, "get_all", return_value=[candidate]), \
             patch.object(related, "records", return_value=[frappe._dict(name="IMPORT")]), \
             patch.object(links, "load_links", return_value={"APP": assigned}) as load:
            result = related._application_rows("EMPRESA", identity)
        self.assertEqual(result[0]["deposits"], assigned)
        self.assertEqual(result[0]["pending_usd"], .01)
        load.assert_called_once_with([candidate])
        with patch.object(related, "_application_rows", return_value=result), \
             patch.object(related, "_deposit_rows", return_value=[]), patch.object(related, "_complementary_rows", return_value=[]):
            # Existing shared endpoint still carries the same links for modal callers.
            data = related._related_pending("EMPRESA", dict(identity, client_name="Persona", client_number="1"))
        self.assertEqual(data["applications"][0]["deposits"], assigned)


if __name__ == "__main__":
    unittest.main()
