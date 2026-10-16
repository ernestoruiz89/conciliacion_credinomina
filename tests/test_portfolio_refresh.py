import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import portfolio_refresh as refresh
from credinomina_reconciliation.credit_portfolio import PORTFOLIO_FIELDS, refresh_accounting_portfolio
from credinomina_reconciliation.remittance_portfolio import load_cuts, update_detail_portfolio
from tests.test_credit_portfolio_enrichment import portfolio_row


class PortfolioRefreshTests(unittest.TestCase):
    def test_refresh_replaces_status_and_clears_removed_credit_evidence(self):
        cut = frappe._dict(name='CUT', disabled=1, rows=[portfolio_row(credit_status='Cancelado', credit_lifecycle='Cancelado')])
        rows = [frappe._dict(event_type='Aplicacion', loan_number=loan, client_name='Nombre fuente',
                            amount=80, portfolio_credit_status='Corriente', portfolio_client='STALE',
                            portfolio_credit_lifecycle='Activo', portfolio_client_name='Anterior')
                for loan in ('CR-100', 'REMOVED')]
        with patch('credinomina_reconciliation.credit_portfolio.frappe.get_all', return_value=[]), \
             patch('credinomina_reconciliation.credit_portfolio.attach_employer_aliases'), \
             patch('credinomina_reconciliation.credit_portfolio.employer_alias_index', return_value=({}, set())):
            refresh_accounting_portfolio(rows, cut)
        self.assertEqual(rows[0].portfolio_credit_status, 'Cancelado')
        self.assertEqual(rows[0].portfolio_credit_lifecycle, 'Cancelado')
        self.assertEqual(rows[0].portfolio_client, 'CLI-7')
        self.assertEqual(rows[1].portfolio_credit_status, '')
        self.assertEqual(rows[1].portfolio_client, '')
        self.assertEqual(rows[1].portfolio_client_name, '')
        self.assertEqual(rows[1].portfolio_validation_status, 'Crédito no encontrado en el corte')
        self.assertTrue(all(r.portfolio_snapshot_used == 'CUT' and r.amount == 80 for r in rows))

    def test_persistence_only_updates_portfolio_fields_and_is_idempotent(self):
        doc = frappe._dict(doctype='CN Accounting Import', name='IMPORT',
                           add_comment=Mock(), notify_update=Mock())
        cut = frappe._dict(name='CUT', file_hash='HASH')
        old = frappe._dict(doctype='CN Source Row', name='ROW', portfolio_credit_status='Corriente',
                           client='CLIENT', amount=100, historical_detail='evidence')
        new = dict(old, portfolio_credit_status='Cancelado', amount=999, client='OTHER')
        with patch.object(frappe.db, 'set_value') as write, patch.object(frappe, 'clear_document_cache'):
            self.assertEqual(refresh._persist_evidence(doc, [old], [new], PORTFOLIO_FIELDS, cut), 1)
            self.assertEqual(write.call_args_list[0].args,
                             ('CN Source Row', 'ROW', {'portfolio_credit_status': 'Cancelado'}))
            write.reset_mock()
            old.portfolio_credit_status = 'Cancelado'
            self.assertEqual(refresh._persist_evidence(doc, [old], [new], PORTFOLIO_FIELDS, cut), 0)
            write.assert_not_called()

    def test_mixed_import_refreshes_only_rows_bound_to_the_cut_and_flags_company_change(self):
        class Row(frappe._dict):
            def as_dict(self):
                return dict(self)

        rows = [Row(name='BOUND', portfolio_snapshot_used='CUT', event_type='Aplicacion'),
                Row(name='REVIEW', portfolio_snapshot_used='CUT', event_type='Ajuste'),
                Row(name='OTHER', portfolio_snapshot_used='OTHER-CUT', event_type='Aplicacion')]
        doc = frappe._dict(docstatus=0, rows=rows, employer='ORIGINAL', portfolio_snapshot='')
        cut = frappe._dict(name='CUT', rows=[])
        def enrich(values, *args, **kwargs):
            for value in values:
                value['portfolio_employer'] = 'CHANGED'
        with patch.object(refresh, '_imports_for_cuts', return_value={'IMPORT'}), \
             patch.object(refresh, '_related_deposits', return_value=[]), \
             patch.object(refresh, '_locked_document', return_value=doc), \
             patch.object(refresh, 'refresh_accounting_portfolio', side_effect=enrich), \
             patch.object(refresh, '_persist_evidence', return_value=2) as persist:
            result = refresh.refresh_portfolio_dependencies(cut)
        self.assertEqual([row.name for row in persist.call_args.args[1]], ['BOUND', 'REVIEW'])
        values = persist.call_args.args[2]
        self.assertTrue(all(value['portfolio_validation_status'] == 'Empresa del movimiento distinta a la del corte'
                            for value in values))
        self.assertEqual(rows[1].event_type, 'Ajuste')
        self.assertEqual(doc.employer, 'ORIGINAL')
        self.assertEqual(result['updated_source_rows'], 2)

    def test_force_refresh_bypasses_ordinary_save_cache(self):
        old = frappe._dict(name='ROW', loan_number='123', employer='E', portfolio_credit_status='Corriente')
        prior = frappe._dict(employer='E', detail_periods=[], detail_rows=[old])
        current = frappe._dict(prior, detail_rows=[frappe._dict(old)], get_doc_before_save=lambda: prior)
        with patch('credinomina_reconciliation.remittance_portfolio.allowed_employers', return_value={'E'}), \
             patch('credinomina_reconciliation.remittance_portfolio.load_cuts', return_value=[]) as cuts:
            update_detail_portfolio(current, force=True)
        cuts.assert_called_once_with(current, {'E'}, complete=True)
        self.assertEqual(current.detail_rows[0].portfolio_credit_status, 'No Identificado')

    def test_hidden_dependency_aborts_forced_refresh(self):
        doc = frappe._dict(detail_periods=[{'period': 'P'}])
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_list', return_value=[]), \
             patch.object(frappe, 'get_all', return_value=[frappe._dict(name='P')]), \
             patch.object(frappe, 'throw', side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                load_cuts(doc, {'E'}, complete=True)

    def test_deposits_using_next_cut_are_included_for_new_fallback_credits(self):
        cut = frappe._dict(name='CUT', report_date='2026-09-30')
        def pages(query, doctype, **kwargs):
            if doctype == 'CN Accounting Import':
                return [frappe._dict(historical_period='NEXT-PERIOD')]
            if doctype == 'CN Remittance Detail':
                return [frappe._dict(parent='DIRECT')]
            if doctype == 'CN Remittance Period':
                self.assertEqual(kwargs['filters']['period'], ['in', ['NEXT-PERIOD']])
                return [frappe._dict(parent='FALLBACK')]
            raise AssertionError(doctype)
        with patch.object(refresh, '_imports_for_cuts', return_value={'IMPORT'}) as imports, \
             patch.object(refresh, '_pages', side_effect=pages), \
             patch.object(refresh, 'child_records', return_value=[]), \
             patch.object(frappe, 'get_all', return_value=[frappe._dict(name='NEXT')]):
            self.assertEqual(refresh._related_deposits(cut, cut.report_date), {'DIRECT', 'FALLBACK'})
        imports.assert_called_once_with({'CUT', 'NEXT'})

    def test_unchanged_file_still_refreshes_related_records(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as importer
        doc = frappe._dict(name='CUT', file_hash=importer.file_sha256(b'file'), status='Importado',
            report_date='2026-09-30', rows=[frappe._dict(credit_number='1-1')],
            check_permission=Mock(), check_if_locked=Mock(), add_comment=Mock())
        result = dict(updated_accounting_imports=1, updated_source_rows=2, updated_deposits=1, updated_deposit_rows=3)
        with patch.object(importer, '_lock_portfolio_availability'), \
             patch.object(frappe, 'get_doc', return_value=doc), \
             patch.object(importer, '_attached_file', return_value=(frappe._dict(file_name='test.xlsx'), b'file')), \
             patch.object(importer, '_has_legacy_numeric_credit_numbers', return_value=False), \
             patch.object(importer, 'refresh_portfolio_dependencies', return_value=result) as update:
            returned = importer.import_portfolio_snapshot('CUT')
        update.assert_called_once_with(doc, doc.report_date)
        self.assertTrue(returned['unchanged'])
        self.assertEqual(returned['updated_deposit_rows'], 3)
        self.assertEqual(returned['snapshot_name'], 'CUT')
        doc.add_comment.assert_called_once()
