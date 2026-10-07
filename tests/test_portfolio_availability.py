import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import bulk_accounting_import as bulk, credit_portfolio, portfolio_naming
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import CNAccountingImport


def reject(message, *args, **kwargs):
    raise ValueError(message)


class PortfolioAvailabilityTests(unittest.TestCase):
    def test_explicit_disabled_cut_rejected_for_bulk_and_enrichment(self):
        cut = frappe._dict(name="CUT", disabled=1, status="Importado", check_permission=Mock())
        with patch.object(frappe, "get_doc", return_value=cut), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value=cut))), \
             patch.object(frappe, "throw", side_effect=reject):
            for action in (lambda: bulk._required_portfolio_snapshot("CUT"),
                           lambda: credit_portfolio.enrich_accounting_records([], "CUT")):
                with self.assertRaisesRegex(ValueError, "desactivado"):
                    action()

    def test_new_selection_rejected_but_existing_reference_can_be_saved(self):
        for previous in (None, frappe._dict(portfolio_snapshot="OTHER"), frappe._dict(portfolio_snapshot="CUT")):
            doc = frappe._dict(portfolio_snapshot="CUT", status="Importado", employer=None,
                               get_doc_before_save=lambda: previous)
            with patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))), \
                 patch.object(frappe, "throw", side_effect=reject):
                if previous and previous.portfolio_snapshot == "CUT":
                    CNAccountingImport._validate_employer_scope(doc)
                else:
                    with self.assertRaisesRegex(ValueError, "desactivado"):
                        CNAccountingImport._validate_employer_scope(doc)

    def test_custom_names_survive_reimport(self):
        doc = frappe._dict(name="Cartera revisada de julio")
        with patch.object(frappe, "rename_doc") as rename:
            self.assertEqual(portfolio_naming.rename_snapshot_for_date(doc, "2025-07-31", "2025-07-31"), doc.name)
            rename.assert_not_called()

    def test_default_names_continue_following_import_date(self):
        for name, old_date in (("CARTERA-BORRADOR-2026-00001", None), ("CARTERA-6-2025", "2025-06-30")):
            doc = frappe._dict(name=name, get_all_children=lambda: [])
            with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), \
                 patch.object(frappe, "rename_doc", return_value="CARTERA-7-2025") as rename:
                self.assertEqual(portfolio_naming.rename_snapshot_for_date(doc, "2025-07-31", old_date), "CARTERA-7-2025")
                rename.assert_called_once()
