import unittest

from credinomina_reconciliation.patches.v1_0.use_collection_client_link import plan_migration
from credinomina_reconciliation.reconciliation import application_matches_collection, match_collection_record, complementary_matches_collection


class CollectionClientMigrationTests(unittest.TestCase):
    def client(self, **values):
        return dict(name="769", client_number="769", client_name="ANA PEREZ", employer="EMP",
                    national_id="CED-A", client_aliases=[]) | values

    def plan(self, rows, clients=None):
        return plan_migration(rows, {"PER": {"employer": "EMP"}}, [self.client()] if clients is None else clients)

    def row(self, **values):
        return dict(name="ROW", parent="PER", client="", client_number="", client_name="ANA PEREZ",
                    national_id="CED-A", loan_number="12800-1", row_key="KEEP", applied_usd=79.23, remitted_usd=79.23) | values

    def test_link_number_and_unique_identity_all_resolve_without_changing_row_or_money(self):
        for values in ({"client": "769"}, {"client_number": "000769"}, {}):
            row = self.row(**values)
            before = dict(row)
            links, created = self.plan([row])
            self.assertEqual(links[0]["client"], "769")
            self.assertEqual(created, [])
            self.assertEqual(row, before)

    def test_missing_registry_client_is_planned_once_from_known_number(self):
        links, created = self.plan([self.row(name=name, client_number="769") for name in ("A", "B")], [])
        self.assertEqual(len(created), 1)
        self.assertEqual([row["client"] for row in links], ["769", "769"])

    def test_conflicting_link_number_company_and_missing_identity_block_preflight(self):
        for row, clients in (
            (self.row(client="769", client_number="999"), [self.client()]),
            (self.row(client="UNKNOWN"), [self.client()]),
            (self.row(client="769"), [self.client(employer="OTHER")]),
            (self.row(client="769", national_id="OTHER"), [self.client()]),
            (self.row(client_name="", national_id=""), []),
            (self.row(), [self.client(name="A", client_number="A"), self.client(name="B", client_number="B")]),
        ):
            with self.subTest(row=row, clients=clients), self.assertRaises(ValueError):
                self.plan([row], clients)

    def test_matching_uses_sole_link_even_if_an_obsolete_column_is_present(self):
        row = self.row(client="769", client_number="OBSOLETE")
        app = dict(client_number="000769", loan_number="12800-1")
        self.assertTrue(application_matches_collection(app, row))
        self.assertFalse(application_matches_collection(dict(app, client_number="999"), row))
        self.assertIs(match_collection_record(app, [row])[0], row)
        self.assertTrue(complementary_matches_collection(dict(app, reference="R"), row, "R"))
