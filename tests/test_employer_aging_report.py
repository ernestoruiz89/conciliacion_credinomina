import unittest
from unittest.mock import patch

from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa import antiguedad_de_saldos_por_empresa as report


class EmployerAgingTests(unittest.TestCase):
    def execute(self, rows, operational=False, filters=None):
        with patch.object(report.detailed, "_", side_effect=lambda s: s), patch.object(report, "_", side_effect=lambda s: s):
            columns = report.detailed.get_columns() if operational else report.detailed.get_application_columns()
            with patch.object(report.detailed, "execute", return_value=(columns, rows, "Aviso original", None, [{"value": 42}])) as base:
                result = report.execute(filters)
                base.assert_called_once_with(filters)
        return result

    def test_aggregates_all_clients_periods_and_modes_without_reaging(self):
        rows = [dict(employer="E", client_name="Ana", period="P1", amount_usd=10.10, applied_usd=15.10,
                     paid_usd=5, adjustment_usd=0, days_1_30=10.10, reconciliation_mode="Historica"),
                dict(employer="E", client_name="Bea", period="P2", amount_usd=20.20, applied_usd=20.21,
                     paid_usd=0, adjustment_usd=-0.01, days_31_60=20.20, reconciliation_mode="Operativa"),
                dict(employer="F", amount_usd=4, without_date=4)]
        columns, data, message, _, summary = self.execute(rows, filters={"as_of_date": "2026-09-30"})
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["amount_usd"], 30.30)
        self.assertEqual(data[0]["applied_usd"], 35.31)
        self.assertEqual(data[0]["days_1_30"], 10.10)
        self.assertEqual(data[0]["days_31_60"], 20.20)
        self.assertEqual(data[1]["without_date"], 4)
        for field in ("client_name", "client_number", "national_id", "loan_number", "period", "due_date", "age_days"):
            self.assertNotIn(field, data[0])
            self.assertNotIn(field, [column["fieldname"] for column in columns])
        self.assertIn("Aviso original", message)
        self.assertEqual(summary, [{"value": 42}])

    def test_missing_conversion_remains_visible_not_zero(self):
        _, data, *_ = self.execute([{"employer": "E"}, {"employer": "F", "amount_usd": 20}, {"employer": "F"}, {}])
        by_company = {row["employer"]: row for row in data}
        self.assertIsNone(by_company["E"]["amount_usd"])
        self.assertEqual(by_company["E"]["missing_fx_count"], 1)
        self.assertEqual(by_company["F"]["amount_usd"], 20)
        self.assertIn("incompletos", by_company["F"]["observation"])
        self.assertIn("identificar", by_company[None]["observation"])

    def test_operational_types_not_combined(self):
        _, data, *_ = self.execute([
            dict(employer="E", balance_type="Cobranza no deducida (informativo)", amount_usd=10, provision_review_usd=10),
            dict(employer="E", balance_type="Detalle de empresa pendiente", amount_usd=20),
        ], operational=True)
        self.assertEqual(len(data), 2)
        self.assertNotIn("provision_review_usd", data[0])
        self.assertNotIn("missing_fx_count", data[0])

    def test_empty_operational_result(self):
        with patch.object(report.detailed, "_", side_effect=lambda s: s), patch.object(report, "_", side_effect=lambda s: s):
            with patch.object(report.detailed, "execute", return_value=(report.detailed.get_columns(), [])):
                self.assertEqual(report.execute({"reconciliation_mode": "Historica"})[1], [])
