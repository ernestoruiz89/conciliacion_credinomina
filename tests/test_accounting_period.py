import unittest
from datetime import date
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import accounting_period as api


class AccountingPeriodTests(unittest.TestCase):
    def source(self, *dates):
        return frappe._dict(employer="A", rows=[frappe._dict(event_type="Aplicacion", event_date=d) for d in dates])

    def test_defaults_use_application_dates_only(self):
        source = self.source("2025-04-30", "2025-04-15")
        source.rows.append(frappe._dict(event_type="Deposito", event_date="2025-01-01"))
        with patch.object(api, "_source", return_value=(source, frappe._dict(payroll_frequency="Quincenal"))):
            result = api.get_period_defaults("I")
        self.assertEqual(result["payroll_month"], "2025-04-01")
        self.assertEqual(result["historical_scope"], "Rango de fechas")
        self.assertEqual(result["historical_start_date"], "2025-04-15")
        self.assertEqual(result["historical_end_date"], "2025-04-30")

    def test_same_day_and_empty_defaults(self):
        for dates, scope in [(("2025-04-15", "2025-04-15"), "Fecha exacta"), ((), "Mensual")]:
            with patch.object(api, "_source", return_value=(self.source(*dates), frappe._dict())):
                result = api.get_period_defaults("I")
            self.assertEqual(result["historical_scope"], scope)
            self.assertEqual(result["historical_application_date"], dates[0] if dates else None)

    def test_create_empty_draft_ignores_injected_fields(self):
        doc = Mock(name="period")
        doc.name, doc.status = "P", "Borrador"
        doc.insert.return_value = doc
        with patch.object(api, "_source", return_value=(self.source(), frappe._dict())), \
             patch.object(api.frappe, "get_doc", return_value=doc) as get_doc:
            result = api.create_draft_period("I", {
                "payroll_month": "2025-04-23", "historical_scope": "Fecha exacta",
                "historical_application_date": "2025-05-10", "employer": "PRIVATE",
                "status": "Conciliado", "docstatus": 1, "collection_rows": [{"applied_usd": 100}],
                "historical_start_date": "2025-01-01",
            })
        fields = get_doc.call_args.args[0]
        self.assertEqual(fields["employer"], "A")
        self.assertEqual(fields["payroll_month"], date(2025, 4, 1))
        self.assertEqual(fields["reconciliation_mode"], "Historica")
        self.assertEqual(fields["status"], "Borrador")
        self.assertEqual(fields["docstatus"], 0)
        self.assertNotIn("collection_rows", fields)
        self.assertNotIn("historical_start_date", fields)
        doc.insert.assert_called_once_with()
        self.assertEqual(result, {"name": "P", "status": "Borrador"})

    def test_operative_uses_selected_cycle_not_historical_dates(self):
        doc = Mock()
        doc.insert.return_value = doc
        with patch.object(api, "_source", return_value=(self.source(), frappe._dict(payroll_frequency="Quincenal"))), \
             patch.object(api.frappe, "get_doc", return_value=doc) as get_doc:
            api.create_draft_period("I", {"payroll_month": "2026-09-01", "collection_cycle": "Segunda quincena",
                                           "historical_application_date": "2025-04-01"})
        fields = get_doc.call_args.args[0]
        self.assertEqual(fields["reconciliation_mode"], "Operativa")
        self.assertEqual(fields["collection_cycle"], "Segunda quincena")
        self.assertNotIn("historical_application_date", fields)

    def test_source_read_and_create_permissions_are_required(self):
        source, employer = Mock(employer="A"), Mock()
        with patch.object(api.frappe, "get_doc", side_effect=[source, employer]), \
             patch.object(api.frappe, "has_permission") as permission:
            api._source("I")
        source.check_permission.assert_called_once_with("read")
        employer.check_permission.assert_called_once_with("read")
        permission.assert_called_once_with("CN Reconciliation Period", "create", throw=True)
        source.check_permission.side_effect = PermissionError
        with patch.object(api.frappe, "get_doc", return_value=source) as get_doc:
            with self.assertRaises(PermissionError):
                api.get_period_defaults("I")
        get_doc.assert_called_once_with("CN Accounting Import", "I")

    def test_missing_month_is_rejected(self):
        with patch.object(api, "_source", return_value=(self.source(), frappe._dict())), \
             patch.object(api.frappe, "throw", side_effect=ValueError), \
             patch.object(api.frappe, "get_doc") as get_doc:
            with self.assertRaises(ValueError):
                api.create_draft_period("I", {})
        get_doc.assert_not_called()
