"""Synthetic transfer fixtures; never run against production, always rollback."""
import frappe
from credinomina_reconciliation import provisional_adjustments as provisional
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo en el sitio aislado de pruebas.')
    frappe.set_user('Administrator')
    results = []
    try:
        from credinomina_reconciliation.complementary_subcategories import seed_subcategories
        seed_subcategories()
        for category, base, applied, deduction, reuse, other_detail in [
            ('', 100, 100, False, False, None), ('Otros ingresos', 100, 90, False, False, None),
            ('Otros ingresos', 100, 90, True, False, None),
            ('Saldo a favor del cliente', 100, 90, False, False, None),
            ('Saldo a favor de la empresa', 100, 90, True, False, None),
            ('Ajuste de conciliación', 90, 100, True, False, None),
            ('Otros ingresos', 100, 90, True, True, None),
            ('Otros ingresos', 100, 90, False, False, 80),
            ('Otros ingresos', 80, 70, True, False, 100),
        ]:
            marker = 'prep-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker, payroll_frequency='Mensual')).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
                client_name='Cliente preparado ' + marker, client_number=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual',
                application_basis='Detalle de empresa' if deduction else 'Cobranza',
                collection_rows=[dict(row_key=marker, source_row=2, client=client.name,
                    client_number=client.client_number, client_name=client.client_name, loan_number=marker+'-1',
                    expected_usd=other_detail if deduction and other_detail is not None else base,
                    expected_nio=(other_detail if deduction and other_detail is not None else base)*36.6243,
                    application_reference=marker,
                    deducted_usd=base if deduction else (other_detail or 0),
                    deduction_status='Deduccion parcial' if other_detail is not None else (
                        'Deduccion total' if deduction else 'Pendiente de detalle'))])).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                status='Importado', source_file='/private/files/'+marker+'.csv',
                rows=[dict(source_row=2, source_key=marker, event_type='Aplicacion', event_date='2026-09-30',
                    client_number=client.client_number, client_name=client.client_name, loan_number=marker+'-1',
                    employer_text=employer.name, currency='USD', amount=applied, amount_usd=applied,
                    reference=marker, voucher=marker, processing_route='Operativa', effective=1,
                    match_status='Pendiente')])).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            period.reload()
            assert period.collection_rows[0].applied_usd == applied, period.as_dict()
            provisional.generate_proposals(period.name)
            period.reload()
            ledger = None
            if reuse:
                ledger = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
                    period=period.name, category=category, currency='USD', amount=base-applied,
                    posting_date='2026-10-05', client_number=client.client_number, loan_number=marker+'-1',
                    reference=marker, description='Ingreso registrado en core', voucher=marker, voucher_line=marker,
                    accounting_source_key=marker, source_account='160209013004', source_currency='USD',
                    source_debit=base-applied, source_credit=0, source_fx_rate=1, source_date='2026-10-05',
                    source_voucher=marker, source_file='/private/files/'+marker+'.xlsx', source_file_hash=marker,
                    source_row=2, accounting_classification='Movimiento interno', review_action='Partida de depósito',
                    review_notes='Clasificado como ingreso', amount_reviewed=1))
                ledger.flags.defer_reconciliation = True
                ledger.insert(); ledger.submit()
            if category:
                proposal = period.provisional_adjustments[0]
                proposal.category = category
                if category == 'Ajuste de conciliación':
                    proposal.subcategory = 'CxC a la empresa'
                proposal.description = 'Diferencia revisada en prueba'
                proposal.reason_type = 'Error de la empresa'
                proposal.assigned_to = 'Administrator'
                proposal.commitment_date = '2026-11-01'
                if ledger:
                    proposal.existing_item = ledger.name
                period.save()
                provisional.approve_proposals(period.name)
            assert frappe.db.count('CN Complementary Item', {'employer': employer.name}) == int(reuse)
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference='BANK-'+marker, deposit_currency='NIO', deposit_amount=base*36.6243,
                fx_rate=36.6243, deposit_date='2026-10-05', detail_periods=[dict(period=period.name)])).insert()
            deposit.submit()
            preview = provisional.preview_transfer(deposit.name)
            try:
                result = provisional.apply_transfer(deposit.name, preview['fingerprint'], True)
            except Exception:
                deposit.reload()
                print('Transfer failure', category, deposit.result, deposit.detail_status,
                      [(r.result, r.amount_usd, r.detail_row) for r in deposit.targets],
                      [(r.match_status, r.match_reason) for r in deposit.detail_rows], flush=True)
                raise
            deposit.reload(); period.reload()
            assert result['detail_pending'] == 0, result
            assert deposit.unclassified_usd == 0, deposit.as_dict()
            assert period.prepared_deposit == deposit.name
            count = frappe.db.count('CN Complementary Item', {'employer': employer.name})
            assert provisional.apply_transfer(deposit.name, preview['fingerprint'], True)['already_applied']
            assert frappe.db.count('CN Complementary Item', {'employer': employer.name}) == count
            if category:
                item = frappe.get_doc('CN Complementary Item', period.provisional_adjustments[0].complementary_item)
                assert item.docstatus == 1 and item.amount_usd == base - applied
                if category.startswith('Saldo a favor'):
                    assert item.credit_pending_usd == 10 and item.result == 'Saldo a favor documentado'
                if category == 'Ajuste de conciliación':
                    from credinomina_reconciliation.receivable_recovery import position
                    assert position(item)[0][0]['receivable_usd'] == 10, position(item)
                if reuse:
                    assert item.name == ledger.name and count == 1
            _reconcile_sources(employer.name, preserve_deposits=True)
            period.reload(); deposit.reload()
            assert period.applied_total_usd == applied and period.pending_usd == 0, period.as_dict()
            assert deposit.unclassified_usd == 0
            if other_detail is not None:
                assert period.collection_rows[0].expected_usd == (other_detail if deduction else base)
                assert period.collection_rows[0].deducted_usd == (base if deduction else other_detail)
            results.append(dict(category=category or 'Sin diferencias', deduction=deduction,
                                reused=reuse, result=deposit.result, detail=deposit.detail_status))
        return results
    finally:
        frappe.db.rollback()
