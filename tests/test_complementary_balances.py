import unittest
from unittest.mock import Mock, patch
import frappe
from credinomina_reconciliation.complementary_balances import financial_balance, get_balance, cached_balance_reads, load_balances, _distribution_index


class ComplementaryBalanceTests(unittest.TestCase):
    def test_nested_read_operation_caches_requested_items_without_sharing_mutations(self):
        deposits = [frappe._dict(name='D', deposit_date='2025-05-01', allocation_detail='['
            '{"tipo":"Partida complementaria","partida":"A","importe_usd":3},'
            '{"tipo":"Partida complementaria","partida":"B","importe_usd":4}]')]
        a = dict(name='A', category='Otros ingresos', amount_usd=10, docstatus=1)
        b = dict(a, name='B')
        @cached_balance_reads
        def nested():
            return load_balances([b])
        @cached_balance_reads
        def report():
            first = load_balances([a])
            first['A']['distributions'][0]['amount_usd'] = 99
            return nested(), load_balances([a])
        with patch('credinomina_reconciliation.report_records.records', return_value=deposits) as read:
            second, repeated = report()
            self.assertEqual(second['B']['used_usd'], 4)
            self.assertEqual(repeated['A']['used_usd'], 3)
            self.assertEqual(read.call_count, 2)  # A and B each have a complete scoped read.
            self.assertEqual(read.call_args_list[0].kwargs['or_filters'], [['allocation_detail', 'like', '%"A"%']])
            self.assertEqual(read.call_args_list[1].kwargs['or_filters'], [['allocation_detail', 'like', '%"B"%']])
            report()  # a new request must establish a new read scope
            self.assertEqual(read.call_count, 4)

    def test_empty_selection_does_not_read_any_deposits(self):
        with patch('credinomina_reconciliation.report_records.records') as read:
            self.assertEqual(_distribution_index(set()), {})
        read.assert_not_called()

    def test_unreadable_journal_never_looks_like_an_unpaid_item(self):
        for content in ('broken', '{"tipo":"Partida complementaria","partida":"A"}',
                        '[null]', '[3]', '["Partida complementaria"]'):
            with self.subTest(content=content):
                deposit = frappe._dict(name='DEP-BAD', deposit_date='2025-05-01', allocation_detail=content)
                with patch('credinomina_reconciliation.report_records.records', return_value=[deposit]), \
                        patch.object(frappe, 'throw', side_effect=ValueError('evidence')) as reject:
                    with self.assertRaises(ValueError):
                        _distribution_index({'A'})
                self.assertIn('DEP-BAD', reject.call_args.args[0])

    def test_large_selection_batches_scope_and_counts_shared_deposit_once(self):
        wanted = {'COMP-' + str(i).zfill(4) for i in range(205)}
        deposit = frappe._dict(name='D', deposit_date='2025-05-01', allocation_detail='['
            '{"tipo":"Partida complementaria","partida":"COMP-0000","importe_usd":3},'
            '{"tipo":"Partida complementaria","partida":"COMP-0204","importe_usd":4},'
            '{"tipo":"Partida complementaria","partida":"OTHER","importe_usd":9}]')
        with patch('credinomina_reconciliation.report_records.records', return_value=[deposit]) as read:
            result = _distribution_index(wanted)
        self.assertEqual(read.call_count, 3)
        self.assertTrue(all(len(call.kwargs['or_filters']) <= 100 for call in read.call_args_list))
        self.assertEqual(set(result), {'COMP-0000', 'COMP-0204'})
        self.assertEqual(len(result['COMP-0000']), 1)
        self.assertEqual(len(result['COMP-0204']), 1)

    def test_cached_zero_is_complete_but_new_item_gets_its_own_full_read(self):
        a = dict(name='A', category='Otros ingresos', amount_usd=10, docstatus=1)
        b = dict(a, name='B')
        @cached_balance_reads
        def report():
            load_balances([a])
            load_balances([a])
            return load_balances([b])
        with patch('credinomina_reconciliation.report_records.records', side_effect=[[], []]) as read:
            self.assertEqual(report()['B']['pending_usd'], 10)
        self.assertEqual(read.call_count, 2)

    def test_mutating_commands_are_not_implicitly_cached(self):
        a = dict(name='A', category='Otros ingresos', amount_usd=10, docstatus=1)
        first = frappe._dict(name='D', deposit_date='2025-05-01', allocation_detail='[]')
        changed = frappe._dict(first, allocation_detail='[{"tipo":"Partida complementaria","partida":"A","importe_usd":10}]')
        with patch('credinomina_reconciliation.report_records.records', side_effect=[[first], [changed]]) as read:
            self.assertEqual(load_balances([a])['A']['pending_usd'], 10)
            self.assertEqual(load_balances([a])['A']['pending_usd'], 0)
            self.assertEqual(read.call_count, 2)

    def test_exception_does_not_leave_a_read_scope_active(self):
        @cached_balance_reads
        def fail():
            load_balances([dict(name='A', category='Otros ingresos')])
            raise RuntimeError('Report failed')
        with patch('credinomina_reconciliation.report_records.records', return_value=[]) as read:
            with self.assertRaises(RuntimeError):
                fail()
            load_balances([dict(name='A', category='Otros ingresos')])
            self.assertEqual(read.call_count, 2)
    def test_form_endpoint_accepts_frappe_document_not_only_dict(self):
        values = {"name": "C", "category": "Otros ingresos", "amount_usd": 100, "docstatus": 1}
        document = Mock(get=lambda field: values.get(field))
        document.name = "C"
        with patch.object(frappe, "get_doc", return_value=document), patch.object(frappe, "get_list", return_value=[]):
            result = get_balance("C")
        document.check_permission.assert_called_once_with("read")
        self.assertEqual(result["pending_usd"], 100)

    def test_cash_balance_counts_signed_actual_allocations_only(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                result = financial_balance({"category": "Ajuste de conciliación", "docstatus": 1, "amount_usd": sign * 500,
                    "accounting_status": "Registrada"}, [{"amount_usd": sign * 350}])
                self.assertEqual((result["used_usd"], result["pending_usd"], result["financial_status"]), (350, 150, "Parcial"))
        result = financial_balance({"category": "Otros ingresos", "docstatus": 1, "amount_usd": 10}, [{"amount_usd": 11}])
        self.assertEqual(result["financial_status"], "Revisar distribución")

    def test_credit_explained_does_not_hide_refund_due(self):
        for category in ("Saldo a favor del cliente", "Saldo a favor de la empresa"):
            result = financial_balance({"category": category, "docstatus": 1, "amount_usd": 100,
                "result": "Saldo a favor documentado", "credit_management_status": "Parcialmente resuelto", "credit_pending_usd": 40})
            self.assertEqual((result["pending_usd"], result["management_pending_usd"]), (0, 40))

    def test_adjustment_residual_is_not_silently_discarded(self):
        result = financial_balance({"category": "Ajuste de aplicación", "docstatus": 1, "amount_usd": 100,
            "related_application": "R", "application_adjustment_usd": 60})
        self.assertEqual((result["pending_usd"], result["financial_status"]), (40, "Parcial"))

    def test_registered_is_not_conciliated_and_offset_not_a_deposit(self):
        result = financial_balance({"category": "Otros ingresos", "docstatus": 1, "amount_usd": 100, "accounting_status": "Registrada"})
        self.assertEqual(result["financial_status"], "Pendiente")
        result = financial_balance({"category": "Compensación entre partidas", "amount_usd": -100, "compensated_usd": 25})
        self.assertEqual((result["used_label"], result["pending_usd"]), ("Compensado", 75))
