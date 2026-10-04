import io
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.control_export import build_control_workbook
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
    _build_work_items,
)


class DateDisplayTests(unittest.TestCase):
    def test_dates_use_system_settings_not_locale_or_fixed_order(self):
        for pattern, expected in (
            ("dd-mm-yyyy", "23-04-2025"), ("dd/mm/yyyy", "23/04/2025"),
            ("mm/dd/yyyy", "04/23/2025"), ("yyyy-mm-dd", "2025-04-23"),
            (None, "2025-04-23"),
        ):
            with self.subTest(pattern=pattern), patch.object(frappe, "db", Mock()) as db:
                db.get_single_value.return_value = pattern
                for value in ("2025-04-23", date(2025, 4, 23),
                              datetime(2025, 4, 23, 23, 59), "2025-04-23T00:00:00Z"):
                    self.assertEqual(display_date(value), expected)
                db.get_single_value.assert_called_with("System Settings", "date_format")

    def test_blank_date_does_not_become_today(self):
        self.assertEqual(display_date(None), "")
        self.assertEqual(display_date(""), "")

    def test_historical_queue_labels_are_formatted_without_changing_data(self):
        period = {"name": "PER-2025-04", "month": "2025-04", "employer": "EMP",
                  "reconciliation_mode": "Historica", "historical_pending_usd": 22.52,
                  "historical_application_date": "2025-04-23",
                  "historical_start_date": "2025-04-01", "historical_end_date": "2025-04-30"}
        with patch.object(frappe, "db", Mock()) as db:
            db.get_single_value.return_value = "dd/mm/yyyy"
            for scope, label in (("Fecha exacta", "2025-04 · 23/04/2025"),
                                 ("Rango de fechas", "2025-04 · 01/04/2025–30/04/2025")):
                period["historical_scope"] = scope
                before = dict(period)
                items = _build_work_items([period], [], [], [], [], [], {})
                self.assertEqual(items[0]["period_label"], label)
                self.assertEqual(items[0]["target_name"], "PER-2025-04")
                self.assertEqual(period, before)

    def test_workbook_retains_real_dates_with_configurable_display(self):
        data = {"year": 2025, "totals": {}, "periods": [],
                "unassigned_historical_applications": [
                    {"event_date": "2025-04-23", "amount": 22.52},
                ]}
        for pattern in ("dd-mm-yyyy", "mm/dd/yyyy", "yyyy-mm-dd"):
            content = build_control_workbook(
                data, exceptions=[], actions=[], employer_label="Empresa",
                generated_at=datetime(2025, 4, 23, 14, 30), date_format=pattern,
            )
            book = load_workbook(io.BytesIO(content))
            # Export scope carries the generation timestamp; dates in tables
            # remain native Excel dates rather than preformatted text.
            rendered = datetime(2025, 4, 23, 14, 30).strftime(pattern.replace("yyyy", "%Y").replace("mm", "%m").replace("dd", "%d") + " %H:%M")
            self.assertIn(rendered, book["Resumen mensual"]["A2"].value)
            dates = [cell for row in book["Aplicaciones sin período"] for cell in row
                     if isinstance(cell.value, datetime)]
            self.assertEqual(len(dates), 1)
            self.assertEqual(dates[0].number_format, pattern)
            self.assertEqual(dates[0].value.date(), date(2025, 4, 23))
