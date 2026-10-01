import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.historical import historical_status
from credinomina_reconciliation.patches.v1_0 import unify_period_statuses as migration
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _operative_period_status


class SharedPeriodStatusTests(unittest.TestCase):
    def period(self, paid=0, complete=False):
        return frappe._dict(status="Pendiente", exception_count=0, collection_rows=[frappe._dict(
            remitted_usd=paid, deduction_status="Deduccion total",
            application_status="Aplicado y remitido" if complete else "Pendiente")])

    def test_both_modes_share_payment_states(self):
        for paid, complete, status in [(0, False, "Pendiente"), (40, False, "Parcial"), (100, True, "Conciliado")]:
            self.assertEqual(_operative_period_status(self.period(paid, complete)), status)
            self.assertEqual(historical_status(100, paid), status)
        self.assertEqual(_operative_period_status(self.period(100, True), True), "Con excedente")
        period = self.period(100, True)
        period.exception_count = 1
        self.assertEqual(_operative_period_status(period), "Parcial")
        self.assertEqual(_operative_period_status(frappe._dict(status="Borrador", collection_rows=[])), "Borrador")

    def test_migration_preserves_closure_and_balances_and_is_idempotent(self):
        rows = [frappe._dict(name=str(i), status=old, status_before_close="", remitted_usd=0)
                for i, old in enumerate(migration.RENAMES)]
        rows += [frappe._dict(name="CLOSED", status="Cerrado", status_before_close="Historico conciliado", remitted_usd=100),
                 frappe._dict(name="PARTIAL", status="Detalle empresa cargado", status_before_close="", remitted_usd=25)]
        def update(doctype, name, values, update_modified):
            self.assertEqual(doctype, "CN Reconciliation Period")
            self.assertFalse(update_modified)
            self.assertLessEqual(set(values), {"status", "status_before_close"})
            next(row for row in rows if row.name == name).update(values)
        setter = Mock(side_effect=update)
        with patch.object(migration.frappe, "get_all", return_value=rows), \
             patch.object(migration.frappe, "db", SimpleNamespace(set_value=setter)), \
             patch.object(migration.frappe, "clear_cache"):
            migration.execute()
            count = setter.call_count
            migration.execute()
        self.assertEqual(setter.call_count, count)
        self.assertEqual(rows[-2].status, "Cerrado")
        self.assertEqual(rows[-2].status_before_close, "Conciliado")
        self.assertEqual(rows[-1].status, "Parcial")
        self.assertEqual(rows[-1].remitted_usd, 25)
