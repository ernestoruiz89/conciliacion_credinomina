import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_related_client as resolver
from credinomina_reconciliation import reconciliation_workbench as workbench


def person(name="3253", **values):
    return dict(name=name, client_number=name, employer="IMMSA", client_name="RONALD FLORES PEREZ",
                client_aliases=[]) | values


def lookup(**values):
    defaults = dict(company="IMMSA", probe=dict(client_name="RONAL FLORE PERE", client_number="", loan_number=""),
                    candidates=[person()], loan_map=None, exact=None, reason="Nombre no verificado")
    defaults.update(values)
    return tuple(defaults[key] for key in ("company", "probe", "candidates", "loan_map", "exact", "reason"))


class ComplementaryClientSuggestionTests(unittest.TestCase):
    def test_similar_name_only_proposes_same_company_and_never_auto_resolves(self):
        data = lookup(candidates=[person(), person("OTHER", employer="ELSE"), person("UNRELATED", client_name="JUAN MANUEL GOMEZ")])
        choices = resolver._suggestions(*data[:4])
        self.assertEqual([row["client"] for row in choices], ["3253"])
        self.assertGreater(choices[0]["similarity"], 65)
        with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                resolver._select(data)

    def test_manual_choice_preserves_verified_scope_without_creating_alias(self):
        company, identity = resolver._select(lookup(), "3253")
        self.assertEqual(company, "IMMSA")
        self.assertEqual(identity["clients"], {"3253"})
        self.assertNotIn("RONAL FLORE PERE", identity["name_values"])
        self.assertEqual(identity["name_values"], {"RONALD FLORES PEREZ"})

    def test_exact_name_duplicates_require_a_choice(self):
        data = lookup(probe=dict(client_name="RONALD FLORES PEREZ", client_number="", loan_number=""),
                      candidates=[person(), person("SECOND")])
        self.assertEqual(len(resolver._suggestions(*data[:4])), 2)
        self.assertEqual(resolver._select(data, "SECOND")[1]["clients"], {"SECOND"})

    def test_selection_cannot_bypass_company_identifiers_permissions_or_changed_candidates(self):
        for data, selected in [
            (lookup(), "FORGED"),
            (lookup(candidates=[person(employer="ELSE")]), "3253"),
            (lookup(probe=dict(client_name="RONAL FLORE PERE", client_number="999", loan_number="")), "3253"),
            (lookup(probe=dict(client_name="RONAL FLORE PERE", client_number="", loan_number="LOAN"),
                    loan_map={"LOAN": {"3253", "OTHER"}}), "3253"),
            (lookup(candidates=[]), "3253"),
            (lookup(exact=person()), "OTHER"),
        ]:
            with self.subTest(selected=selected, data=data), patch.object(frappe, "throw", side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    resolver._select(data, selected)

    def test_suggestions_are_bounded_and_need_a_company(self):
        data = lookup(candidates=[person(str(index)) for index in range(25)])
        self.assertEqual(len(resolver._suggestions(*data[:4])), 10)
        self.assertEqual(resolver._suggestions("", *data[1:4]), [])

    def test_endpoint_returns_choices_then_reads_only_after_explicit_selection(self):
        source = frappe._dict(name="ITEM")
        company = Mock()
        with patch.object(frappe, "get_doc", side_effect=lambda dt, name: source if dt == "CN Complementary Item" else company), \
             patch.object(resolver, "_lookup", return_value=lookup()) as load, \
             patch.object(workbench, "_client_data", return_value=dict(applications=[], rows=[])) as read:
            preview = resolver.get_related_pending("ITEM")
            self.assertTrue(preview["needs_client_selection"])
            self.assertEqual(preview["candidates"][0]["client"], "3253")
            read.assert_not_called()
            result = resolver.get_related_pending("ITEM", "3253")
            self.assertEqual(load.call_count, 2, "Recheck candidates and permissions for each request")
            self.assertEqual(result["context"], dict(complementary_item="ITEM", client_choice="3253"))
            self.assertEqual(read.call_args.args[1]["clients"], {"3253"})

    def test_workbench_revalidates_the_chosen_client_in_complementary_context(self):
        source = Mock()
        identity = dict(numbers={"3253"}, clients={"3253"}, national_ids=set(), names=set())
        with patch.object(frappe, "get_doc", return_value=source), \
             patch.object(resolver, "resolve", return_value=("IMMSA", identity)) as resolve:
            workbench._scope(context=dict(complementary_item="ITEM", client_choice="3253"))
        resolve.assert_called_once_with(source, "3253")


if __name__ == "__main__":
    unittest.main()
