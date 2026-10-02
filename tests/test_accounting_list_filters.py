import json
import unittest
from pathlib import Path


class AccountingListFiltersTests(unittest.TestCase):
    def test_company_and_bulk_date_are_standard_filters(self):
        path = Path(__file__).resolve().parents[1] / (
            "credinomina_reconciliation/conciliacion_credinomina/doctype/"
            "cn_accounting_import/cn_accounting_import.json"
        )
        meta = json.loads(path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in meta["fields"]}
        self.assertEqual(fields["employer"]["in_standard_filter"], 1)
        self.assertEqual(fields["employer"]["options"], "CN Employer")
        self.assertEqual(fields["bulk_event_date"]["in_standard_filter"], 1)
        self.assertEqual(fields["bulk_event_date"]["fieldtype"], "Date")
        self.assertEqual(fields["bulk_event_date"]["read_only"], 1)
