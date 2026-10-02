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

    def test_closed_guard_applies_to_saved_operational_links(self):
        row = frappe._dict(name="A", application_allocation_detail="[]")
        def get_value(dt, name, fields, **kwargs):
            if dt == "CN Collection Row":
                return "P" if fields == "parent" else frappe._dict(parent="P", row_key="K", remitted_usd=10)
            return "Cerrado"
        with patch.object(frappe, "db", Mock(get_value=Mock(side_effect=get_value))), \
             patch.object(frappe, "get_all", return_value=[]), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                adjustments.assert_adjustable(row, "C", saved_collections=["COL"])

    def test_adjustment_item_itself_cannot_be_a_cash_target(self):
        row = frappe._dict(name="A", application_allocation_detail="[]")
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=True))), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                adjustments.assert_adjustable(row, "C")

    def test_cash_and_manual_reservations_are_counted_once_per_deposit(self):
        row = frappe._dict(name="A", amount=137.33, historical_remitted_usd=111.32)
        deposit = frappe._dict(name="D", allocation_detail='[{"tipo":"Aplicacion historica","aplicacion_id":"A","importe_usd":111.32}]')
        targets = [frappe._dict(parent="D", historical_application="A", amount_usd=111.32),
                   frappe._dict(parent="DRAFT", historical_application="A", amount_usd=10)]
        with patch.object(frappe, "get_all", side_effect=lambda dt, **kw: targets if dt == "CN Remittance Target" else [deposit]):
            coverage = adjustments.cash_coverage(row)
        self.assertEqual(coverage["adjustable_usd"], 16.01)
        self.assertEqual(coverage["protected_usd"], 121.32)
        self.assertEqual(list(coverage["snapshots"]), ["D"])

    def test_operative_shared_cash_limits_reduction_to_collective_pending(self):
        row = frappe._dict(name="A", amount=100, application_allocation_detail='[{"collection_row_id":"C","amount_usd":100}]')
        collection = frappe._dict(name="C", parent="P", row_key="K", applied_usd=200)
        deposit = frappe._dict(name="D", allocation_detail='[{"tipo":"Cobranza","periodo":"P","fila_id":"K","importe_usd":120}]')
        with patch.object(frappe, "get_all", side_effect=lambda dt, **kw: [] if dt == "CN Remittance Target" else [deposit]), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value=collection))):
            self.assertEqual(adjustments.cash_coverage(row)["adjustable_usd"], 80)

    def test_mixed_status_requires_positive_cash_and_zero_pending(self):
        row = frappe._dict(name="A", event_type="Aplicacion", effective=1, match_status="Conciliado",
            amount=137.33, application_adjustment_usd=26.01, historical_period="P", historical_remitted_usd=111.32,
            historical_balance_usd=0)
        adjustments.mark_mixed_settlements([row], [])
        self.assertEqual(row.deposit_match_status, adjustments.MIXED_STATUS)
        row.historical_balance_usd, row.deposit_match_status = 1, "Depósito parcial"
        adjustments.mark_mixed_settlements([row], [])
        self.assertEqual(row.deposit_match_status, "Depósito parcial")

    def test_guard_detects_reallocation_not_just_same_deposit_total(self):
        signature = adjustments.allocation_signature([{"tipo":"Aplicacion historica","aplicacion_id":"A","importe_usd":100}])
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value='[{"tipo":"Aplicacion historica","aplicacion_id":"B","importe_usd":100}]'))), \
             patch.object(frappe, "throw", side_effect=ValueError), patch.object(adjustments, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                adjustments.assert_cash_preserved({"D":signature})

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
