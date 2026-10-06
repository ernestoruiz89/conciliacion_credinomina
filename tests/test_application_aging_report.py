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
            if doctype == "CN Employer":
                return [frappe._dict(name="E", grace_days=10)]
            return periods if doctype == "CN Reconciliation Period" else imports

        def get_all(doctype, **kwargs):
            calls.append(("all", doctype, kwargs))
            if doctype == "CN Source Row":
                self.assertEqual(kwargs["filters"]["parent"], ["in", ["I"]])
                self.assertEqual(kwargs["filters"]["parenttype"], "CN Accounting Import")
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
        self.assertTrue(any(kind == "list" and dt == "CN Accounting Import" for kind, dt, _ in calls))
        self.assertTrue(any(kind == "list" and dt == "CN Reconciliation Period" for kind, dt, _ in calls))
        return result

    def test_default_includes_historical_and_ages_from_company_deadline(self):
        columns, data, message, _, summary = self.run_report()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["amount_usd"], 22.52)
        self.assertEqual(data[0]["age_days"], 1)
        self.assertEqual(data[0]["days_1_15"], 22.52)
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


class ReceivableAgingFilterTests(unittest.TestCase):
    def report(self, filters=None):
        app = dict(employer='E', balance_type=report.APPLICATION_BALANCE, client_number='123',
                   amount_usd=100, application_date='2025-04-30', payroll_month='2025-04-01',
                   not_due=0, without_date=0)
        adjustment = dict(name='COMP', employer='E', client_number='456', national_id='ID-456',
                          receivable_usd=10, posting_date='2025-05-01', period='P',
                          credit_commitment_date='2025-05-10')
        periods = {'P': dict(name='P', payroll_month='2025-04-01', reconciliation_mode='Historica')}
        with patch.object(report, 'load_application_context', return_value=([], {}, periods, {}, {})), \
             patch.object(report, 'application_balances', return_value=[app]) as apps, \
             patch('credinomina_reconciliation.deposit_adjustment_receivables.load_receivables', return_value=[adjustment]) as adjustments:
            result = report.execute({'as_of_date': '2025-06-01', **(filters or {})})
        return result, apps, adjustments

    def test_total_applications_and_adjustments_are_separate_filters(self):
        for selected, expected in [(None, [100, 10]), ('CxC total', [100, 10]),
                                   (report.APPLICATION_BALANCE, [100]), ('CxC por ajustes', [10])]:
            with self.subTest(selected=selected):
                result, _, _ = self.report({'balance_type': selected})
                self.assertEqual(sorted(row['amount_usd'] for row in result[1]), sorted(expected))
                self.assertEqual(result[4][0]['value'], sum(expected))

    def test_type_filter_does_not_compute_unselected_financial_population(self):
        _, _, adjustments = self.report({'balance_type': report.APPLICATION_BALANCE})
        adjustments.assert_not_called()
        _, apps, _ = self.report({'balance_type': 'CxC por ajustes'})
        apps.assert_not_called()

    def test_adjustment_identity_and_month_filters_preserve_selected_scope(self):
        for selected, expected in [({'client_number': '456'}, 1), ({'national_id': 'ID-456'}, 1),
                                   ({'national_id': 'OTHER'}, 0), ({'reconciliation_mode': 'Operativa'}, 0),
                                   ({'from_month': '2025-05-01', 'to_month': '2025-05-31'}, 1),
                                   ({'from_month': '2025-04-01', 'to_month': '2025-04-30'}, 0)]:
            with self.subTest(filters=selected):
                result, _, _ = self.report({'balance_type': 'CxC por ajustes', **selected})
                self.assertEqual(len(result[1]), expected)
        columns = self.report()[0][0]
        self.assertIn('posting_date', {column['fieldname'] for column in columns})


if __name__ == "__main__":
    unittest.main()
