import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as report


class ApplicationAgingReportTests(unittest.TestCase):
    def run_report(self, filters=None):
        calls = []
        periods = [frappe._dict(name="H", employer="E", payroll_month="2025-04-01",
                               reconciliation_mode="Historica")]
        imports = [frappe._dict(name="I", employer="E")]

        def get_list(doctype, **kwargs):
            calls.append(("list", doctype, kwargs))
            return periods if doctype == "CN Reconciliation Period" else imports

        def get_all(doctype, **kwargs):
            calls.append(("all", doctype, kwargs))
            if doctype == "CN Source Row":
                self.assertEqual(kwargs["filters"]["parent"], ["in", ["I"]])
                self.assertEqual(kwargs["filters"]["parenttype"], "CN Source Import")
                return [frappe._dict(name="A", parent="I", event_type="Aplicacion", effective=1,
                                    match_status="Conciliado", historical_period="H", amount=22.52,
                                    currency="USD", event_date="2025-04-30", client_number="123")]
            if doctype == "CN Collection Row":
                self.assertEqual(kwargs["filters"]["parent"], ["in", ["H"]])
                return []
            if doctype == "CN Employer":
                return [frappe._dict(name="E", grace_days=10)]
            raise AssertionError(doctype)

        with patch.object(report.frappe, "get_list", side_effect=get_list), \
             patch.object(report.frappe, "get_all", side_effect=get_all), \
             patch.object(report, "_", side_effect=lambda text: text):
            result = report.execute(dict({"as_of_date": "2025-05-11"}, **(filters or {})))
        self.assertTrue(any(kind == "list" and dt == "CN Source Import" for kind, dt, _ in calls))
        self.assertTrue(any(kind == "list" and dt == "CN Reconciliation Period" for kind, dt, _ in calls))
        return result

    def test_default_includes_historical_and_ages_from_company_deadline(self):
        columns, data, message, _, summary = self.run_report()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["amount_usd"], 22.52)
        self.assertEqual(data[0]["age_days"], 1)
        self.assertEqual(data[0]["days_1_30"], 22.52)
        self.assertEqual(summary[0]["value"], 22.52)
        self.assertIn("grace_days", message)
        self.assertIn("paid_usd", {column["fieldname"] for column in columns})

    def test_filters_do_not_hide_history_by_default_and_can_select_modes(self):
        for filters, expected in [({"reconciliation_mode": "Historica"}, 1),
                                  ({"reconciliation_mode": "Operativa"}, 0),
                                  ({"employer": "Other"}, 0),
                                  ({"client_number": "123"}, 1),
                                  ({"loan_number": "Missing"}, 0),
                                  ({"from_month": "2025-04-01", "to_month": "2025-04-30"}, 1),
                                  ({"from_month": "2025-05-01"}, 0)]:
            with self.subTest(filters=filters):
                self.assertEqual(len(self.run_report(filters)[1]), expected)


if __name__ == "__main__":
    unittest.main()
