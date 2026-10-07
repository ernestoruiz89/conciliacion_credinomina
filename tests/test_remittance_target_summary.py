import json
import unittest
from pathlib import Path

from credinomina_reconciliation.remittance_target_summary import describe_targets


class RemittanceTargetSummaryTests(unittest.TestCase):
    def test_historical_target_displays_business_identity_not_only_internal_id(self):
        targets = '[{"claim_id":"H:sp6qrrc95k","amount_usd":22.52}]'
        text = describe_targets(targets, {"H:sp6qrrc95k": {
            "client_name": "JULIO CESAR GARCIA CENTENO", "client_number": "3538",
            "loan_number": "108331-1", "period": "CN-PER-2026-00004", "payroll_month": "2025-04",
            "accounting_entry": "001011446", "receipt": "1579", "event_date": "2025-04-23",
        }}, date_formatter=lambda value: "23/04/2025")
        for expected in ("Aplicación del core", "US$ 22.52", "JULIO CESAR GARCIA CENTENO",
                         "Nro. Cliente: 3538", "Crédito: 108331-1", "2025-04", "001011446", "1579"):
            self.assertIn(expected, text)
        self.assertNotIn("sp6qrrc95k", text)
        self.assertIn("Fecha: 23/04/2025", text)
        self.assertNotIn("Conciliado", text)  # A proposed destination isn't necessarily applied.

    def test_collection_and_complementary_targets_remain_separate(self):
        text = describe_targets([{"claim_id": "C:row", "amount_usd": 90},
                                 {"claim_id": "X:fee", "amount_usd": 10}], {
            "C:row": {"loan_number": "100-1", "installment_number": "3"},
            "X:fee": {"description": "Cobranza administrativa", "accounting_entry": "AS-2"},
        })
        self.assertIn("Cuota de cobranza — US$ 90.00", text)
        self.assertIn("Cuota: 3", text)
        self.assertIn("Partida complementaria — US$ 10.00", text)
        self.assertIn("Cobranza administrativa", text)

    def test_missing_record_and_invalid_json_are_not_reported_as_reconciled(self):
        self.assertIn("Registro no disponible", describe_targets([{"claim_id": "H:missing", "amount_usd": 1}], {}))
        self.assertIn("H:missing", describe_targets([{"claim_id": "H:missing", "amount_usd": 1}], {}))
        self.assertIn("Sin destinos identificados", describe_targets("[]", {}))
        for invalid in ("bad JSON", "{}", "[null]"):
            self.assertIn("No se pudo interpretar", describe_targets(invalid, {}))

    def test_summary_is_read_only_and_raw_ids_are_preserved_in_collapsed_section(self):
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_detail/cn_remittance_detail.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doc["fields"]}
        self.assertTrue(fields["matched_targets_summary"]["read_only"])
        self.assertTrue(fields["matched_targets_summary"]["allow_on_submit"])
        self.assertTrue(fields["targets_technical_section"]["collapsible"])
        self.assertEqual(fields["matched_targets"]["options"], "JSON")
        self.assertEqual(set(doc["field_order"]), set(fields))
