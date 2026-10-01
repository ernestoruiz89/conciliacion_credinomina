"""Imported evidence linked to a closed period must not be silently rewritten."""

import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import (
    cn_accounting_import as source_module,
)


def _row(**values):
    return frappe._dict({
        "name": "APP-1", "event_type": "Aplicacion",
        "manual_fx_rate": 0,
        "processing_route": "Operativa", "historical_period": "",
        "collection_period": "PER-OPEN", "collection_row_id": "",
        "application_allocation_detail": "[]", "allocation_detail": "[]",
        "match_status": "Conciliado",
        **values,
    })


def _document(old_rows, new_rows, **changes):
    old = frappe._dict({
        "name": "IMP-1",
        "source_file": "/private/files/core.xlsx", "file_hash": "hash-1",
        "historical_backfill": 0, "historical_period": "",
        "status": "Importado", "rows": old_rows,
    })
    new = frappe._dict({**old, "rows": new_rows, **changes})
    new.get_doc_before_save = lambda: old
    new._assert_no_closed_period_links = lambda rows: (
        source_module.CNAccountingImport._assert_no_closed_period_links(new, rows)
    )
    return new


class ClosedSourceImportGuardTests(unittest.TestCase):
    @staticmethod
    def _get_value(doctype, name, field):
        if doctype == "CN Reconciliation Period" and field == "status":
            return "Cerrado" if name == "PER-CLOSED" else "Pendiente"
        if doctype == "CN Collection Row" and field == "parent":
            return "PER-CLOSED" if name == "ROW-CLOSED" else "PER-OPEN"
        if doctype == "CN Complementary Item" and field == "period":
            return "PER-CLOSED" if name == "FEE-CLOSED" else "PER-OPEN"
        return None

    def _check(self, document):
        with patch.object(
            source_module.frappe, "db", Mock(get_value=Mock(side_effect=self._get_value)),
        ):
            source_module.CNAccountingImport._validate_closed_source_edits(document)

    def test_multiquincena_second_closed_period_rejects_rate_edit(self):
        original = _row(application_allocation_detail=json.dumps([
            {"period": "PER-OPEN", "collection_row_id": "ROW-OPEN", "amount_usd": 50},
            {"period": "PER-CLOSED", "collection_row_id": "ROW-CLOSED", "amount_usd": 50},
        ]))
        edited = _row(**{**original, "manual_fx_rate": 36.9})
        with patch.object(source_module.frappe, "throw", side_effect=ValueError) as reject:
            with self.assertRaises(ValueError):
                self._check(_document([original], [edited]))
        self.assertIn("PER-CLOSED", reject.call_args.args[0])
        self.assertIn("Reabrir período", reject.call_args.args[0])

    def test_unrelated_edit_and_recalculated_fields_are_allowed(self):
        closed = _row(name="APP-CLOSED", collection_period="PER-CLOSED")
        open_old = _row(name="APP-OPEN")
        closed_recalculated = _row(**{**closed, "match_status": "Enlace provisional"})
        open_edited = _row(**{**open_old, "manual_fx_rate": 36.9})
        document = _document([closed, open_old], [closed_recalculated, open_edited])
        token = source_module._source_reconcile_verified.set(True)
        try:
            self._check(document)
        finally:
            source_module._source_reconcile_verified.reset(token)

    def test_user_cannot_edit_derived_links_on_closed_source(self):
        closed = _row(collection_period="PER-CLOSED")
        edited = _row(**{**closed, "application_allocation_detail": json.dumps([
            {"period": "PER-OPEN", "collection_row_id": "ROW-OPEN", "amount_usd": 50},
        ])})
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self._check(_document([closed], [edited]))

    def test_new_row_or_new_import_cannot_target_closed_period(self):
        new = _row(name="", collection_period="", historical_period="PER-CLOSED")
        for document in (
            _document([], [new]),
            _document([], [], historical_period="PER-CLOSED"),
        ):
            with self.subTest(document=document):
                with patch.object(source_module.frappe, "throw", side_effect=ValueError):
                    with self.assertRaises(ValueError):
                        self._check(document)
        first_save = _document([], [new])
        first_save.get_doc_before_save = lambda: None
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self._check(first_save)

    def test_new_row_inherits_unchanged_closed_parent_historical_period(self):
        new = _row(name="", collection_period="", historical_period="")
        document = _document([], [new], historical_period="PER-CLOSED")
        document.get_doc_before_save().historical_period = "PER-CLOSED"
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self._check(document)
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                with patch.object(
                    source_module.frappe, "db", Mock(get_value=Mock(side_effect=self._get_value)),
                ):
                    source_module.CNAccountingImport.on_trash(document)

    def test_deleting_import_linked_to_closed_period_requires_reopen(self):
        linked = _row(collection_period="PER-CLOSED")
        document = _document([linked], [linked])
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                with patch.object(
                    source_module.frappe, "db", Mock(get_value=Mock(side_effect=self._get_value)),
                ):
                    source_module.CNAccountingImport.on_trash(document)

    def test_removing_linked_row_or_replacing_source_file_requires_reopen(self):
        linked = _row(collection_period="PER-CLOSED")
        for document in (
            _document([linked], []),
            _document([linked], [linked], source_file="/private/files/replacement.xlsx"),
        ):
            with self.subTest(document=document.source_file, rows=len(document.rows)):
                with patch.object(source_module.frappe, "throw", side_effect=ValueError):
                    with self.assertRaises(ValueError):
                        self._check(document)

    def test_closed_historical_and_accounting_deposit_links_are_protected(self):
        historical = _row(historical_period="PER-CLOSED", collection_period="")
        deposit = _row(
            name="DEP-1", event_type="Deposito", collection_period="",
            allocation_detail=json.dumps([
                {"tipo": "Partida complementaria", "partida": "FEE-CLOSED"},
            ]),
        )
        for old in (historical, deposit):
            changed = _row(**{**old, "manual_fx_rate": 36.9})
            with self.subTest(row=old.name):
                with patch.object(source_module.frappe, "throw", side_effect=ValueError):
                    with self.assertRaises(ValueError):
                        self._check(_document([old], [changed]))

    def test_row_linked_only_by_collection_row_id_is_protected(self):
        old = _row(collection_period="", collection_row_id="ROW-CLOSED")
        changed = _row(**{**old, "processing_route": "Historica"})
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self._check(_document([old], [changed]))


if __name__ == "__main__":
    unittest.main()
