import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import remittance_client_alias as api


class Row(frappe._dict):
    def as_dict(self):
        return dict(self)


class RemittanceClientAliasTests(unittest.TestCase):
    def setUp(self):
        self.row = Row(name="R66", source_row=66, loan_number="12671-1", client_name="JEFRI RAMON GARCIA SANCHEZ")
        self.doc = Row(name="DEP", employer="EMP", docstatus=0, modified="d1", detail_rows=[self.row],
            check_permission=Mock(), _assert_open_related_periods=Mock(), save=Mock(), add_comment=Mock())
        self.person = dict(name="2635", client_number="2635", employer="EMP", employee_number="E1",
            national_id="CED1", client_name="JEFFRY RAMON GARCIA SANCHEZ", client_aliases=[])
        self.client = Row(name="2635", client_number="2635", client_name=self.person["client_name"],
            modified="c1", check_permission=Mock(), append=Mock(), save=Mock(), add_comment=Mock())

    @contextmanager
    def evidence(self, loans=None, people=None):
        with patch.object(api, "load_client_index", return_value=people or [self.person]), \
             patch.object(api, "allowed_employers", return_value={"EMP"}), \
             patch.object(api, "load_detail_loan_clients", return_value=loans if loans is not None else {"12671-1": {"2635"}}), \
             patch.object(frappe, "throw", side_effect=ValueError):
            yield

    def test_credit_number_or_employee_can_verify_unknown_name(self):
        for identity in [dict(loan_number="12671-1"), dict(client_number="2635"), dict(employee_number="E1")]:
            with self.subTest(identity=identity), self.evidence():
                self.row.loan_number = ""
                self.row.client_number = self.row.employee_number = ""
                self.row.update(identity)
                row, candidate = api._candidate(self.doc, "R66")
                self.assertEqual(candidate["name"], "2635")
                self.assertEqual(row.client_name, "JEFRI RAMON GARCIA SANCHEZ")

    def test_non_clients_and_name_only_rows_are_not_alias_candidates(self):
        for loan, number in [("UNKNOWN", ""), ("", ""), ("12671-1", "UNKNOWN")]:
            with self.subTest(loan=loan, number=number), self.evidence():
                self.row.loan_number, self.row.client_number = loan, number
                with self.assertRaises(ValueError):
                    api._candidate(self.doc, "R66")

    def test_unknown_ambiguous_or_contradictory_credit_never_yields_alias(self):
        for loans in [{}, {"12671-1": {None}}, {"12671-1": {"2635", "OTHER"}}, {"12671-1": {"OTHER"}}]:
            with self.subTest(loans=loans), self.evidence(loans=loans):
                self.row.client_number = "2635"
                with self.assertRaises(ValueError):
                    api._candidate(self.doc, "R66")

    def test_verified_name_or_other_identifier_conflict_is_not_eligible(self):
        for changes in [dict(client_name=self.person["client_name"]), dict(employee_number="OTHER"),
                        dict(national_id="OTHER"), dict(employer="UNAUTHORIZED"), dict(client="OTHER")]:
            with self.subTest(changes=changes), self.evidence():
                original = dict(self.row)
                self.row.update(changes)
                with self.assertRaises(ValueError):
                    api._candidate(self.doc, "R66")
                self.row.clear(); self.row.update(original)

    def test_name_matching_another_person_cannot_be_added_as_alias(self):
        from credinomina_reconciliation.client_identity import ClientIdentityIndex
        from credinomina_reconciliation.deposit_identity import DetailIdentityEvidence
        other = dict(self.person, name="OTHER", client_name=self.row.client_name, client_number="999")
        evidence = DetailIdentityEvidence()
        evidence["12671-1"] = {"2635"}
        evidence.current_index = ClientIdentityIndex([self.person, other])
        self.row.client_number = "2635"
        with self.evidence(people=[self.person, other], loans=evidence):
            with self.assertRaises(ValueError):
                api._candidate(self.doc, "R66")

    def test_detail_must_belong_to_deposit(self):
        with self.evidence():
            with self.assertRaises(ValueError):
                api._candidate(self.doc, "FORGED")

    def test_preview_requires_client_permissions_and_returns_both_versions(self):
        with self.evidence(), patch.object(api, "_deposit", return_value=self.doc), \
             patch.object(frappe, "get_doc", return_value=self.client):
            result = api.preview_alias("DEP", "R66")
        self.assertEqual(result["alias_name"], self.row.client_name)
        self.assertEqual((result["client"], result["modified"], result["client_modified"]), ("2635", "d1", "c1"))
        self.assertEqual([call.args for call in self.client.check_permission.call_args_list], [("read",), ("write",)])
        self.client.save.assert_not_called()

    def create(self, **values):
        with self.evidence(), patch.object(api, "_deposit", return_value=self.doc), \
             patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool"), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["EMP"]), \
             patch.object(frappe, "get_doc", return_value=self.client):
            return api.create_alias(**(dict(remittance_name="DEP", detail_row_name="R66", client_name="2635",
                modified="d1", client_modified="c1") | values))

    def test_create_uses_source_name_and_reidentifies_without_allocating(self):
        self.doc.save.side_effect = lambda: self.row.update(client="2635")
        self.doc.allocation_detail = '[{"importe": 10}]'
        self.assertEqual(self.create()["client"], "2635")
        self.client.append.assert_called_once_with("aliases", {"alias_name": self.row.client_name})
        self.client.save.assert_called_once_with()
        self.doc.save.assert_called_once_with()
        self.assertEqual(self.doc.allocation_detail, '[{"importe": 10}]')
        self.assertEqual(self.row.client_name, "JEFRI RAMON GARCIA SANCHEZ")
        self.assertEqual(self.doc.result, "Pendiente")

    def test_stale_versions_or_tampered_client_rejected_before_write(self):
        for values in [dict(modified="old"), dict(client_modified="old"), dict(client_name="OTHER")]:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.create(**values)
                self.client.save.assert_not_called()
                self.doc.save.assert_not_called()

    def test_save_failure_propagates_for_request_rollback(self):
        self.doc.save.side_effect = ValueError("Closed period")
        with self.assertRaisesRegex(ValueError, "Closed period"):
            self.create()

    def test_cancelled_linked_or_closed_deposit_cannot_use_alias_flow(self):
        for values in [dict(docstatus=2), dict(consolidated_deposit="OTHER"), dict(cash_identity_status="Origen vinculado")]:
            with self.subTest(values=values), patch.object(frappe, "get_doc", return_value=Row(self.doc | values)), \
                 patch.object(frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    api._deposit("DEP")
        self.doc._assert_open_related_periods.side_effect = ValueError("Closed period")
        with patch.object(frappe, "get_doc", return_value=self.doc):
            with self.assertRaisesRegex(ValueError, "Closed period"):
                api._deposit("DEP")
