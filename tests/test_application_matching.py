import copy
import itertools
import unittest
from collections import defaultdict
from unittest.mock import Mock, patch

from credinomina_reconciliation.application_matching import CollectionCandidateIndex
from credinomina_reconciliation.reconciliation import application_matches_collection
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def as_dict(self):
        return dict(self)


class FullScan:
    """The original candidate selection, retained only as a test oracle."""

    def __init__(self, rows, aliases):
        self.rows = rows

    def candidates(self, period, application):
        return [row for row in self.rows if row.parent == period]


class CollectionCandidateTests(unittest.TestCase):
    def test_index_returns_the_same_matches_as_a_full_scan_in_the_same_order(self):
        rows = [Row(name=str(i), parent=period, loan_number=loan, client=client,
                    client_name=name, national_id=national_id, employee_number=employee,
                    doctype="CN Collection Row", client_number="IGNORED")
                for i, (period, loan, client, national_id, employee, name) in enumerate(itertools.product(
                    ("P1", "P2"), ("", "00121-1", "121-2"), ("", "7"),
                    ("", "ID1"), ("", "E1"), ("ANA PÉREZ", "LUIS GOMEZ")))]
        # Interchange records may carry the number directly; persisted payroll
        # resolves its client link instead. Duplicate quotas stay distinct.
        rows.extend([Row(name="legacy", parent="P1", client_number="8", loan_number="A+B"),
                     Row(name="duplicate", **{k: v for k, v in rows[1].items() if k != "name"})])
        aliases = {row.name: ("ANA MARIA PEREZ",) if row.client_name == "ANA PÉREZ" else () for row in rows}
        original = copy.deepcopy(rows)
        index = CollectionCandidateIndex(rows, aliases)
        for period, loan, client, national_id, employee, name in itertools.product(
            ("P1", "P2", "UNKNOWN"), ("", "121", "000121-2", "A+B", "404-1"),
            ("", "7", "8", "IGNORED"), ("", "ID1", "OTHER"),
            ("", "E1", "OTHER"), ("", "Perez Ana", "ANA MARIA PEREZ"),
        ):
            application = dict(loan_number=loan, client_number=client, national_id=national_id,
                               employee_number=employee, client_name=name)
            expected = [row.name for row in rows if row.parent == period
                        and application_matches_collection(application, row, aliases[row.name])]
            actual = [row.name for row in index.candidates(period, application)
                      if application_matches_collection(application, row, aliases[row.name])]
            if actual != expected:
                self.fail(f"Changed matching: {period}, {application}: {actual} != {expected}")
        self.assertEqual(rows, original)

    def match(self, sources, collections, *, index=CollectionCandidateIndex, fixed=(), clients=()):
        sources = copy.deepcopy(sources)
        resolver = Mock()
        resolver.resolve.side_effect = lambda source: (source.get("_source_employer") or "E1", "")
        periods = [Row(name="P1", employer="E1", reconciliation_mode="Operativa",
                       collection_cycle="Mensual", payroll_month="2026-09-01"),
                   Row(name="P2", employer="E2", reconciliation_mode="Operativa",
                       collection_cycle="Mensual", payroll_month="2026-09-01")]
        with patch.object(engine, "load_client_index", return_value=clients), \
             patch.object(engine.frappe, "get_all", return_value=[]), \
             patch.object(engine, "attach_employer_aliases"), \
             patch.object(engine, "AccountingEmployerResolver", return_value=resolver), \
             patch.object(engine, "CollectionCandidateIndex", index), \
             patch.object(engine, "application_matches_collection", wraps=application_matches_collection) as compare:
            engine._match_applications(sources, collections, [], defaultdict(list), periods, fixed_sources=fixed)
        return sources, compare.call_count

    def source(self, name="A1", **values):
        return Row(dict(name=name, event_type="Aplicacion", effective=1, historical_period="P1",
                        event_date="2026-09-30", currency="USD", amount=100, loan_number="109-1",
                        _source_employer="E1", client_number="7") | values)

    def collection(self, name="C1", **values):
        return Row(dict(name=name, parent="P1", loan_number="00109-1", client="7",
                        client_name="ANA PEREZ", expected_usd=100, expected_nio=0) | values)

    def test_engine_preserves_ambiguity_references_capacity_and_company_guards(self):
        base = self.collection()
        clients = [dict(name="7", employer="E1", client_name="ANA PEREZ", client_aliases=["ANA MARIA PEREZ"]),
                   dict(name="8", employer="E1", client_name="ANA PEREZ", client_aliases=[])]
        scenarios = [
            ([self.source()], [base]),
            ([self.source()], [base, self.collection("C2")]),
            ([self.source(reference="REF")], [self.collection(application_reference="REF"),
                                               self.collection("C2", application_reference="OTHER")]),
            ([self.source(reference="DIFFERENT")], [self.collection(application_reference="REF")]),
            ([self.source(amount=120)], [base]),
            ([self.source(amount=40), self.source("A2", amount=60)], [base]),
            ([self.source(client_number="8")], [base]),
            ([self.source(_source_employer="E2")], [base]),
            ([self.source(installment_number="2")], [self.collection(installment_number="1")]),
            ([self.source(loan_number="", client_number="", national_id="ID1")],
             [self.collection(national_id="ID1"), self.collection("C2", national_id="OTHER")]),
            ([self.source(loan_number="", client_number="", client_name="ANA MARIA PEREZ")], [base]),
            ([self.source(loan_number="", client_number="", client_name="ANA PEREZ")], [base]),
            ([self.source()], [self.collection("C2", parent="P2"), base]),
        ]
        fixed = [self.source("PAID", amount=20, match_status="Conciliado",
                             application_allocation_detail='[{"collection_row_id":"C1","amount_usd":20}]')]
        for sources, collections in scenarios:
            for reserved in ((), fixed):
                with self.subTest(sources=sources, reserved=bool(reserved)):
                    expected, _ = self.match(sources, collections, index=FullScan, clients=clients, fixed=reserved)
                    actual, _ = self.match(sources, collections, clients=clients, fixed=reserved)
                    self.assertEqual(actual, expected)

    def test_unique_credit_matching_does_not_scan_every_payroll_row(self):
        count = 500
        collections = [self.collection(f"C{i}", loan_number=f"{i + 1:06}-1") for i in range(count)]
        sources = [self.source(f"A{i}", loan_number=f"{i + 1}-1") for i in range(count)]
        actual, comparisons = self.match(sources, collections)
        self.assertEqual(comparisons, count)
        self.assertEqual([row.collection_row_id for row in actual], [row.name for row in collections])
        self.assertTrue(all(row.historical_period == "P1" for row in actual))
