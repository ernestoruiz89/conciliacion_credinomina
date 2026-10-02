import json
import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation import reconciliation_scope as scope
from credinomina_reconciliation.client_identity import ClientIdentityIndex, choose_client
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as remittance


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def as_dict(self):
        return dict(self)


class ScopedReconciliationTests(unittest.TestCase):
    def test_index_preserves_exact_names_aliases_and_cross_company_conflicts(self):
        clients = [
            dict(name="1", employer="A", client_number="001", national_id="ID1", employee_number="10",
                 client_name="ANA PÉREZ", client_aliases=["ANA MARIA PEREZ"]),
            dict(name="2", employer="B", client_number="002", national_id="ID2", employee_number="10",
                 client_name="ANA PEREZ", client_aliases=[]),
            dict(name="3", employer="A", client_number="003", national_id="ID3", employee_number="30",
                 client_name="CARLOS LOPEZ", client_aliases=["ANA MARIA PEREZ"]),
        ]
        index = ClientIdentityIndex(clients)
        for employer in ("", "A", "B", "C"):
            for number in ("", "001", "002", "999"):
                for national_id in ("", "ID1", "ID2", "UNKNOWN"):
                    for employee in ("", "10", "30", "999"):
                        for name in ("", "Pérez Ana", "ANA MARIA PEREZ", "CARLOS LOPEZ", "OTHER"):
                            record = dict(client_number=number, national_id=national_id,
                                          employee_number=employee, client_name=name)
                            self.assertEqual(choose_client(record, index, employer),
                                             choose_client(record, clients, employer))

    def load(self, imports, rows, companies=("A",), collections=(), items=()):
        queries, loaded = [], []

        def get_all(doctype, **kwargs):
            queries.append((doctype, kwargs))
            if doctype == "CN Accounting Import":
                return imports
            if doctype == "CN Reconciliation Period":
                return [Record(name="PA", employer="A"), Record(name="PB", employer="B")]
            if doctype == "CN Employer":
                return [Record(name="A", employer_name="A"), Record(name="B", employer_name="B")]
            if doctype == "CN Source Row":
                candidates = [row for row in rows if row.parent in kwargs["filters"]["parent"][1]]
                start = kwargs["limit_start"]
                return candidates[start:start + kwargs["limit_page_length"]]
            if doctype == "CN Collection Row":
                return collections
            if doctype == "CN Complementary Item":
                return items
            return []

        def get_doc(doctype, name):
            loaded.append(name)
            return Record(name=name, check_permission=Mock())

        with (
            patch.object(scope.frappe, "get_all", side_effect=get_all),
            patch.object(scope.frappe, "get_doc", side_effect=get_doc),
            patch.object(scope.frappe, "parse_json", side_effect=json.loads),
            patch.object(scope, "attach_employer_aliases"),
            patch.object(scope, "_", side_effect=lambda value: value),
            patch.object(scope.frappe, "throw", side_effect=ValueError),
        ):
            result = scope.load_scoped_imports(companies)
        return result, loaded, queries

    def test_only_related_documents_are_loaded_and_read_pages_are_bounded(self):
        imports = [Record(name="IA", employer="A"), Record(name="IB", employer="B")]
        rows = [Record(parent="IB", employer_text="B") for _ in range(2001)]
        result, loaded, queries = self.load(imports, rows)
        self.assertEqual(loaded, ["IA"])
        result[0].check_permission.assert_called_once_with("write")
        pages = [kw for dt, kw in queries if dt == "CN Source Row"]
        self.assertEqual([kw["limit_start"] for kw in pages], [0, 1000, 2000])
        self.assertTrue(all(kw["limit_page_length"] == 1000 for kw in pages))
        self.assertTrue(all("source_description" not in kw["fields"] for kw in pages))

    def test_linked_payers_are_loaded_in_original_creation_order(self):
        imports = [Record(name="IB", employer="B"), Record(name="IA", employer="A")]
        self.assertEqual(self.load(imports, [], companies=("A", "B"))[1], ["IB", "IA"])

    def test_outside_import_linked_to_scope_is_not_silently_excluded(self):
        imports = [Record(name="IB", employer="B")]
        cases = [
            Record(parent="IB", historical_period="PA"),
            Record(parent="IB", portfolio_employer="A", employer_text="B"),
            Record(parent="IB", employer_text="A"),
            Record(parent="IB", collection_row_id="CR"),
            Record(parent="IB", application_allocation_detail='[{"collection_row_id":"CR"}]'),
            Record(parent="IB", allocation_detail='[{"partida":"X"}]'),
        ]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.load(imports, [row], collections=[Record(name="CR", parent="PA")],
                          items=[Record(name="X", period="PA")])

    def test_import_header_and_selected_rows_reject_wrong_company(self):
        with self.assertRaises(ValueError):
            self.load([Record(name="IB", employer="B", historical_period="PA")], [])
        with self.assertRaises(ValueError):
            self.load([Record(name="IA", employer="A")], [Record(parent="IA", portfolio_employer="B")])

    def test_deposit_destinations_do_not_change_accounting_owner(self):
        _, loaded, _ = self.load([Record(name="IB", employer="B")], [
            Record(parent="IB", employer_text="B", event_type="Deposito", allocation_detail='[{"periodo":"PA"}]'),
        ])
        self.assertEqual(loaded, [])

    def test_portfolio_company_takes_priority_over_description(self):
        _, loaded, _ = self.load([Record(name="IB", employer="B")], [
            Record(parent="IB", employer_text="A", portfolio_employer="B"),
        ])
        self.assertEqual(loaded, [])

    def test_unchanged_documents_are_not_saved_and_transient_fields_are_ignored(self):
        doc = Record(name="IA", rows=[Record(amount_usd=20)], save=Mock())
        doc._reconciliation_original_state = scope.document_state(doc)
        doc.rows[0]._source_import = "IA"
        self.assertFalse(engine._save_reconciled_document(doc))
        doc.save.assert_not_called()
        doc.rows[0].amount_usd = 21
        self.assertTrue(engine._save_reconciled_document(doc))
        doc.save.assert_called_once_with(ignore_permissions=True)

    def test_deposit_passes_its_company_to_engine_never_global(self):
        doc = Record(employer="A")
        with patch.object(engine, "_reconcile_sources", return_value={}) as run:
            remittance.CNRemittanceAllocation._reconcile(doc)
        run.assert_called_once_with("A", progress=None)

    def test_missing_deposit_company_cannot_fall_back_to_global(self):
        with patch.object(engine, "_reconcile_sources") as run, patch.object(remittance.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                remittance.CNRemittanceAllocation._reconcile(Record())
        run.assert_not_called()
