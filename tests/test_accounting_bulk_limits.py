"""Boundary checks at the real bulk preview gate, before portfolio processing."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from credinomina_reconciliation import bulk_accounting_import as bulk


class PassedLimitCheck(Exception):
    pass


class BulkMovementLimitTests(unittest.TestCase):
    def test_limit_accepts_one_hundred_thousand_and_rejects_more(self):
        def reject(message):
            raise ValueError(message)

        for count in (20_001, 99_999, 100_000, 100_001):
            with self.subTest(count=count), ExitStack() as stack:
                stack.enter_context(patch.object(bulk, "_permissions"))
                stack.enter_context(patch.object(bulk, "_required_portfolio_snapshot", return_value="CUT"))
                stack.enter_context(patch.object(bulk, "_file", return_value=(SimpleNamespace(file_name="test.csv"), b"test")))
                stack.enter_context(patch.object(bulk.frappe, "get_list", return_value=[]))
                stack.enter_context(patch.object(bulk.frappe, "has_permission", return_value=True))
                stack.enter_context(patch.object(bulk.frappe, "throw", side_effect=reject))
                stack.enter_context(patch.object(bulk, "_", side_effect=lambda text: text))
                stack.enter_context(patch.object(bulk, "attach_employer_aliases"))
                stack.enter_context(patch.object(bulk, "parse_accounting_movements", return_value=[]))
                stack.enter_context(patch.object(bulk, "apply_accounting_currency_override", return_value=[{"accounting_source_key": "key", "event_type": "Aplicacion"}] * count))
                stack.enter_context(patch.object(bulk, "identify_lines"))
                enrich = stack.enter_context(patch.object(bulk, "enrich_accounting_records", side_effect=PassedLimitCheck))
                options = {"source_file": "/private/files/test.csv", "currency": "USD"}
                if count <= 100_000:
                    with self.assertRaises(PassedLimitCheck):
                        bulk._plan(options)
                    enrich.assert_called_once()
                else:
                    with self.assertRaisesRegex(ValueError, "La carga supera 100,000 movimientos"):
                        bulk._plan(options)
                    enrich.assert_not_called()
