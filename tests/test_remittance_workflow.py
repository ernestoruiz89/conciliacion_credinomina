"""Core allocation rules for registered deposits (independent of Frappe)."""

import json
import unittest
from pathlib import Path

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.reconciliation import converted_amount


class RegisteredRemittanceWorkflowTest(unittest.TestCase):
    def test_registered_deposit_has_editable_multi_target_table(self):
        root = Path(__file__).resolve().parents[1]
        doctype = root / "credinomina_reconciliation" / "conciliacion_credinomina" / "doctype"
        header = json.loads(
            (doctype / "cn_remittance_allocation" / "cn_remittance_allocation.json")
            .read_text(encoding="utf-8")
        )
        child = json.loads(
            (doctype / "cn_remittance_target" / "cn_remittance_target.json")
            .read_text(encoding="utf-8")
        )
        fields = {field["fieldname"]: field for field in header["fields"]}
        self.assertEqual("CN Remittance Target", fields["targets"]["options"])
        self.assertEqual(1, fields["targets"]["allow_on_submit"])
        self.assertEqual(1, child["istable"])
        self.assertIn("support_file", fields)
        self.assertIn("deposit_date", fields)
        self.assertEqual(1, fields["deposit_date"]["reqd"])
        self.assertFalse({
            "target_section", "period", "row_key", "historical_application",
            "complementary_item",
        } & fields.keys())
        child_fields = {field["fieldname"] for field in child["fields"]}
        self.assertTrue({
            "period", "row_key", "historical_application", "complementary_item",
        } <= child_fields)
        self.assertFalse(fields["support_file"].get("reqd"))
        self.assertEqual(1, fields["support_file"]["allow_on_submit"])
        self.assertEqual(1, fields["detail_file"]["allow_on_submit"])
        self.assertEqual(1, fields["detail_rows"]["allow_on_submit"])
        self.assertEqual("Datetime", fields["detail_imported_on"]["fieldtype"])

    def test_later_month_deposit_can_settle_prior_month_application(self):
        result = allocate_cash(
            [{"id": "deposit-may", "reference": "REM-1", "amount_usd": 90,
              "event_date": "2025-05-27", "group": "Empresa A"}],
            [{"id": "H:application-april", "amount_usd": 90,
              "references": ["REM-1"], "period": "2025-04",
              "group": "Empresa A"}],
        )
        self.assertEqual(90, result["allocations"][0]["amount_usd"])
        self.assertEqual(0, result["deposit_remaining"]["deposit-may"])

    def test_one_deposit_can_fund_multiple_applications_and_remain_partial(self):
        result = allocate_cash(
            [{"id": "deposit", "reference": "R", "amount_usd": 100}],
            [{"id": "H:a", "amount_usd": 40, "references": ["R"]},
             {"id": "H:b", "amount_usd": 70, "references": ["R"]}],
            [{"id": "line-1", "deposit_id": "deposit", "claim_id": "H:a", "amount_usd": 40},
             {"id": "line-2", "deposit_id": "deposit", "claim_id": "H:b", "amount_usd": 60}],
        )
        self.assertEqual("Aplicada", result["instruction_results"]["line-1"])
        self.assertEqual("Aplicada", result["instruction_results"]["line-2"])
        self.assertEqual(10, result["claim_remaining"]["H:b"])

    def test_nio_deposit_requires_documented_rate_for_usd_equivalent(self):
        deposit = {"currency": "NIO", "amount": 3653,
                   "manual_fx_rate": 36.53, "manual_fx_evidence": "Comprobante bancario"}
        self.assertAlmostEqual(100, converted_amount(deposit, "USD"))
        deposit["manual_fx_evidence"] = ""
        self.assertIsNone(converted_amount(deposit, "USD"))


if __name__ == "__main__":
    unittest.main()
