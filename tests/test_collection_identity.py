import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import collection_identity as identity


class CollectionIdentityTests(unittest.TestCase):
    def test_linked_client_completes_only_blank_number(self):
        client = dict(name="769", client_number="769", employer="EMP")
        row = dict(client="769", client_number="", client_name="Nombre de la planilla")
        identity.complete_collection_client_number(row, client, "EMP")
        self.assertEqual(row["client_number"], "769")
        self.assertEqual(row["client_name"], "Nombre de la planilla")
        row["client_number"] = "000769"
        identity.complete_collection_client_number(row, client, "EMP")
        self.assertEqual(row["client_number"], "000769")

    def test_conflicting_number_or_company_is_reported_without_overwriting(self):
        def reject(message):
            raise ValueError(message)

        for number, employer, message in (("999", "EMP", "no coincide"), ("", "OTHER", "otra empresa")):
            row = dict(source_row=4, client_number=number)
            with self.subTest(number=number, employer=employer), \
                 patch.object(frappe, "throw", side_effect=reject), \
                 self.assertRaisesRegex(ValueError, "Fila 4.*" + message):
                identity.complete_collection_client_number(row, dict(name="769", client_number="769", employer=employer), "EMP")
            self.assertEqual(row["client_number"], number)

    def test_one_query_for_many_rows_without_storing_duplicate_number(self):
        rows = [frappe._dict(client="769") for _ in range(200)]
        period = frappe._dict(employer="EMP", status="Pendiente", collection_rows=rows)
        with patch.object(frappe, "get_all", return_value=[
            frappe._dict(name="769", client_number="769", employer="EMP")]) as query:
            identity.validate_collection_clients(period)
        query.assert_called_once()
        self.assertEqual(query.call_args.kwargs["filters"], {"name": ["in", ["769"]]})
        self.assertTrue(all("client_number" not in row for row in rows))
        self.assertEqual(identity.collection_record(rows[0])["client_number"], "769")

    def test_closed_period_does_not_change_historical_identity(self):
        row = frappe._dict(client="769", client_number="")
        with patch.object(frappe, "get_all") as query:
            identity.validate_collection_clients(frappe._dict(status="Cerrado", collection_rows=[row]))
        query.assert_not_called()
        self.assertFalse(row.client_number)
