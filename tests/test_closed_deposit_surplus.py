"""A documented surplus cannot change a deposit linked to a closed period."""

import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from credinomina_reconciliation import company_credit as surplus_module
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item import cn_complementary_item as controller


class ClosedDepositSurplusTests(unittest.TestCase):
    def _check(self, *, own_period=None, detail_period=None, target_period=None,
               allocated_period=None, status="Cerrado"):
        deposit = SimpleNamespace(
            detail_periods=[{"period": detail_period}] if detail_period else [],
            targets=[SimpleNamespace(
                period=target_period, historical_application=None,
                complementary_item=None,
            )] if target_period else [],
            allocation_detail=json.dumps(
                [{"periodo": allocated_period}] if allocated_period else []
            ),
        )
        surplus = SimpleNamespace(period=own_period, registered_deposit="REM-1")

        def get_value(doctype, name, field):
            if doctype == "CN Reconciliation Period" and field == "status":
                return status
            return None

        with (
            patch.object(surplus_module.frappe, "get_doc", return_value=deposit),
            patch.object(surplus_module.frappe, "db", SimpleNamespace(get_value=get_value)),
            patch.object(surplus_module.frappe, "throw", side_effect=ValueError) as rejected,
        ):
            if status == "Cerrado":
                with self.assertRaises(ValueError):
                    surplus_module.ensure_related_periods_open(surplus)
                self.assertEqual(rejected.call_count, 1)
            else:
                surplus_module.ensure_related_periods_open(surplus)
                rejected.assert_not_called()

    def test_explicit_closed_period_is_protected(self):
        self._check(own_period="PER-1")

    def test_deposit_target_closed_period_is_protected(self):
        self._check(target_period="PER-1")

    def test_deposit_detail_period_is_protected(self):
        self._check(detail_period="PER-1")

    def test_actual_allocation_closed_period_is_protected(self):
        self._check(allocated_period="PER-1")

    def test_open_related_period_can_receive_surplus(self):
        self._check(target_period="PER-1", status="Pendiente")

    def _guards(self, stack):
        paths = [
            'credinomina_reconciliation.receivable_recovery.guard_origin',
            'credinomina_reconciliation.client_credit.guard_cancel',
            'credinomina_reconciliation.complementary_exceptions.guard_item_delete',
            'credinomina_reconciliation.complementary_compensation.guard_delete',
        ]
        return [stack.enter_context(patch(path)) for path in paths] + [
            stack.enter_context(patch.object(controller, 'guard_tolerance_item'))]

    def test_canceled_credit_deletion_does_not_require_reopening_periods(self):
        for category in ('Saldo a favor del cliente', 'Saldo a favor de la empresa'):
            with self.subTest(category=category), ExitStack() as stack:
                guards = self._guards(stack)
                closed = stack.enter_context(patch.object(controller, 'ensure_related_periods_open', side_effect=ValueError('Cerrado')))
                doc = frappe._dict(docstatus=2, category=category)
                controller.CNComplementaryItem.on_trash(doc)
                closed.assert_not_called()
                for guard in guards:
                    guard.assert_called_once_with(doc)

    def test_non_canceled_deletion_still_requires_open_periods(self):
        for status in (0, 1):
            for category in ('Saldo a favor del cliente', 'Saldo a favor de la empresa'):
                with self.subTest(status=status, category=category), ExitStack() as stack:
                    self._guards(stack)
                    closed = stack.enter_context(patch.object(controller, 'ensure_related_periods_open', side_effect=ValueError('Cerrado')))
                    doc = frappe._dict(docstatus=status, category=category)
                    with self.assertRaisesRegex(ValueError, 'Cerrado'):
                        controller.CNComplementaryItem.on_trash(doc)
                    closed.assert_called_once_with(doc)

    def test_canceled_deletion_still_rejects_protected_evidence(self):
        for index in range(5):
            with self.subTest(guard=index), ExitStack() as stack:
                guards = self._guards(stack)
                guards[index].side_effect = ValueError('Conservar evidencia')
                with self.assertRaisesRegex(ValueError, 'Conservar evidencia'):
                    controller.CNComplementaryItem.on_trash(frappe._dict(docstatus=2, category='Saldo a favor del cliente'))

    def test_cancellation_still_checks_periods_even_with_target_docstatus_two(self):
        with ExitStack() as stack:
            self._guards(stack)
            stack.enter_context(patch('credinomina_reconciliation.complementary_distribution.guard_closed_distributions'))
            stack.enter_context(patch('credinomina_reconciliation.complementary_cancellation.prepare_cancellation', return_value={}))
            closed = stack.enter_context(patch.object(controller, 'ensure_related_periods_open', side_effect=ValueError('Cerrado')))
            doc = frappe._dict(docstatus=2, category='Saldo a favor del cliente', flags=frappe._dict())
            with self.assertRaisesRegex(ValueError, 'Cerrado'):
                controller.CNComplementaryItem.before_cancel(doc)
            closed.assert_called_once_with(doc)


if __name__ == "__main__":
    unittest.main()
