import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_exception import cn_reconciliation_exception as controller


class ExceptionNamingTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(controller, "now_datetime", return_value=datetime(2026, 9, 30))
        clock.start()
        self.addCleanup(clock.stop)

    def test_script_naming_without_extra_required_fields(self):
        schema = json.loads(Path(controller.__file__).with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(schema["naming_rule"], "By script")
        self.assertFalse(schema["autoname"])
        fields = {field["fieldname"]: field for field in schema["fields"]}
        self.assertNotIn("naming_series", fields)
        self.assertFalse(fields["period"].get("reqd"))

    def test_uses_period_month_and_year_not_creation_date(self):
        for date, prefix in (("2025-04-01", "CN-EXC-4-2025-"),
                             ("2025-05-15", "CN-EXC-5-2025-"),
                             ("2026-04-01", "CN-EXC-4-2026-")):
            with self.subTest(date=date), patch.object(controller.frappe, "db", Mock()) as db, \
                    patch.object(controller, "getseries", return_value="042") as counter:
                db.get_value.return_value = date
                db.exists.return_value = False
                doc = SimpleNamespace(period="P1")
                controller.CNReconciliationException.autoname(doc)
                self.assertEqual(doc.name, prefix + "042")
                counter.assert_called_once_with(prefix, 3)
                db.get_value.assert_called_once_with("CN Reconciliation Period", "P1", "payroll_month")

    def test_without_period_uses_separate_four_digit_sequence(self):
        with patch.object(controller.frappe, "db", Mock()) as db, \
                patch.object(controller, "getseries", return_value="0001") as counter:
            doc = SimpleNamespace(period=None)
            db.exists.return_value = False
            controller.CNReconciliationException.autoname(doc)
            self.assertEqual(doc.name, "CN-EXC-2026-0001")
            counter.assert_called_once_with("CN-EXC-2026-", 4)
            db.get_value.assert_not_called()

    def test_without_period_sequence_is_scoped_to_creation_year(self):
        with patch.object(controller.frappe, "db", Mock()) as db, \
                patch.object(controller, "now_datetime", return_value=datetime(2027, 1, 1)), \
                patch.object(controller, "getseries", return_value="0001") as counter:
            db.exists.return_value = False
            self.assertEqual(controller.new_exception_name(None), "CN-EXC-2027-0001")
            counter.assert_called_once_with("CN-EXC-2027-", 4)

    def test_skips_existing_names_without_overwriting_them(self):
        with patch.object(controller.frappe, "db", Mock()) as db, \
                patch.object(controller, "getseries", side_effect=["0001", "0002"]):
            db.exists.side_effect = [True, False]
            self.assertEqual(controller.new_exception_name(None), "CN-EXC-2026-0002")

    def test_invalid_period_does_not_use_current_date_or_no_period_sequence(self):
        with patch.object(controller.frappe, "db", Mock()) as db, \
                patch.object(controller.frappe, "throw", side_effect=ValueError), \
                patch.object(controller, "_", side_effect=lambda message: message), \
                patch.object(controller, "getseries") as counter:
            db.get_value.return_value = None
            with self.assertRaises(ValueError):
                controller.CNReconciliationException.autoname(SimpleNamespace(period="P1"))
            counter.assert_not_called()
