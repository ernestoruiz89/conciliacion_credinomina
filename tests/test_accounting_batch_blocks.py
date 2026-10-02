import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation import accounting_batch_store as store
from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.accounting_batch import accounting_group_csv
from credinomina_reconciliation.parsers import read_table, _records_from_header


class BatchBlockTests(unittest.TestCase):
    def plan(self, groups=(), complementary=(), deposits=()):
        return {"file_hash": "hash", "file_name": "ledger.csv", "groups": list(groups),
                "complementary": list(complementary), "deposits": list(deposits)}

    def test_bounds_preserve_order_and_independent_similar_rows(self):
        items = [{"source_row": i, "amount": 10} for i in range(26000)]
        blocks = list(store.split_plan(self.plan(complementary=items)))
        self.assertEqual(len(blocks), 1040)
        self.assertTrue(all(len(block["complementary"]) == 25 for block in blocks))
        self.assertEqual([row for block in blocks for row in block["complementary"]], items)

    def test_never_split_one_company_day_document(self):
        groups = [{"rows": [1] * size} for size in (800, 300, 2000, 10)]
        blocks = list(store.split_plan(self.plan(groups=groups, deposits=[1, 2])))
        self.assertEqual([len(b["groups"]) for b in blocks], [1, 1, 1, 1])
        self.assertEqual(blocks[-1]["deposits"], [1, 2])

    def test_mixed_kinds_share_document_limit(self):
        blocks = list(store.split_plan(self.plan(groups=[{"rows": [1]}] * 24,
                                                 complementary=[1, 2], deposits=[3])))
        self.assertEqual(blocks[0]["complementary"], [1])
        self.assertEqual(blocks[1]["complementary"], [2])
        self.assertEqual(blocks[1]["deposits"], [3])

    def test_empty_plan_no_blocks(self):
        self.assertEqual(list(store.split_plan(self.plan())), [])

    def test_manual_company_and_csv_frozen_in_block(self):
        group = {"rows": [{"source_row": 8, "_manual_employer": "Seleccionada"}]}
        with patch.object(store, "insert_internal") as insert:
            count = store.prepare_blocks("token", self.plan(groups=[group]), {8: {"cuenta_contable": "123", "empresa": "Original"}})
        self.assertEqual(count, 1)
        payload = insert.call_args.kwargs["payload"]
        self.assertIn("CN_EMPRESA_ASIGNADA", payload)
        self.assertIn("Seleccionada", payload)
        self.assertIn("Original", payload)
        self.assertNotIn("csv_content", group, "Release temporary CSV after storing each bounded block")

    def test_overlap_only_skips_same_physical_rows_and_preserves_csv_positions(self):
        rows = [{"source_row": i, "amount_usd": 10, "_manual_employer": "Elegida"} for i in (2, 20, 200)]
        raw = {i: {"cuenta_contable": "123", "descripcion": "Mismo asiento", "debito_del_mes": 10} for i in (2, 20, 200)}
        group = {"rows": rows, "count": 3, "total_usd": 30,
                 "csv_content": accounting_group_csv(raw, rows).decode("utf-8")}
        plan = self.plan(groups=[group])
        with patch.object(bulk.frappe, "db", Mock(sql=Mock(return_value=[(20,)]))):
            bulk._exclude_completed_applications(plan)
        self.assertEqual([row["source_row"] for row in group["rows"]], [2, 200])
        self.assertEqual(group["total_usd"], 20)
        output = list(_records_from_header(read_table("group.csv", group["csv_content"].encode()), "cuenta_contable"))
        self.assertEqual([int(row["cn_fila_origen"]) for _, row in output], [2, 200])
        self.assertEqual([row["cn_empresa_asignada"] for _, row in output], ["Elegida", "Elegida"])
