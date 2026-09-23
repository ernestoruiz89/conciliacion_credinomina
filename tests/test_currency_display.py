"""Currency controls must not fall back to the site's default C$ label."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "credinomina_reconciliation" / "conciliacion_credinomina"


class CurrencyDisplayTests(unittest.TestCase):
    def test_every_currency_field_points_to_a_real_currency_field(self):
        for path in (ROOT / "doctype").glob("*/*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            fields = {field["fieldname"]: field for field in document.get("fields", ())}
            for field in fields.values():
                if field.get("fieldtype") != "Currency":
                    continue
                with self.subTest(doctype=document["name"], field=field["fieldname"]):
                    option = field.get("options")
                    self.assertIn(option, fields)
                    self.assertNotIn(option, {"USD", "NIO"})
                    if option == "usd_currency":
                        self.assertEqual(fields[option]["default"], "USD")
                    elif option == "nio_currency":
                        self.assertEqual(fields[option]["default"], "NIO")
                    else:
                        self.assertIn(option, {"currency", "deposit_currency"})

    def test_report_columns_reference_row_currency_fields(self):
        for report in (
            "antiguedad_de_saldos", "estado_de_cuenta_operativo",
            "resumen_de_conciliacion",
        ):
            path = ROOT / "report" / report / f"{report}.py"
            source = path.read_text(encoding="utf-8")
            with self.subTest(report=report):
                self.assertNotIn('"fieldtype": "Currency", "options": "USD"', source)
                self.assertNotIn('"fieldtype": "Currency", "options": "NIO"', source)
                self.assertTrue(
                    '"usd_currency": "USD"' in source
                    or 'row["usd_currency"] = "USD"' in source
                )


if __name__ == "__main__":
    unittest.main()
