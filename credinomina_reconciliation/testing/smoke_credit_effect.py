"""Rollback-only SQL regression for refunds followed by accounting reclassification."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import client_credit, complementary_compensation as offsets, credit_effect_migration, credit_usage
from credinomina_reconciliation import compensation_effect
from credinomina_reconciliation.testing.smoke_client_credit import must_fail


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CREDIT-EFFECT-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name, client_name=marker, client_number=marker)).insert()
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2025-08-07', deposit_reference=marker, deposit_currency='USD', deposit_amount=19.25)).insert()
            deposit.submit()
            credit = frappe.get_doc(dict(doctype='CN Complementary Item', category=client_credit.CATEGORY,
                posting_date='2025-08-07', currency='USD', amount=19.25, employer=employer.name,
                registered_deposit=deposit.name, credit_client=client.name, credit_assigned_to='Administrator',
                credit_commitment_date='2025-09-30', credit_treatment='Pendiente de decisión',
                reason_type='Error de la empresa', description='Saldo a favor')).insert()
            credit.submit()
            ledger = frappe.get_doc(dict(doctype='CN Complementary Item', category='Por clasificar',
                posting_date='2025-11-21', currency='USD', amount=19.25, employer=employer.name,
                description='Reclasificación 3004 a 3001', voucher=marker, voucher_line=marker,
                accounting_source_key=marker, source_account='160209013004', source_currency='NIO',
                source_debit=704.89, source_credit=0, source_fx_rate=36.6243, source_voucher=marker,
                source_date='2025-11-21', source_file='/private/files/test-credit-effect.xlsx',
                source_file_hash=marker, source_row=20936, source_client_name=client.client_name,
                accounting_classification='Movimiento interno')).insert()
            concept = f'C$704.89 TRANSFERENCIA A/F DE {client.client_name} EN CONCEPTO DE REEMBOLSO DE CREDITO CANCELADO'
            reason = 'FECHA APLIC.: 20/08/2025\nCONCEPTO: ' + concept + '\nCuenta contable afectada: 260199013001'
            preview = offsets.preview_compensation(credit.name, ledger.name)
            offsets.confirm_compensation(credit.name, ledger.name, 19.25, '2025-11-21', reason, preview['request_key'])
            credit.reload(); ledger.reload(); deposit.reload()
            frozen_deposit = deposit.as_dict()
            # Reconstruct the pre-change record: a documented refund and an old
            # accounting-only offset, before any effect field existed.
            refund = dict(operation_id='REFUND-' + marker, tratamiento='Devolución', importe_usd=19.25,
                fecha='2025-08-20', referencia='001011316', soporte='/private/files/Comprobante_001011316.pdf',
                observaciones='Realizado el 20/08/2025\nCONCEPTO:\n' + concept)
            frozen_history = json.dumps([refund], ensure_ascii=False)
            frappe.db.set_value(credit.doctype, credit.name, dict(credit_history=frozen_history,
                credit_resolved_usd=19.25, credit_pending_usd=0, credit_management_status='Resuelto'))
            frozen_rows = {}
            for doc in (credit, ledger):
                row = doc.compensations[0]
                frozen_rows[doc.name] = (row.name, row.operation_id, row.compensation_date, row.amount_usd, row.reason)
                frappe.db.set_value('CN Complementary Offset', row.name, 'credit_effect', '')
            credit_effect_migration.execute()
            credit_effect_migration.execute()
            credit.reload(); ledger.reload(); deposit.reload()
            assert not credit.credit_usage_issue and credit.credit_pending_usd == 0 and credit.credit_resolved_usd == 19.25
            assert credit.credit_history == frozen_history and deposit.as_dict() == frozen_deposit
            for doc in (credit, ledger):
                row = doc.compensations[0]
                assert (row.name, row.operation_id, row.compensation_date, row.amount_usd, row.reason) == frozen_rows[doc.name]
                assert row.credit_effect == credit_usage.DOCUMENTARY and row.credit_management_entry == refund['operation_id']
                assert row.credit_management_item == credit.name and doc.compensated_usd == 19.25
            from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute
            for day, expected in [('2025-08-19', -19.25), ('2025-08-20', 0), ('2025-11-21', 0), ('2026-08-31', 0)]:
                rows = execute(dict(employer=employer.name, cutoff_date=day, view_mode='Detalle'))[1]
                amounts = [row['balance_usd'] for row in rows if row['source_document'] == credit.name]
                assert amounts == ([expected] if expected else []), (day, rows)
            must_fail(lambda: client_credit.reverse_management(credit.name, str(credit.modified), refund['operation_id'], '2025-12-01', 'Devolución revertida'))
            reversal = offsets.get_reversible_compensations(credit.name)
            offsets.reverse_compensation(credit.name, preview['request_key'], '2025-11-22', 'Retirar vínculo', reversal['request_key'])
            credit.reload()
            assert credit.credit_pending_usd == 0
            proposal = offsets.preview_compensation(credit.name, ledger.name, credit_usage.DOCUMENTARY, refund['operation_id'])
            assert proposal['suggested_usd'] == 19.25
            args = (credit.name, ledger.name, 19.25, '2025-11-23', 'Vincular devolución', proposal['request_key'], credit_usage.DOCUMENTARY, refund['operation_id'])
            offsets.confirm_compensation(*args)
            offsets.confirm_compensation(*args)
            credit.reload(); ledger.reload()
            assert credit.credit_pending_usd == 0 and credit.credit_history == frozen_history
            correction = offsets.get_date_correction_candidates(credit.name)
            offsets.correct_compensation_date(credit.name, proposal['request_key'], '2025-11-24', 'Fecha correcta', correction['request_key'])
            credit.reload(); ledger.reload()
            assert credit.credit_pending_usd == 0 and len(credit.compensations) == 5
            assert all(row.credit_effect == credit_usage.DOCUMENTARY for row in credit.compensations)
            # A legacy case lacking sufficient evidence remains reviewable and
            # unavailable, but no longer blocks migration of the entire site.
            for doc in (credit, ledger):
                for row in doc.compensations:
                    if row.operation_id != proposal['request_key'] and row.corrects_operation_id != proposal['request_key']:
                        continue
                    frappe.db.set_value('CN Complementary Offset', row.name, dict(
                        credit_effect='', credit_management_item='', credit_management_entry='', reason='Sin referencia suficiente'))
            credit_effect_migration.execute()
            credit.reload()
            assert credit.credit_usage_issue and credit.credit_pending_usd == 0
            must_fail(lambda: credit_usage.totals(credit))
            report = execute(dict(employer=employer.name, cutoff_date='2026-08-31', view_mode='Detalle'))[1]
            assert any(row['source_document'] == credit.name and row['balance_usd'] is None for row in report), report
            # Classify the remaining active operation explicitly, retaining the
            # original/reversal pair and the real refund unchanged.
            plan = compensation_effect.preview(credit.name, proposal['request_key'], credit_usage.DOCUMENTARY, refund['operation_id'])
            must_fail(lambda: compensation_effect.classify(credit.name, proposal['request_key'], credit_usage.DOCUMENTARY,
                'stale-token', 'Vista previa anterior', refund['operation_id']))
            compensation_effect.classify(credit.name, proposal['request_key'], credit_usage.DOCUMENTARY,
                plan['token'], 'Comprobante de devolución verificado', refund['operation_id'])
            credit.reload(); ledger.reload(); deposit.reload()
            assert not credit.credit_usage_issue and credit.credit_resolved_usd == 19.25 and credit.credit_pending_usd == 0
            assert credit.credit_history == frozen_history and len(credit.compensations) == 5
            assert deposit.as_dict() == frozen_deposit
            return dict(refund_and_reclassification_count_once=True, cutoffs=True, preserved_journals=True,
                documentary_reverse=True, creation_and_retry=True, ambiguous_migration=True, manual_classification=True)
    finally:
        frappe.db.rollback()
