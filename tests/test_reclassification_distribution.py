import unittest
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.reclassification_distribution import APPLICATION, DEPOSIT, COMPANY_CREDIT, INCOME, OTHER, validate_portion, guard_journal, application_links, deposit_credits, preview_destination
from credinomina_reconciliation.complementary_balances import financial_balance


class SplitReclassificationTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(name="CORE", docstatus=1, accounting_source_key="K", source_currency="USD",
                         source_debit=.10, source_credit=0, amount_usd=.10, employer="EMPRESA 1")

    def test_ten_companies_cannot_exceed_original_debit(self):
        existing = []
        for n in range(10):
            target = dict(employer=f"EMPRESA {n}", client_name=f"CLIENTE {n}", available_usd=.01)
            result = validate_portion(self.item, target, DEPOSIT, .01, existing,
                                      override=True, reason="Ajuste global verificado")
            existing.append(dict(amount_usd=result["amount_usd"]))
        self.assertEqual(result["remaining_usd"], 0)
        with self.assertRaises(frappe.ValidationError):
            validate_portion(self.item, target, DEPOSIT, .01, existing, override=True, reason="Ajuste")

    def test_exception_needs_explicit_check_and_reason(self):
        target = dict(employer="OTRA", client_name="OTRO", available_usd=.10)
        for override, reason in ((False, "Revisado"), (True, "")):
            with self.subTest(override=override, reason=reason), self.assertRaises(frappe.ValidationError):
                validate_portion(self.item, target, DEPOSIT, .01, [], override=override, reason=reason)

    def test_direction_and_destination_capacity_are_enforced(self):
        target = dict(employer="EMPRESA 1", client_name="CLIENTE", available_usd=.01)
        with self.assertRaises(frappe.ValidationError):
            validate_portion(self.item, target, APPLICATION, .01, [], override=True, reason="Revisado")
        with self.assertRaises(frappe.ValidationError):
            validate_portion(self.item, target, DEPOSIT, .02, [], override=True, reason="Revisado")
        credit = dict(self.item, source_debit=0, source_credit=.10)
        result = validate_portion(credit, target, APPLICATION, .01, [], override=True, reason="Revisado")
        self.assertEqual(str(result["remaining_usd"]), "0.09")

    def test_journal_counts_portions_and_cannot_be_edited_directly(self):
        entry = dict(operation_id="OP", kind=DEPOSIT, target="D", parent_document="DEP", employer="E", client="C",
                     credit_origin_date="2025-04-01", effective_date="2025-04-01", amount_usd=.03)
        previous = dict(self.item, category="Compensación entre partidas", reclassification_allocations=json.dumps([entry]))
        balance = financial_balance(previous)
        self.assertEqual((balance["used_usd"], balance["pending_usd"], balance["financial_status"]), (.03, .07, "Parcial"))
        with self.assertRaises(frappe.ValidationError):
            guard_journal(dict(previous, reclassification_allocations="[]"), previous)
        with self.assertRaises(frappe.ValidationError):
            guard_journal(dict(previous, employer="OTRA"), previous)

    def test_application_portions_use_target_company_and_effective_date(self):
        entries = [dict(operation_id="A", kind=APPLICATION, target="SRC", parent_document="IMPORT", employer="OTRA",
                        effective_date="2025-06-01", amount_usd=.01),
                   dict(operation_id="B", kind=APPLICATION, target="SRC", parent_document="IMPORT", employer="OTRA",
                        effective_date="2025-07-01", amount_usd=.02)]
        items = [dict(name="CORE", employer="ORIGEN", reclassification_allocations=json.dumps(entries))]
        rows = application_links(items, {"SRC"}, employer="OTRA", cutoff="2025-06-15")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].related_application, rows[0].application_adjustment_usd), ("SRC", .01))
        self.assertEqual(application_links(items, {"SRC"}, employer="ORIGEN"), [])

    def test_income_is_not_a_customer_credit(self):
        shared = dict(parent_document="DEP", employer="E", client="C", client_name="Nombre",
                      credit_origin_date="2025-04-01", effective_date="2025-05-01", amount_usd=.01)
        entries = [dict(shared, operation_id="A", kind=DEPOSIT, target="R1"),
                   dict(shared, operation_id="B", kind=INCOME, target="R2"),
                   dict(shared, operation_id="C", kind=COMPANY_CREDIT, target="R3"),
                   dict(shared, operation_id="D", kind=OTHER, target="R4")]
        items = [dict(name="CORE", reclassification_allocations=json.dumps(entries))]
        credits = deposit_credits(items, {"DEP"}, cutoff="2025-04-15")
        self.assertEqual(len(credits), 2)
        self.assertEqual((credits[0].credit_detail_row, credits[0].credit_pending_usd), ("R1", .01))
        self.assertEqual(credits[1].category, "Saldo a favor de la empresa")

    def test_company_credit_remains_pending_until_documented_management(self):
        entry = dict(operation_id="PAGO-DUPLICADO", kind=COMPANY_CREDIT, target="FILA",
                     parent_document="DEP", employer="EMPRESA", credit_origin_date="2025-04-01",
                     effective_date="2025-05-01", amount_usd=.02,
                     management_history=[dict(fecha="2025-06-01", importe_usd=.01),
                                         dict(fecha="2025-07-01", importe_usd=-.01)])
        item = dict(name="CORE", reclassification_allocations=json.dumps([entry]))
        def balance(cutoff):
            return deposit_credits([item], {"DEP"}, cutoff=cutoff)[0]
        self.assertEqual(balance("2025-04-15").credit_pending_usd, .02)
        self.assertEqual(balance("2025-06-15").credit_pending_usd, .01)
        self.assertEqual(balance("2025-07-15").credit_pending_usd, .02)
        self.assertEqual(balance("2025-04-15").category, "Saldo a favor de la empresa")

    def test_journal_rejects_hidden_destination_without_exposing_its_name(self):
        from credinomina_reconciliation.report_records import _require_reclassification_targets
        entry = dict(operation_id="P", kind=COMPANY_CREDIT, target="ROW", parent_document="SECRETO",
                     employer="E", credit_origin_date="2025-04-01", effective_date="2025-05-01", amount_usd=.01)
        item = dict(reclassification_allocations=json.dumps([entry]))
        with patch.object(frappe, "has_permission", return_value=False):
            with self.assertRaises(frappe.PermissionError) as error:
                _require_reclassification_targets(item)
        self.assertNotIn("SECRETO", str(error.exception))

    def test_deposit_lookup_limits_candidate_journals(self):
        from credinomina_reconciliation.reclassification_distribution import load_deposit_credits
        with patch.object(frappe, 'get_all', return_value=[]) as query:
            self.assertEqual(load_deposit_credits({'DEP-123'}), [])
        self.assertEqual(query.call_args.kwargs['or_filters'],
                         [['reclassification_allocations', 'like', '%"DEP-123"%']])

    def test_deposit_picker_filters_identity_and_zero_available_with_or_without_override(self):
        class Doc(frappe._dict):
            def check_permission(self, permission):
                pass
        item = Doc(employer='E', client_name='SIRIO JUNIOR LEIVA OBANDO', loan_number='', client_number='')
        def row(name, client, pending, linked=0):
            return frappe._dict(name=name, client=name, employer='E', client_name=client, client_number='', loan_number='',
                                source_row=1, amount_usd=10, linked_usd=linked, pending_usd=pending, identity_reason='')
        deposit = Doc(name='DEP', employer='E', docstatus=1, modified='v1', deposit_date='2025-04-01',
                      amount_usd=40, allocated_usd=10, detail_rows=[
                          row('matching', 'SIRIO JUNIOR LEIVA OBANDO', 5),
                          row('other', 'OTRO CLIENTE', 5),
                          row('settled', 'SIRIO JUNIOR LEIVA OBANDO', 0, 10),
                          row('reserved', 'SIRIO JUNIOR LEIVA OBANDO', 5)])
        credit = frappe._dict(credit_detail_row='reserved', amount_usd=10)
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: item if doctype == 'CN Complementary Item' else deposit), \
                patch.object(frappe, 'has_permission', return_value=True), \
                patch('credinomina_reconciliation.paying_employers.allowed_employers', return_value={'E'}), \
                patch('credinomina_reconciliation.client_credit.load_credits', return_value=[credit]):
            normal = preview_destination('CORE', 'DEP', 'deposit')
            exception = preview_destination('CORE', 'DEP', 'deposit', override=1)
        self.assertEqual([r['name'] for r in normal['rows']], ['matching'])
        self.assertEqual([r['name'] for r in exception['rows']], ['matching', 'other'])
        self.assertEqual(normal['rows'][0]['pending_usd'], 5)

    def test_application_picker_uses_actual_uncovered_balance(self):
        class Doc(frappe._dict):
            def check_permission(self, permission):
                pass
            def as_dict(self):
                return dict(self)
        item = Doc(employer='E', client_name='SIRIO', client_number='', loan_number='')
        def row(name, client):
            return Doc(name=name, event_type='Aplicacion', effective=1, currency='USD', client_name=client,
                       client_number='', loan_number='', source_row=1, amount_usd=10,
                       historical_period='P', collection_period='')
        document = Doc(employer='E', docstatus=0, status='Importado', modified='v1',
                       rows=[row('open', 'SIRIO'), row('paid', 'SIRIO'), row('other', 'OTRO')])
        def coverage(source, context=None):
            return {'adjustable_usd': 0 if source.name == 'paid' else 4}
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: item if doctype == 'CN Complementary Item' else document), \
                patch('credinomina_reconciliation.application_adjustment_links.confirmed', return_value=[]), \
                patch('credinomina_reconciliation.application_candidate_context.CandidateContext'), \
                patch('credinomina_reconciliation.application_adjustments.cash_coverage', side_effect=coverage):
            normal = preview_destination('CORE', 'IMPORT', 'application')
            exception = preview_destination('CORE', 'IMPORT', 'application', override=1)
        self.assertEqual([r['name'] for r in normal['rows']], ['open'])
        self.assertEqual([r['name'] for r in exception['rows']], ['open', 'other'])
        self.assertEqual(normal['rows'][0]['pending_usd'], 4)
