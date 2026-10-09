import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
    CNAccountingImport,
)
from credinomina_reconciliation.patches.v1_0 import refresh_accounting_import_summaries as migration


class AccountingImportSummaryMigrationTests(unittest.TestCase):
    def document(self, **overrides):
        doc = frappe._dict(doctype="CN Accounting Import", name="CONTA-1",
            status="Importado con excepciones", exception_count=1, matched_count=0,
            row_count=1, ignored_count=0, total_usd=100, total_net_applied_usd=100,
            rows=[frappe._dict(name="ROW-1", event_type="Aplicacion", effective=1,
                match_status="Conciliado", deposit_match_status="Depósito conciliado",
                amount_usd=100, collection_period="CLOSED-PERIOD", collection_row_id="COL-1",
                allocation_detail='[{"deposito":"DEP-1","importe_usd":100}]')])
        doc.update(overrides)
        doc.recalculate_reconciliation_summary = lambda: CNAccountingImport.recalculate_reconciliation_summary(doc)
        doc.save = Mock(side_effect=AssertionError("Migration must not save/reconcile"))
        return doc

    def test_settled_rows_clear_stale_exception_header(self):
        for state in ("Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"):
            with self.subTest(state=state):
                doc = self.document()
                doc.rows[0].deposit_match_status = state
                doc.recalculate_reconciliation_summary()
                self.assertEqual((doc.status, doc.exception_count, doc.matched_count), ("Importado", 0, 1))

    def test_real_exceptions_remain_even_when_collection_is_matched(self):
        for changes in (
            {"deposit_match_status": "Sin deposito"},
            {"deposit_match_status": "Depósito parcial"},
            {"deposit_match_status": None},
            {"match_status": "Ambiguo"},
            {"match_status": "Sin coincidencia"},
            {"event_type": "Deposito", "unallocated_usd": 25},
        ):
            with self.subTest(changes=changes):
                doc = self.document(status="Importado")
                doc.rows[0].update(changes)
                doc.recalculate_reconciliation_summary()
                self.assertEqual((doc.status, doc.exception_count, doc.matched_count),
                                 ("Importado con excepciones", 1, 0))

    def test_draft_and_failed_imports_are_not_promoted(self):
        for status in ("Borrador", "Fallido"):
            doc = self.document(status=status)
            doc.recalculate_reconciliation_summary()
            self.assertEqual(doc.status, status)

    def test_patch_repairs_only_header_and_is_idempotent(self):
        doc = self.document()
        rows_before = copy.deepcopy(doc.rows)
        with patch.object(migration.frappe, "get_all", return_value=[doc.name]) as get_all, \
             patch.object(migration.frappe, "get_doc", return_value=doc), \
             patch.object(migration.frappe, "db", Mock()) as db, \
             patch.object(migration.frappe, "clear_document_cache") as clear:
            migration.execute()
            db.set_value.assert_called_once_with(doc.doctype, doc.name,
                {"matched_count": 1, "exception_count": 0, "status": "Importado"}, update_modified=False)
            clear.assert_called_once_with(doc.doctype, doc.name)
            self.assertEqual(get_all.call_args.kwargs["filters"], {
                "status": ["in", ["Importado", "Importado con excepciones"]], "docstatus": ["!=", 2],
            })
            db.reset_mock()
            clear.reset_mock()
            migration.execute()
            db.set_value.assert_not_called()
            clear.assert_not_called()
        doc.save.assert_not_called()
        self.assertEqual(doc.rows, rows_before)
        self.assertEqual((doc.total_usd, doc.total_net_applied_usd), (100, 100))
