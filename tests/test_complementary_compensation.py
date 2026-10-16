import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import complementary_compensation as offsets


def item(name="A", amount=100, **kwargs):
    return frappe._dict(name=name, amount_usd=amount, posting_date="2025-04-01", docstatus=0,
                        category="Por clasificar", flags={}, compensations=[], **kwargs)


class ComplementaryCompensationTests(unittest.TestCase):
    def test_cutoff_partial_full_and_decimal_precision(self):
        doc = item(amount=46.53)
        doc.compensations = [frappe._dict(amount_usd=46.52, compensation_date="2025-08-01"),
                             frappe._dict(amount_usd=.01, compensation_date="2025-09-01")]
        self.assertEqual(offsets.balance(doc, "2025-03-31")["original_usd"], 0)
        self.assertEqual(offsets.balance(doc, "2025-04-30")["pending_usd"], 46.53)
        self.assertEqual(offsets.balance(doc, "2025-08-01")["pending_usd"], .01)
        self.assertEqual(offsets.balance(doc, "2025-08-31")["status"], "Compensada parcialmente")
        self.assertEqual(offsets.balance(doc, "2025-09-01")["status"], "Compensada totalmente")
        self.assertEqual(doc.amount_usd, 46.53)

    def test_evidence_direction_not_unsigned_import_amount(self):
        self.assertEqual(offsets._direction(item(accounting_source_key="k", source_debit=100, source_credit=0)), 1)
        self.assertEqual(offsets._direction(item(accounting_source_key="k", source_debit=0, source_credit=100)), -1)
        self.assertEqual(offsets._direction(item(amount=-100)), -1)

    def test_pair_guards(self):
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=False), get_value=Mock(return_value="Pendiente"))), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(offsets, "_", side_effect=lambda text: text):
            offsets._validate_pair(item(), item("B", -100))  # unidentified companies are allowed
            cases = [(item(), item()), (item(), item("B", 100)),
                     (item(employer="E1"), item("B", -100, employer="E2")),
                     (item(source_account="1"), item("B", -100, source_account="2")),
                     (item(related_application="APP"), item("B", -100)),
                     (item(registered_deposit="DEP"), item("B", -100))]
            for left, right in cases:
                with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                    offsets._validate_pair(left, right)

    def test_closed_period_and_reserved_deposit_block(self):
        with patch.object(frappe, "throw", side_effect=ValueError), patch.object(offsets, "_", side_effect=lambda text: text):
            with patch.object(frappe, "db", Mock(get_value=Mock(return_value="Cerrado"))), self.assertRaises(ValueError):
                offsets._eligible(item(period="P"))
            with patch.object(frappe, "db", Mock(exists=Mock(return_value=True))), self.assertRaises(ValueError):
                offsets._eligible(item())

    def test_manual_ledger_writes_and_forged_flag_rejected(self):
        doc = item()
        doc.flags["compensation_write_token"] = True
        doc.compensations = [frappe._dict(amount_usd=100, operation_id="forged")]
        with patch.object(frappe, "throw", side_effect=ValueError), patch.object(offsets, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                offsets.guard_document(doc)
            with self.assertRaises(ValueError):
                offsets.guard_delete(doc)

    def test_confirmed_fields_frozen_and_totals_recomputed(self):
        old = item()
        old.category = offsets.CATEGORY
        old.compensations = [frappe._dict(amount_usd=30, compensation_date="2025-08-01")]
        changed = frappe._dict(old, amount=999)
        with patch.object(frappe, "throw", side_effect=ValueError), patch.object(offsets, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                offsets.guard_document(changed, old)
        with patch.object(offsets, "nowdate", return_value="2026-10-01"):
            offsets.update_totals(old)
        self.assertEqual(old.compensation_pending_usd, 70)
        self.assertEqual(old.compensated_usd, 30)

    def test_confirmation_requires_permission_on_both_documents(self):
        doc = Mock()
        doc.check_permission.side_effect = PermissionError
        with patch.object(frappe, "get_doc", return_value=doc), self.assertRaises(PermissionError):
            offsets.confirm_compensation("A", "B", 10, "2025-08-01", "Test", "a" * 32)
        doc.submit.assert_not_called()

    def test_json_numeric_roundtrip_is_not_an_edit(self):
        old = item()
        old.amount = 100.0
        old.compensations = [frappe._dict(amount_usd=30.0, compensation_date="2025-08-01")]
        current = frappe._dict(old, amount=100, compensations=[frappe._dict(amount_usd=30, compensation_date="2025-08-01")])
        offsets.guard_document(current, old)

    def test_balance_api_checks_read_permission(self):
        doc = Mock()
        doc.check_permission.side_effect = PermissionError
        with patch.object(frappe, "get_doc", return_value=doc), self.assertRaises(PermissionError):
            offsets.get_compensation_balance("A", "2025-08-01")

    def reversal_pair(self):
        left, right = item(), item("B", -100)
        for doc, other in ((left, right), (right, left)):
            doc.category = offsets.CATEGORY
            doc.docstatus = 1
            doc.flags = frappe._dict()
            doc.check_permission = Mock()
            doc.save = Mock()
            doc.append = lambda field, value, doc=doc: doc[field].append(frappe._dict(value))
            doc.compensations = [frappe._dict(operation_id="a" * 32, counterpart=other.name,
                amount_usd=30, compensation_date="2025-08-01", reason="Original")]
        return left, right

    def reversal_context(self, left, right):
        stack = ExitStack()
        stack.enter_context(patch.object(frappe, "get_doc", side_effect=lambda doctype, name, **kw: left if name == "A" else right))
        stack.enter_context(patch.object(frappe, "db", Mock(exists=Mock(return_value=False))))
        stack.enter_context(patch.object(frappe, "session", frappe._dict(user="reviewer@example.test")))
        def reject(message, *args, **kwargs):
            raise ValueError(message)
        stack.enter_context(patch.object(frappe, "throw", side_effect=reject))
        stack.enter_context(patch.object(offsets, "_", side_effect=lambda text: text))
        stack.enter_context(patch.object(offsets, "nowdate", return_value="2026-10-03"))
        stack.enter_context(patch.object(offsets, "now_datetime", return_value="2026-10-03 10:00:00"))
        return stack

    def test_reversal_appends_pair_preserves_cutoff_and_is_idempotent(self):
        left, right = self.reversal_pair()
        with self.reversal_context(left, right):
            args = ("A", "a" * 32, "2025-09-01", "Error de selección", "b" * 32)
            offsets.reverse_compensation(*args)
            offsets.reverse_compensation(*args)
            for doc in (left, right):
                self.assertEqual(len(doc.compensations), 2)
                self.assertEqual(doc.compensations[0].amount_usd, 30)
                self.assertEqual(doc.compensations[1].amount_usd, -30)
                self.assertEqual(doc.compensations[1].reverses_operation_id, "a" * 32)
                self.assertEqual(doc.compensations[1].confirmed_by, "reviewer@example.test")
                self.assertEqual(offsets.balance(doc, "2025-08-31")["pending_usd"], 70)
                self.assertEqual(offsets.balance(doc, "2025-09-01")["pending_usd"], 100)
                doc.save.assert_called_once()
                self.assertNotIn("compensation_write_token", doc.flags)
                with self.assertRaises(ValueError):
                    offsets.guard_delete(doc)
            self.assertEqual(offsets.get_reversible_compensations("A")["rows"], [])
            with self.assertRaisesRegex(ValueError, "ya fue revertida"):
                offsets.reverse_compensation("A", "a" * 32, "2025-09-01", "Otra", "c" * 32)
            with self.assertRaisesRegex(ValueError, "otros datos"):
                offsets.reverse_compensation("A", "a" * 32, "2025-09-01", "Otro motivo", "b" * 32)

    def test_reversal_requires_valid_pair_date_reason_and_both_permissions(self):
        cases = [
            ("2025-07-31", "Corrección", None),
            ("2027-01-01", "Corrección", None),
            ("2025-09-01", "", None),
            ("2025-09-01", "Corrección", "amount"),
            ("2025-09-01", "Corrección", "missing"),
            ("2025-09-01", "Corrección", "permission"),
        ]
        for date, reason, problem in cases:
            with self.subTest(problem=problem, date=date, reason=reason):
                left, right = self.reversal_pair()
                if problem == "amount":
                    right.compensations[0].amount_usd = 29
                elif problem == "missing":
                    right.compensations = []
                elif problem == "permission":
                    right.check_permission.side_effect = PermissionError
                with self.reversal_context(left, right), self.assertRaises((ValueError, PermissionError)):
                    offsets.reverse_compensation("A", "a" * 32, date, reason, "b" * 32)
                left.save.assert_not_called()
                right.save.assert_not_called()

    def test_date_correction_after_reversal_preserves_history_and_all_cutoffs(self):
        left, right = self.reversal_pair()
        with self.reversal_context(left, right):
            offsets.reverse_compensation('A', 'a' * 32, '2025-09-01', 'Error de fecha', 'b' * 32)
            before = [[dict(row) for row in doc.compensations] for doc in (left, right)]
            args = ('A', 'a' * 32, '2025-06-01', 'Fecha correcta según asiento', 'c' * 32)
            offsets.correct_compensation_date(*args)
            offsets.correct_compensation_date(*args)
            for doc, history in zip((left, right), before):
                self.assertEqual([dict(row) for row in doc.compensations[:2]], history)
                self.assertEqual(len(doc.compensations), 5)
                self.assertEqual(offsets.balance(doc, '2025-05-31')['compensated_usd'], 0)
                for date in ('2025-06-01', '2025-07-01', '2025-08-01', '2025-08-31', '2025-09-01', '2026-10-03'):
                    self.assertEqual(offsets.balance(doc, date)['compensated_usd'], 30)
                self.assertEqual(doc.save.call_count, 2)
            self.assertEqual([r['operation_id'] for r in offsets.get_reversible_compensations('A')['rows']], ['c' * 32])
            self.assertEqual([r['operation_id'] for r in offsets.get_date_correction_candidates('A')['rows']], ['c' * 32])
            with self.assertRaisesRegex(ValueError, 'ya fue corregida'):
                offsets.correct_compensation_date('A', 'a' * 32, '2025-06-01', 'Otra', 'd' * 32)
            offsets.reverse_compensation('A', 'c' * 32, '2025-10-01', 'Reversión posterior', 'd' * 32)
            self.assertEqual(offsets.balance(left, '2025-09-30')['compensated_usd'], 30)
            self.assertEqual(offsets.balance(left, '2025-10-01')['compensated_usd'], 0)

    def test_correction_of_active_compensation_does_not_increase_current_usage(self):
        left, right = self.reversal_pair()
        with self.reversal_context(left, right):
            offsets.correct_compensation_date('A', 'a' * 32, '2025-06-01', 'Fecha correcta', 'c' * 32)
            for doc in (left, right):
                self.assertEqual(len(doc.compensations), 3)
                self.assertEqual(offsets.balance(doc)['compensated_usd'], 30)
                self.assertEqual(offsets.balance(doc, '2025-06-01')['compensated_usd'], 30)
            with self.assertRaisesRegex(ValueError, 'otros datos'):
                offsets.correct_compensation_date('A', 'a' * 32, '2025-07-01', 'Fecha correcta', 'c' * 32)

    def test_date_correction_rejects_invalid_pairs_dates_and_permissions_before_saving(self):
        for problem in ('future', 'before_origin', 'same_date', 'empty_reason', 'permission', 'missing', 'amount', 'reversal', 'receivable'):
            with self.subTest(problem=problem):
                left, right = self.reversal_pair()
                date, reason = '2025-06-01', 'Corrección'
                if problem == 'future': date = '2030-01-01'
                if problem == 'before_origin': date = '2025-03-01'
                if problem == 'same_date': date = '2025-08-01'
                if problem == 'empty_reason': reason = ''
                if problem == 'permission': right.check_permission.side_effect = PermissionError
                if problem == 'missing': right.compensations = []
                if problem == 'amount': right.compensations[0].amount_usd = 29
                if problem == 'receivable': right.receivable_origin = 'RECEIVABLE'
                if problem == 'reversal': left.compensations.append(frappe._dict(operation_id='b' * 32,
                    reverses_operation_id='a' * 32, compensation_date='2025-09-01', amount_usd=-30, counterpart='B'))
                with self.reversal_context(left, right), patch('credinomina_reconciliation.receivable_recovery.lock_recovery_origins'), self.assertRaises((ValueError, PermissionError)):
                    offsets.correct_compensation_date('A', 'a' * 32, date, reason, 'c' * 32)
                left.save.assert_not_called()
                right.save.assert_not_called()

    def test_correction_cannot_overconsume_at_an_intermediate_cutoff(self):
        left, right = self.reversal_pair()
        with self.reversal_context(left, right):
            offsets.reverse_compensation('A', 'a' * 32, '2025-09-01', 'Error', 'b' * 32)
            for doc, other in ((left, right), (right, left)):
                doc.compensations.extend([
                    frappe._dict(operation_id='e' * 32, counterpart=other.name, amount_usd=80, compensation_date='2025-05-01'),
                    frappe._dict(operation_id='f' * 32, counterpart=other.name, amount_usd=-80, compensation_date='2025-07-01', reverses_operation_id='e' * 32),
                ])
                doc.save.reset_mock()
            with self.assertRaisesRegex(ValueError, 'saldo compensado inválido'):
                offsets.correct_compensation_date('A', 'a' * 32, '2025-06-01', 'Corrección', 'c' * 32)
            left.save.assert_not_called()
            right.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
