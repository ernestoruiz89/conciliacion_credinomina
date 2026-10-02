import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.employer_naming import employer_document_prefix
from credinomina_reconciliation import accounting_naming, period_naming


class EmployerShortNameTests(unittest.TestCase):
    def test_short_name_wins_and_blank_falls_back_to_code_in_both_names(self):
        for short, expected in [(" INDENICSA ", "INDENICSA"), ("", "5111"),
                                (None, "5111"), ("   ", "5111"), ("IND.YYYY", "IND.YYYY")]:
            with self.subTest(short=short), patch.object(frappe, "db", Mock(
                get_value=Mock(side_effect=lambda dt, name, field: {"short_name": short, "employer_code": " 5111 "}[field]),
                exists=Mock(return_value=False),
            )), patch.object(period_naming, "validate_name"), patch.object(accounting_naming, "validate_name"), \
                 patch.object(period_naming, "getseries", return_value="01"), \
                 patch.object(accounting_naming, "getseries", return_value="001"):
                self.assertEqual(employer_document_prefix("Company"), expected)
                self.assertEqual(period_naming.new_period_name("Company", "2026-09-30"), f"{expected}-9-2026-01")
                prefix = accounting_naming.accounting_prefix("Company", date(2026, 9, 30))
                self.assertEqual(accounting_naming.new_accounting_name(prefix), f"CONTA-{expected}-9-2026-001")

    def test_optional_field_keeps_company_document_name_and_code(self):
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_employer/cn_employer.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in metadata["fields"]}
        self.assertFalse(fields["short_name"].get("reqd"))
        self.assertEqual(fields["short_name"]["label"], "Nombre corto")
        self.assertEqual(metadata["autoname"], "field:employer_name")
        self.assertTrue(fields["employer_code"]["unique"])
        self.assertTrue(fields["employer_code"]["reqd"])

    def test_blank_short_name_and_code_are_rejected(self):
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=" "))), \
             patch.object(frappe, "throw", side_effect=ValueError), \
             patch.object(period_naming, "_", side_effect=lambda text: text), \
             patch.object(accounting_naming, "_", side_effect=lambda text: text):
            with self.assertRaises(ValueError):
                period_naming.period_name_prefix("Company", "2026-09-01")
            with self.assertRaises(ValueError):
                accounting_naming.accounting_prefix("Company", date(2026, 9, 1))
