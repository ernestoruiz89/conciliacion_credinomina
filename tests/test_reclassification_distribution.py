import unittest
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.reclassification_distribution import APPLICATION, DEPOSIT, COMPANY_CREDIT, INCOME, OTHER, validate_portion, guard_journal, application_links, deposit_credits, preview_destination, unique_credit_updates, apply_unique_credit_updates
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

    def test_full_distribution_to_one_credit_fills_only_empty_fields(self):
        item = dict(amount_usd=10, loan_number='', client_number='', reference='', client_name='PERSONA')
        rows = [dict(kind=DEPOSIT, parent_document='DEP', employer='E', loan_number='001234-1',
                     client_number='0042', client_name='PERSONA', amount_usd=4),
                dict(kind=DEPOSIT, parent_document='DEP', employer='E', loan_number='1234-1',
                     client_number='42', client_name='PERSONA', amount_usd=6)]
        self.assertEqual(unique_credit_updates(item, rows[:1], {'DEP': 'BANCO'}), {})
        self.assertEqual(unique_credit_updates(item, rows, {'DEP': 'BANCO'}),
                         {'loan_number': '001234-1', 'client_number': '0042', 'reference': 'BANCO'})
        self.assertEqual(unique_credit_updates(dict(item, loan_number='1234-1', client_number='42', reference='ORIGINAL'),
                                               rows, {'DEP': 'BANCO'}), {})
        self.assertEqual(unique_credit_updates(item, [rows[0], dict(rows[1], loan_number='987-1')], {'DEP': 'BANCO'}), {})
        self.assertEqual(unique_credit_updates(item, [rows[0], dict(rows[1], employer='OTRA')], {'DEP': 'BANCO'}), {})
        self.assertEqual(unique_credit_updates(item, [rows[0], dict(rows[1], client_number='999')], {'DEP': 'BANCO'}), {})

    def test_reference_only_when_deposit_reference_is_unambiguous(self):
        item = dict(amount_usd=10, loan_number='', client_number='', reference='')
        base = dict(kind=DEPOSIT, employer='E', loan_number='10-1', client_number='42', client_name='C')
        rows = [dict(base, parent_document='D1', amount_usd=5), dict(base, parent_document='D2', amount_usd=5)]
        self.assertNotIn('reference', unique_credit_updates(item, rows, {'D1': 'A', 'D2': 'B'}))
        self.assertNotIn('reference', unique_credit_updates(item, rows, {'D1': 'A', 'D2': ''}))
        self.assertEqual(unique_credit_updates(item, rows, {'D1': 'A', 'D2': 'A'})['reference'], 'A')
        applications = [dict(base, kind=APPLICATION, parent_document='I', amount_usd=10)]
        self.assertNotIn('reference', unique_credit_updates(item, applications, {'I': 'VOUCHER'}))

    def test_hidden_destination_never_populates_source_fields(self):
        item = frappe._dict(doctype='CN Complementary Item', name='CORE', amount_usd=1)
        rows = [dict(kind=DEPOSIT, parent_document='D', employer='E', loan_number='10-1', amount_usd=1)]
        with patch.object(frappe.db, 'exists', return_value=True), patch.object(frappe, 'has_permission', return_value=False), \
                patch.object(frappe.db, 'set_value') as write:
            self.assertEqual(apply_unique_credit_updates(item, rows), {})
        write.assert_not_called()

    def test_legacy_installment_migration_preserves_existing_values(self):
        from credinomina_reconciliation.patches.v1_0.preserve_complementary_legacy_installment import execute
        with patch.object(frappe.db, 'has_column', return_value=True), patch.object(frappe.db, 'sql') as sql:
            execute()
        self.assertIn("SET legacy_installment_number = installment_number", sql.call_args.args[0])
        self.assertIn("COALESCE(legacy_installment_number, '') = ''", sql.call_args.args[0])
        with patch.object(frappe.db, 'has_column', return_value=False), patch.object(frappe.db, 'sql') as sql:
            execute()
        sql.assert_not_called()

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
        with self.assertRaises(frappe.ValidationError):
            guard_journal(dict(previous, reclassification_deletions='[{"forged":true}]'), previous)

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
        current = financial_balance(dict(item, docstatus=1, category='Compensación entre partidas', amount_usd=.02))
        self.assertEqual(current['management_pending_usd'], .02)
        self.assertEqual(current['management_status'], 'Pendiente')

    def test_management_missing_reference_returns_validation_without_changing_history(self):
        from credinomina_reconciliation.reclassification_distribution import record_credit_management
        entry = dict(management_history=[])
        args = dict(item_name='CORE', operation_id='P', modified='v1', treatment='Devolución',
                    amount_usd=.01, event_date='2025-12-01')
        with patch('credinomina_reconciliation.reclassification_distribution._credit_management_item',
                   return_value=(None, [entry], entry)), patch.object(frappe.db, 'set_value') as write:
            for reference in ({}, {'reference': None}, {'reference': ''}, {'reference': '   '}):
                with self.subTest(reference=reference), self.assertRaisesRegex(frappe.ValidationError, 'referencia del comprobante'):
                    record_credit_management(**args, **reference)
            write.assert_not_called()
        self.assertEqual(entry['management_history'], [])

    def test_reconciled_customer_credit_still_requires_refund_management(self):
        entry = dict(operation_id='P', kind=DEPOSIT, target='R', parent_document='DEP', employer='E', client='5471',
                     credit_origin_date='2025-11-12', effective_date='2025-12-31', amount_usd=25.22)
        item = dict(name='CN-COMP-2026-00515', docstatus=1, category='Compensación entre partidas', amount_usd=25.22,
                    reclassification_allocations=json.dumps([entry]))
        result = financial_balance(item)
        self.assertEqual((result['pending_usd'], result['management_pending_usd'], result['management_status']), (0, 25.22, 'Pendiente'))
        entry['management_history'] = [dict(fecha='2026-01-01', importe_usd=5)]
        item['reclassification_allocations'] = json.dumps([entry])
        self.assertEqual(financial_balance(item)['management_pending_usd'], 20.22)
        entry['management_history'].append(dict(fecha='2026-02-01', importe_usd=20.22))
        item['reclassification_allocations'] = json.dumps([entry])
        self.assertEqual(financial_balance(item)['management_status'], 'Resuelto')

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
