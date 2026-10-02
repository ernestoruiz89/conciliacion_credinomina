import csv
from copy import deepcopy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import accounting_origin_repair as repair
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.parsers import SourceFileError, file_sha256, parse_accounting_movements
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def csv_content(positions=(2500,), include_origin=True):
    stream = io.StringIO()
    writer = csv.writer(stream)
    headers = ["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF", "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES"]
    if include_origin:
        headers.append("CN_FILA_ORIGEN")
    writer.writerow(headers)
    for position in positions:
        values = ["1602", "2025-04-30", "12", "05", "001011019", "REF",
                  "PRESTAMO 109070-1 PAGO APLICADO POR DEDUCCIONES NOMINA", 51.08, 0]
        if include_origin:
            values.append(position)
        writer.writerow(values)
    return stream.getvalue().encode()


def fixed_records(positions=(2500,)):
    rows = parse_accounting_movements("individual.csv", csv_content(positions))
    for row in rows:
        row["source_currency"] = "USD"
    with patch.object(frappe, "get_all", return_value=[]):
        return identify_lines(rows, "original-hash")


class AccountingOriginRepairTests(unittest.TestCase):
    def test_original_position_survives_csv_instead_of_its_local_position(self):
        rows = parse_accounting_movements("individual.csv", csv_content((1400, 25999)))
        self.assertEqual([row["source_row"] for row in rows], [1400, 25999])
        plain = parse_accounting_movements("original.csv", csv_content(include_origin=False))
        self.assertEqual(plain[0]["source_row"], 2)
        self.assertIsNone(plain[0]["_csv_original_row"])

    def test_invalid_or_repeated_metadata_never_falls_back_to_csv_position(self):
        for value in ("", "0", "-2", "1.5", "ABC", "NaN", "1e5"):
            with self.subTest(value=value), self.assertRaisesRegex(SourceFileError, "CN_FILA_ORIGEN"):
                parse_accounting_movements("individual.csv", csv_content((value,)))
        with self.assertRaisesRegex(SourceFileError, "repetida"):
            parse_accounting_movements("individual.csv", csv_content((2000, 2000)))

    def test_missing_original_column_blocks_bulk_reimport_before_enrichment(self):
        rows = parse_accounting_movements("individual.csv", csv_content(include_origin=False))
        def reject(message):
            raise ValueError(message)
        with patch.object(accounting.frappe, "throw", side_effect=reject), \
             patch.object(accounting, "_", side_effect=lambda text: text), \
             patch.object(accounting, "enrich_accounting_records") as enrich:
            with self.assertRaisesRegex(ValueError, "CN_FILA_ORIGEN"):
                accounting._validate_bulk_reimport(SimpleNamespace(), rows)
            enrich.assert_not_called()

    def test_plan_repairs_only_origin_and_false_mirrors_leaving_ids_money_and_links(self):
        records = fixed_records()
        saved = frappe._dict({**records[0], "name": "stable-row-id", "idx": 1,
            "source_row": 6, "accounting_source_key": "unrelated-deposit-key", "remittance_allocation": "DEP-WRONG",
            "complementary_item": "COMP-WRONG", "historical_period": "CLOSED", "historical_remitted_usd": 40,
            "application_adjustment_usd": -5, "net_applied_usd": 46.08, "deposit_match_status": "Depósito parcial",
            "application_allocation_detail": '[{"deposito":"DEP-VALID","importe_usd":40}]'})
        original = deepcopy(saved)
        plan = repair.plan_origin_repairs(SimpleNamespace(rows=[saved]), records)
        self.assertEqual(saved, original)
        self.assertEqual(plan, [{"row": "stable-row-id", "before": {"source_row": 6,
            "accounting_source_key": "unrelated-deposit-key", "remittance_allocation": "DEP-WRONG", "complementary_item": "COMP-WRONG"},
            "after": {"source_row": 2500, "accounting_source_key": records[0]["accounting_source_key"],
                      "remittance_allocation": "", "complementary_item": ""}}])
        self.assertTrue(set(plan[0]["after"]).issubset(repair.REPAIR_FIELDS))
        saved.update(plan[0]["after"])
        self.assertEqual(repair.plan_origin_repairs(SimpleNamespace(rows=[saved]), records), [])

    def test_independent_identical_lines_keep_their_ids_order_and_own_keys(self):
        records = fixed_records((1400, 1401))
        saved = [frappe._dict({**record, "idx": index, "name": f"ROW-{index}", "source_row": index + 1,
                 "accounting_source_key": "wrong"}) for index, record in enumerate(records, 1)]
        plan = repair.plan_origin_repairs(SimpleNamespace(rows=saved[::-1]), records)
        self.assertEqual([change["row"] for change in plan], ["ROW-1", "ROW-2"])
        self.assertEqual([change["after"]["source_row"] for change in plan], [1400, 1401])
        self.assertNotEqual(plan[0]["after"]["accounting_source_key"], plan[1]["after"]["accounting_source_key"])

    def test_mismatches_reject_the_whole_document_without_partial_updates(self):
        records = fixed_records((1400, 1401))
        saved = [frappe._dict({**record, "idx": index, "name": f"ROW-{index}"}) for index, record in enumerate(records, 1)]
        for difference in ({"source_debit": 60}, {"event_date": "2025-05-01"}, {"voucher": "OTHER"},
                           {"source_description": "Alterado"}, {"event_type": "Ajuste"}):
            rows = deepcopy(saved)
            rows[-1].update(difference)
            with self.subTest(difference=difference), self.assertRaises(SourceFileError):
                repair.plan_origin_repairs(SimpleNamespace(rows=rows), records)
        with self.assertRaises(SourceFileError):
            repair.plan_origin_repairs(SimpleNamespace(rows=saved[:1]), records)

    def test_reordered_identical_healthy_lines_are_not_swapped_during_repair(self):
        records = fixed_records((1400, 1401))
        saved = [frappe._dict({**record, "idx": 2 - index, "name": f"ROW-{index}"})
                 for index, record in enumerate(records)]
        self.assertEqual(repair.plan_origin_repairs(SimpleNamespace(rows=saved), records), [])
        saved[0].accounting_source_key = "wrong"
        saved[0].source_row = 2
        changes = repair.plan_origin_repairs(SimpleNamespace(rows=saved), records)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["row"], "ROW-0")
        self.assertEqual(changes[0]["after"]["source_row"], 1400)

    def test_csv_repair_requires_unchanged_csv_and_matching_original_file_row(self):
        content = csv_content()
        document = SimpleNamespace(source_file="individual.csv", file_hash=file_sha256(content), currency="USD",
                                   manual_fx_rate=0, bulk_source_hash="original-hash")
        original = {row["source_row"]: row for row in parse_accounting_movements("individual.csv", content)}
        with patch.object(repair, "_read_file", return_value=(SimpleNamespace(file_name="individual.csv"), content)), \
             patch.object(frappe, "get_all", return_value=[]):
            records = repair._csv_records(document, original)
            self.assertEqual(records[0]["source_row"], 2500)
            for bad_original in ({}, {2500: {**original[2500], "accounting_source_key": "different"}}):
                with self.assertRaisesRegex(SourceFileError, "no acredita"):
                    repair._csv_records(document, bad_original)
            document.file_hash = "changed"
            with self.assertRaisesRegex(SourceFileError, "cambiado"):
                repair._csv_records(document, original)

    def test_original_hash_must_match_and_alternate_attachment_can_recover_it(self):
        content = csv_content(include_origin=False)
        parents = [frappe._dict(bulk_source_file=url, bulk_source_hash=file_sha256(content))
                   for url in ("missing.csv", "available.csv")]
        with patch.object(repair, "_read_file", side_effect=[FileNotFoundError("missing"),
                (SimpleNamespace(file_name="available.csv"), content)]):
            rows = repair._original_records(parents)
            self.assertEqual(list(rows), [2])
        with patch.object(repair, "_read_file", return_value=(SimpleNamespace(file_name="changed.csv"), content + b"\n")):
            with self.assertRaisesRegex(SourceFileError, "huella"):
                repair._original_records(parents)
        with self.assertRaisesRegex(SourceFileError, "Falta el archivo"):
            repair._original_records([frappe._dict(bulk_source_file="", bulk_source_hash="hash")])

    def test_replaced_csv_cannot_forge_origin_or_change_original_financial_evidence(self):
        document = SimpleNamespace(currency="USD")
        records = parse_accounting_movements("individual.csv", csv_content())
        original = {2500: deepcopy(records[0])}
        records[0]["source_currency"] = "USD"
        repair.assert_csv_origins(document, records, original)
        for changes in ({"source_row": 6}, {"source_debit": 100}, {"voucher": "OTHER"},
                        {"event_date": "2025-05-01"}, {"accounting_source_key": "forged"},
                        {"event_type": "Deposito"}, {"_csv_original_row": None}):
            with self.subTest(changes=changes), self.assertRaises(SourceFileError):
                repair.assert_csv_origins(document, [{**records[0], **changes}], original)


if __name__ == "__main__":
    unittest.main()
