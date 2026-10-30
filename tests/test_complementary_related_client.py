import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_related_client as resolver
from credinomina_reconciliation import reconciliation_workbench as workbench


def client(name="7593", **values):
    return frappe._dict(dict(name=name, client_number=name, client_name="JOSUE ELI MARTINEZ LAZO",
                            employer="LALA", national_id="CEDULA") | values)


def item(**values):
    return frappe._dict(dict(name="ITEM", employer="LALA", check_permission=Mock()) | values)


class ComplementaryRelatedClientTests(unittest.TestCase):
    def resolve(self, source, clients=None, owners=None, aliases=None):
        with patch.object(resolver, "records", return_value=clients if clients is not None else [client()]), \
             patch.object(resolver, "child_records", return_value=aliases or []), \
             patch.object(resolver, "_loan_owners", return_value=owners or set()) as loans, \
             patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "throw", side_effect=frappe.ValidationError):
            result = resolver.resolve(source)
            if not source.get("loan_number"):
                loans.assert_not_called()
            source.check_permission.assert_called_once_with("read")
            return result

    def test_any_single_identifier_resolves_without_filling_the_origin(self):
        for values in [dict(client_name="Josué Eli Martínez Lazo"), dict(client_number="007593"),
                       dict(loan_number="0013682-1")]:
            with self.subTest(values=values):
                source = item(**values)
                before = dict(source)
                company, identity = self.resolve(source, owners={"7593"})
                self.assertEqual(company, "LALA")
                self.assertEqual(identity["client_number"], "7593")
                self.assertEqual(identity["clients"], {"7593"})
                self.assertEqual(identity["client_name"], "JOSUE ELI MARTINEZ LAZO")
                self.assertEqual(dict(source), before, "Lookup must not change client fields or amounts")

    def test_verified_alias_resolves_and_remains_in_query_identity(self):
        company, identity = self.resolve(item(client_name="JOSUE MARTINEZ"),
            aliases=[frappe._dict(parent="7593", alias_name="JOSUE MARTINEZ")])
        self.assertEqual(company, "LALA")
        self.assertIn("JOSUE MARTINEZ", identity["name_values"])

    def test_missing_company_uses_uniquely_resolved_clients_company(self):
        company, identity = self.resolve(item(employer="", client_number="7593"))
        self.assertEqual(company, "LALA")
        self.assertEqual(identity["numbers"], {"7593"})

    def test_unknown_ambiguous_and_contradictory_inputs_are_not_guessed(self):
        other = client("OTHER", client_name="OTRA PERSONA", national_id="OTRA-CEDULA")
        cases = [
            (item(), [client()], set()),
            (item(client_name="Sin identificar"), [client()], set()),
            (item(client_number="UNKNOWN"), [client()], set()),
            (item(client_name=client().client_name), [client(), client("DUPLICATE")], set()),
            (item(client_number="7593", client_name="OTRA PERSONA"), [client(), other], set()),
            (item(loan_number="13682-1"), [client(), other], {"7593", "OTHER"}),
            (item(loan_number="13682-1"), [client()], {None}),
            (item(client_number="7593", loan_number="13682-1"), [client(), other], {"OTHER"}),
        ]
        for source, clients, owners in cases:
            with self.subTest(source=source):
                with self.assertRaises(frappe.ValidationError):
                    self.resolve(source, clients=clients, owners=owners)

    def test_origin_permission_checked_before_directory_or_loan_lookup(self):
        source = item(client_number="7593", check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(resolver, "records") as read:
            with self.assertRaises(frappe.PermissionError):
                resolver.resolve(source)
            read.assert_not_called()

    def test_credit_lookup_uses_readable_imports_and_snapshots_and_normalizes_padding(self):
        calls = []
        def credit_rows(doctype, **kwargs):
            calls.append((doctype, kwargs))
            if doctype == "CN Source Row":
                return [frappe._dict(client="7593", client_number="7593", client_name=client().client_name,
                                     loan_number="0013682-1")]
            return []
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(resolver, "records", return_value=[frappe._dict(name="READABLE")]), \
             patch.object(resolver, "get_credit_rows", side_effect=credit_rows):
            owners = resolver._loan_owners("13682-1", "LALA", [client()])
        self.assertEqual(owners, {"7593"})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(kwargs["filters"]["parent"] == ["in", ["READABLE"]] for _, kwargs in calls))
        self.assertEqual(calls[1][1]["filters"]["employer"], "LALA")

    def test_conflicting_loan_evidence_is_retained_as_ambiguity(self):
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(resolver, "records", return_value=[frappe._dict(name="READABLE")]), \
             patch.object(resolver, "get_credit_rows", return_value=[frappe._dict(
                 client="7593", client_number="DIFFERENT", matched_client="7593", client_number_core="DIFFERENT")]):
            self.assertEqual(resolver._loan_owners("13682-1", "LALA", [client()]), {None})

    def test_workbench_preserves_complementary_origin_and_rechecks_company_access(self):
        source = item(client_number="7593")
        company_doc = Mock()
        identity = dict(numbers={"7593"}, clients={"7593"}, national_ids=set(), names=set())
        with patch.object(frappe, "get_doc", side_effect=[source, company_doc]) as read, \
             patch.object(resolver, "resolve", return_value=("LALA", identity)):
            self.assertEqual(workbench._scope(context={"complementary_item": "ITEM"}), ("LALA", identity))
        self.assertEqual(read.call_args_list[0].args, ("CN Complementary Item", "ITEM"))
        company_doc.check_permission.assert_called_once_with("read")


if __name__ == "__main__":
    unittest.main()
