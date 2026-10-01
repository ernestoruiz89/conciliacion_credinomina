import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import application_adjustments as adjustments
from credinomina_reconciliation.reconciliation import net_application_amount, converted_amount
from credinomina_reconciliation.application_aging import application_balances


class ApplicationAdjustmentTests(unittest.TestCase):
    def test_partial_full_cent_precision_and_original_unchanged(self):
        for original, adjusted, net in [(100, 30, 70), (100, 100, 0), (46.53, 46.52, 0.01), (100, 0, 100)]:
            row = dict(event_type="Aplicacion", currency="USD", amount=original, application_adjustment_usd=adjusted)
            self.assertEqual(net_application_amount(row), net)
            self.assertEqual(converted_amount(row, "USD"), net)
            self.assertEqual(row["amount"], original)

    def test_nio_equivalent_reduced_but_cash_is_not(self):
        row = dict(event_type="Aplicacion", currency="USD", amount=100, manual_fx_rate=36.6243, application_adjustment_usd=25)
        self.assertEqual(converted_amount(row, "NIO"), 2746.82)
        row["event_type"] = "Deposito"
        self.assertEqual(converted_amount(row, "USD"), 100)

    def test_read_only_aging_uses_net_in_both_modes(self):
        imports = {"I": {"name": "I", "employer": "E"}}
        for mode in ["Historica", "Operativa"]:
            source = dict(event_type="Aplicacion", parent="I", effective=1, currency="USD", amount=100,
                          application_adjustment_usd=30, processing_route=mode, event_date="2025-04-15")
            result = application_balances([source], imports, {}, {}, {"E": {"grace_days": 10}}, "2026-10-01")
            self.assertEqual(result[0]["amount_usd"], 70)
            source["application_adjustment_usd"] = 100
            self.assertEqual(application_balances([source], imports, {}, {}, {"E": {}}, "2026-10-01"), [])

    def test_refresh_overwrites_client_supplied_totals(self):
        row = frappe._dict(name="A", event_type="Aplicacion", amount=100, application_adjustment_usd=999)
        with patch.object(frappe, "db", Mock(sql=Mock(return_value=[frappe._dict(related_application="A", application_adjustment_usd=30)]))):
            adjustments.refresh_rows([row])
        self.assertEqual(row.application_adjustment_usd, 30)
        self.assertEqual(row.net_applied_usd, 70)
        self.assertEqual(row.application_adjustment_status, "Aplicación ajustada parcialmente")

    def test_confirmation_checks_submit_permission(self):
        document = Mock()
        document.check_permission.side_effect = PermissionError
        with patch.object(frappe, "get_doc", return_value=document), self.assertRaises(PermissionError):
            adjustments.confirm_adjustment("C")
        document.check_permission.assert_called_once_with("submit")
        document.submit.assert_not_called()

    def test_deposit_guard_applies_to_saved_operational_links(self):
        row = frappe._dict(name="A", application_allocation_detail="[]")
        def get_value(dt, name, fields, **kwargs):
            if dt == "CN Collection Row":
                return "P" if fields == "parent" else frappe._dict(parent="P", row_key="K", remitted_usd=10)
            return "Pendiente"
        with patch.object(frappe, "db", Mock(get_value=Mock(side_effect=get_value))), \
             patch.object(frappe, "get_all", return_value=[]), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                adjustments.assert_adjustable(row, "C", saved_collections=["COL"])

    def test_selected_target_blocks_confirmation_even_without_paid_cash(self):
        row = frappe._dict(name="A", application_allocation_detail="[]")
        target = frappe._dict(historical_application="A")
        with patch.object(frappe, "get_all", return_value=[target]), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                adjustments.assert_adjustable(row, "C")

    def test_source_identity_cannot_be_changed_or_deleted(self):
        old = frappe._dict(name="A", event_type="Aplicacion", amount=100)
        previous = frappe._dict(employer="E", rows=[old])
        for rows in [[], [frappe._dict(old, amount=90)]]:
            doc = frappe._dict(employer="E", rows=rows, get_doc_before_save=lambda: previous)
            with patch.object(frappe, "get_all", return_value=["A"]), \
                 patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
                with self.assertRaises(ValueError):
                    adjustments.guard_source_changes(doc)


if __name__ == "__main__":
    unittest.main()
