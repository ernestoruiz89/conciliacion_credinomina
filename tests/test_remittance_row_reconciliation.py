import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import remittance_row_reconciliation as service


class RowReconciliationTests(unittest.TestCase):
    def document(self, explicit=False, shared=False):
        return frappe._dict(allocated_usd=70, allocation_detail=json.dumps([
            dict(tipo="Aplicacion historica", aplicacion_id="A", importe_usd=30, **({"fila_detalle":"R1"} if explicit else {})),
            dict(tipo="Aplicacion historica", aplicacion_id="B", importe_usd=40, fila_detalle="R2")]),
            detail_rows=[frappe._dict(name="R1", matched_targets='[{"claim_id":"H:A"}]'),
                         frappe._dict(name="R2", matched_targets='[{"claim_id":"H:A"}]' if shared else '[{"claim_id":"H:B"}]')], targets=[])

    def test_exact_provenance_preserves_other_row_even_with_same_destination(self):
        document=self.document(explicit=True, shared=True)
        removed, kept=service.split_row_allocations(document,"R1")
        self.assertEqual(sum(x['importe_usd'] for x in removed),30)
        self.assertEqual(kept,json.loads(document.allocation_detail)[1:])

    def test_legacy_unique_destination_can_be_attributed_without_guessing(self):
        removed, kept=service.split_row_allocations(self.document(),"R1")
        self.assertEqual([r['aplicacion_id'] for r in removed],['A'])
        self.assertEqual([r['aplicacion_id'] for r in kept],['B'])

    def test_ambiguous_legacy_and_unassigned_manual_links_are_rejected(self):
        with patch.object(frappe,'throw',side_effect=ValueError):
            with self.assertRaises(ValueError): service.split_row_allocations(self.document(shared=True),'R1')
            doc=self.document()
            doc.targets=[frappe._dict(historical_application='A',detail_row='')]
            with self.assertRaises(ValueError): service.split_row_allocations(doc,'R1')

    def test_inconsistent_journal_and_unknown_allocations_are_rejected(self):
        with patch.object(frappe,'throw',side_effect=ValueError):
            doc=self.document();doc.allocated_usd=80
            with self.assertRaises(ValueError): service.split_row_allocations(doc,'R1')
            doc=self.document();entries=json.loads(doc.allocation_detail)
            entries[0].update(tipo='Desconocido',fila_detalle='R1')
            del entries[0]['aplicacion_id']
            doc.allocation_detail=json.dumps(entries)
            with self.assertRaises(ValueError): service.split_row_allocations(doc,'R1')

    def test_application_and_signed_core_complement_are_released_together(self):
        doc=self.document(explicit=True)
        entries=json.loads(doc.allocation_detail)
        entries.append(dict(tipo='Partida complementaria',partida='CORE',importe_usd=-5,fila_detalle='R1'))
        doc.allocation_detail=json.dumps(entries);doc.allocated_usd=65
        removed,kept=service.split_row_allocations(doc,'R1')
        self.assertEqual(sum(r['importe_usd'] for r in removed),25)
        self.assertEqual(kept,[entries[1]])

    def test_write_permission_is_checked_before_locks(self):
        with patch.object(frappe,'get_doc') as get_doc, patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock:
            get_doc.return_value.check_permission.side_effect=frappe.PermissionError
            with self.assertRaises(frappe.PermissionError): service.unreconcile_row('DEP','R1','STAMP')
            lock.assert_not_called()

    def test_stale_request_cannot_change_rows(self):
        doc=Mock(docstatus=1, modified='NEW',employer='EMP')
        with patch.object(frappe,'get_doc',return_value=doc), patch.object(frappe,'db',Mock()), \
                patch.object(frappe,'has_permission',return_value=True), patch.object(frappe,'throw',side_effect=ValueError), \
                patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool'), \
                patch('credinomina_reconciliation.paying_employers.reconciliation_companies',return_value={'EMP'}):
            with self.assertRaises(ValueError): service.unreconcile_row('DEP','R1','OLD')
            doc.save.assert_not_called()
