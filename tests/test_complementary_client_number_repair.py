import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import complementary_client_number_repair as repair
from credinomina_reconciliation.deposit_identity import DetailIdentityEvidence


class ClientNumberRepairTests(unittest.TestCase):
    def setUp(self):
        self.item = frappe._dict(name="X", doctype="CN Complementary Item", employer="EMP", client_number="MARVIN JOSE URBINA",
            client_name="MARVIN JOSE URBINA", source_client_name="MARVIN JOSE URBINA", loan_number="001139-1",
            amount_usd=19.32, docstatus=1, credit_client="")
        self.client = dict(name="5586", client_number="5586", employer="EMP", client_name="MARVIN JOSE URBINA", client_aliases=[])
        self.clients, self.evidence = [self.client], DetailIdentityEvidence()
        self.evidence["1139-1"] = {"5586"}
        self.evidence.loan_employers["1139-1"] = {"EMP"}

    def test_verified_owner_corrects_number_without_mutating_item_or_finances(self):
        before = copy.deepcopy(self.item)
        proposal = repair.plan(self.item, self.clients, self.evidence)
        self.assertEqual(proposal["client_number"], "5586")
        self.assertEqual(self.item, before)
        self.item.client_number = proposal["client_number"]
        self.assertIsNone(repair.plan(self.item, self.clients, self.evidence))

    def test_unknown_ambiguous_wrong_company_or_contradictory_names_require_review(self):
        for change in ("missing_loan", "missing_company", "unknown", "ambiguous", "company", "name", "beneficiary", "source"):
            with self.subTest(change=change):
                item, clients, evidence = copy.deepcopy((self.item, self.clients, self.evidence))
                if change == "missing_loan": item.loan_number = ""
                if change == "missing_company": item.employer = ""
                if change == "unknown": evidence = {}
                if change == "ambiguous": evidence["1139-1"] = {"5586", "OTHER"}
                if change == "company": clients[0]["employer"] = "OTHER"
                if change == "name": item.client_number = item.client_name = "OTRA PERSONA"
                if change == "beneficiary": item.credit_client = "OTHER"
                if change == "source": item.source_client_name = "OTRA PERSONA"
                with self.assertRaises(repair.ReviewRequired):
                    repair.plan(item, clients, evidence)

    def test_registered_alphanumeric_ids_and_numeric_numbers_are_never_replaced(self):
        for value in ("005586", "ID-ABC"):
            self.item.client_number = value
            self.assertIsNone(repair.plan(self.item, self.clients, self.evidence))
        self.item.client_number = "MARVIN JOSE URBINA"
        self.clients.append(dict(name="OTHER", client_number="MARVIN JOSE URBINA"))
        self.assertIsNone(repair.plan(self.item, self.clients, self.evidence))

    def test_exact_registered_alias_is_valid_but_similar_spelling_is_not(self):
        self.clients[0]["client_aliases"] = ["MARVIN JOSE URBINA"]
        self.clients[0]["client_name"] = "MARVIN JOSE URBINA SEVILLA"
        self.assertEqual(repair.plan(self.item, self.clients, self.evidence)["client"], "5586")
        self.item.client_number = self.item.client_name = "MARVIN JOSE URBINAS"
        with self.assertRaises(repair.ReviewRequired):
            repair.plan(self.item, self.clients, self.evidence)

    def test_historical_company_requires_actual_loan_evidence(self):
        from credinomina_reconciliation.deposit_identity import DetailIdentityEvidence
        historical = DetailIdentityEvidence()
        historical.update(self.evidence)
        historical.loan_employers["1139-1"] = {"EMP"}
        historical.company_clients["EMP"]["5586"] = dict(self.client)
        self.client["employer"] = "CURRENT-COMPANY"
        self.assertEqual(repair.plan(self.item, self.clients, historical)["client_number"], "5586")
        historical.loan_employers["1139-1"] = {"OTHER"}
        with self.assertRaises(repair.ReviewRequired):
            repair.plan(self.item, self.clients, historical)

    def test_corrupt_existing_journal_is_never_treated_as_missing_links(self):
        proposal = repair.plan(self.item, self.clients, self.evidence)
        deposit = frappe._dict(name="D", allocation_detail='[{"partida":"X"')
        with patch.object(frappe, "get_all", side_effect=[[], [deposit]]):
            with self.assertRaisesRegex(repair.ReviewRequired, "diario ilegible"):
                repair._assert_linked_clients(self.item, proposal)

    def test_dry_run_and_update_whitelist_never_call_save_or_reconciliation(self):
        self.item.save = Mock(side_effect=AssertionError("Must not save"))
        registered = frappe._dict(client_number="5586")
        with patch.object(frappe, "get_doc", side_effect=lambda doctype, *a, **k: self.item if doctype == self.item.doctype else registered), \
                patch.object(repair, "_assert_linked_clients"), patch.object(repair, "_record_version") as audit, \
                patch.object(frappe, "clear_document_cache"), patch.object(frappe.db, "set_value") as write:
            self.assertTrue(repair._repair("X", "EMP", self.clients, self.evidence, True))
            write.assert_not_called(); audit.assert_not_called()
            self.assertTrue(repair._repair("X", "EMP", self.clients, self.evidence, False))
            write.assert_called_once_with("CN Complementary Item", "X", "client_number", "5586", update_modified=False)
            self.item.save.assert_not_called()

    def test_existing_link_cannot_change_beneficiary_or_point_to_a_missing_row(self):
        proposal = repair.plan(self.item, self.clients, self.evidence)
        deposit = frappe._dict(name="D", docstatus=1, targets=[frappe._dict(complementary_item="X", detail_row="R")],
            detail_rows=[frappe._dict(name="R", client="OTHER", client_number="OTHER")], allocation_detail="[]")
        with patch.object(frappe, "get_all", side_effect=[["D"], []]), patch.object(frappe, "get_doc", return_value=deposit):
            with self.assertRaises(repair.ReviewRequired):
                repair._assert_linked_clients(self.item, proposal)
        deposit.detail_rows = []
        with patch.object(frappe, "get_all", side_effect=[["D"], []]), patch.object(frappe, "get_doc", return_value=deposit):
            with self.assertRaises(repair.ReviewRequired):
                repair._assert_linked_clients(self.item, proposal)

    def test_incompatible_record_is_reviewed_and_unexpected_failure_aborts_after_rollback(self):
        lock = patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool")
        with patch.object(repair, "candidate_rows", return_value=[self.item]), \
                patch.object(repair, "load_client_index", return_value=self.clients), \
                patch.object(repair, "load_detail_loan_clients", return_value=self.evidence), lock, \
                patch("credinomina_reconciliation.paying_employers.load_payer_map", return_value={}), \
                patch("credinomina_reconciliation.complementary_distribution.shared_company_pools", return_value=[]), \
                patch.object(frappe, "logger", return_value=Mock()), patch.object(frappe, "db", Mock()) as db, \
                patch.object(repair, "_repair", side_effect=repair.ReviewRequired("Ambiguo")) as apply:
            self.assertEqual(repair.run()["review"], {"X": "Ambiguo"})
            db.rollback.assert_called_once_with(save_point="repair_complementary_client_number")
            apply.side_effect = RuntimeError("Audit failure")
            with self.assertRaisesRegex(RuntimeError, "Audit failure"):
                repair.run()
            self.assertEqual(db.rollback.call_count, 2)
