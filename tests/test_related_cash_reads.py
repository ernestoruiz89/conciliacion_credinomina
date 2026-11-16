"""Find the same cash without a LIKE scan per application, at any import size."""
import copy
import json
import re
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.complementary_cancellation import _related_cash


class RelatedCashReadsTests(unittest.TestCase):
    def test_large_scope_reads_journals_in_pages_and_keeps_exact_evidence(self):
        def deposit(name, entries, **values):
            return frappe._dict(name=name, allocation_detail=json.dumps(entries), docstatus=1,
                                employer="OTHER-COMPANY", **values)

        journals = [deposit(f"D-{i:04}", [{"aplicacion_id": "OTHER"}]) for i in range(503)]
        # Stored cash without targets, across companies and periods, must remain
        # evidence. Neither notes nor a prefix match identifies an actual claim.
        journals[0] = deposit("D-0000", [{"aplicacion_id": "APP-1", "importe_usd": 40}])
        journals[1] = deposit("D-0001", [{"periodo": "P_100%", "importe_usd": 20}])
        journals[2] = deposit("D-0002", [{"partida": "ITEM", "importe_usd": -10}])
        journals[3] = deposit("D-0003", [{"periodo": "P_100%-OTHER"}])
        journals[4] = deposit("D-0004", [{"observacion": "APP-1", "aplicacion_id": "APP-1000"}])
        journals[5] = deposit("D-0005", [{"aplicacion_id": "APP-2"}])  # Selected deposit.
        journals[501] = deposit("D-0501", [{"aplicacion_id": "APP-499"}, {"periodo": "OTHER"}])
        journals[502] = deposit("D-0502", [{"aplicacion_id": "APP-0"}, {"partida": "ITEM"}])
        before = copy.deepcopy(journals)
        calls = []

        def query(doctype, *, filters, fields, limit_page_length, order_by=None):
            self.assertEqual(doctype, "CN Remittance Allocation")
            self.assertEqual(filters["docstatus"], 1)
            self.assertNotIn("employer", filters)
            calls.append((filters, fields))
            if fields == ["name", "allocation_detail"]:
                self.assertEqual(order_by, "name asc")
                self.assertEqual(limit_page_length, 500)
                return [row for row in journals if row.name > filters["name"][1]][:limit_page_length]
            return [row for row in journals if row.name in filters["name"][1]]

        rows = [frappe._dict(name=f"APP-{i}", event_type="Aplicacion") for i in range(500)]
        with patch.object(frappe, "get_all", side_effect=query), \
             patch.object(frappe, "get_doc") as get_doc, patch.object(frappe, "db", Mock()) as db:
            result = _related_cash([frappe._dict(name="D-0005")], [frappe._dict(name="P_100%")],
                                   rows, [frappe._dict(name="ITEM")])
        self.assertEqual([row.name for row in result], ["D-0000", "D-0001", "D-0002", "D-0501", "D-0502"])
        self.assertEqual(journals, before)
        self.assertEqual(len(calls), 3)  # Two journal pages, one metadata read.
        self.assertEqual(result[2].allocation_detail, before[2].allocation_detail)
        get_doc.assert_not_called()
        self.assertEqual(db.mock_calls, [])

    def test_empty_scope_does_not_query_or_match_empty_identifiers(self):
        with patch.object(frappe, "get_all") as query:
            self.assertEqual(_related_cash([], [frappe._dict(name=None)],
                [frappe._dict(name="DEPOSIT", event_type="Deposito")], [frappe._dict(name="")]), [])
        query.assert_not_called()

    def test_large_scope_without_matches_does_not_load_metadata(self):
        rows = [frappe._dict(name=f"APP-{i}", event_type="Aplicacion") for i in range(499)]
        journals = [frappe._dict(name="OTHER", allocation_detail='[{"periodo":"UNRELATED"}]'),
                    frappe._dict(name="EMPTY", allocation_detail="[]"),
                    frappe._dict(name="MALFORMED", allocation_detail="not json")]
        with patch.object(frappe, "get_all", return_value=journals) as query:
            self.assertEqual(_related_cash([], [], rows, []), [])
        query.assert_called_once()

    def test_small_scopes_keep_exact_links_and_parameterize_literal_identifiers(self):
        for count in (1, 5, 10, 20):
            with self.subTest(rows=count):
                names = ["A[1].*|%_'--"] + [f"APP-{i}" for i in range(count - 1)]
                journals = [frappe._dict(name="YES", allocation_detail=json.dumps([
                    {"aplicacion_id": names[0], "importe_usd": 40}])),
                    frappe._dict(name="NO", allocation_detail=json.dumps([{"aplicacion_id": names[0] + "-OTHER"}])),
                    frappe._dict(name="NOTE", allocation_detail=json.dumps([{"observacion": names[0]}])),
                    frappe._dict(name="SELECTED", allocation_detail=json.dumps([{"aplicacion_id": names[0]}]))]
                with patch.object(frappe.db, "sql", return_value=journals) as query, \
                     patch.object(frappe, "get_all") as get_all:
                    result = _related_cash([frappe._dict(name="SELECTED")], [],
                        [frappe._dict(name=name, event_type="Aplicacion") for name in names], [])
                self.assertEqual(result, journals[:1])
                get_all.assert_not_called()
                query.assert_called_once()
                sql, parameters = query.call_args.args
                self.assertIn("docstatus=1", sql)
                self.assertIn("allocation_detail REGEXP %s", sql)
                self.assertNotIn(names[0], sql)
                self.assertEqual(parameters, ("|".join(re.escape(name) for name in sorted(names)),))
                self.assertIsNotNone(re.fullmatch(parameters[0], names[0]))
                self.assertIsNone(re.fullmatch(parameters[0], "A1anything"))
