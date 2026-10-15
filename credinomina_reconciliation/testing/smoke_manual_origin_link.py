"""Real database coverage for explicit origin linking. Isolated site; always rollback."""
from hashlib import sha256
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import manual_origin_link as api
from credinomina_reconciliation.accounting_review import create_review_items
from credinomina_reconciliation.deposit_accounting_sources import source_writer, append_source
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute as control


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    tables = [api.DEPOSIT, api.ITEM, 'CN Deposit Accounting Source', 'File']
    before = {table: frappe.db.count(table) for table in tables}
    marker = 'ORIGIN-' + frappe.generate_hash(length=8)
    try:
        company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        bank = frappe.get_doc(dict(doctype='CN Bank Account', account_name=marker, bank_name='BAC',
            account_number=marker, currency='USD', active=1)).insert()
        file = save_file(marker + '.csv', b'Original accounting evidence', company.doctype, company.name, is_private=1)

        def receipt(ref, amount=100, confirmed=True, employer=None):
            doc = frappe.get_doc(dict(doctype=api.DEPOSIT, employer=employer or company.name,
                bank_account=bank.name, deposit_reference=ref, deposit_date='2026-10-13',
                deposit_voucher=marker + '-BANK',
                deposit_currency='USD', deposit_amount=amount)).insert()
            if confirmed:
                doc.submit()
            return doc.reload()

        def movements(doc, credits=(60, 40)):
            rows = [dict(accounting_source_key=sha256(f'{marker}-{doc.name}-{i}'.encode()).hexdigest(),
                event_type='Ajuste', accounting_classification='Movimiento interno', tmov='01', tdoc='01',
                classification_reason='Movimiento interno', source_account='1602', source_currency='USD',
                source_credit=credit, source_debit=0, amount_usd=credit, event_date='2026-10-13',
                voucher=f'{marker}-LEDGER-{i}', accounting_reference=doc.deposit_reference,
                source_row=i + 1, source_description=f'DEPOSITO {doc.deposit_reference} Cliente {i}',
                resolved_employer=doc.employer) for i, credit in enumerate(credits)]
            return create_review_items(rows, file.file_url, marker), rows

        def snapshot(doc):
            return {field: doc.get(field) for field in ('docstatus', 'deposit_amount', 'amount_usd',
                'deposit_date', 'deposit_reference', 'deposit_voucher', 'fx_rate', 'allocated_usd',
                'unallocated_usd', 'allocation_detail', 'result', 'financial_status')}

        def apply(doc, names, kind='internal'):
            preview = api.preview(doc.name, names, kind)
            return api.link(doc.name, names, preview['token'], 'Comprobante bancario verificado', kind)

        # Selection > 500, all original lines preserved once in the ledger.
        doc = receipt(marker + '-MASS', 565, confirmed=False)
        items, records = movements(doc, (1,) * 565)
        names = [item.name for item in items]
        assert len(api.candidates(doc.name, reference=doc.deposit_reference)) == 565
        saved = snapshot(doc)
        result = apply(doc, names)
        assert result['status'] == 'Vinculado' and result['count'] == 565, result
        doc.reload()
        assert snapshot(doc) == saved, (saved, snapshot(doc))
        assert len(doc.accounting_sources) == 565
        assert {source.source_voucher for source in doc.accounting_sources} == {row['voucher'] for row in records}
        assert api.link(doc.name, names, 'old-token', 'Retry')['already_linked']
        assert not create_review_items(records, file.file_url, marker)
        items[0].reload()
        assert items[0].origin_deposit == doc.name and financial_balance(items[0])['pending_usd'] == 0
        assert frappe.db.count('File', {'file_url': file.file_url, 'attached_to_name': doc.name}) == 1
        report = [row for row in control(dict(month='2026-10-01', employer=company.name))[1]
                  if row.get('remittance_allocation') == doc.name]
        assert len(report) == 565 and sum(row['credit_usd'] for row in report) == 565
        assert sum(row['pending_usd'] is not None for row in report) == 1
        # A linked item cannot become a second financial target through ordinary saves.
        frappe.db.savepoint('origin_tamper')
        try:
            items[0].origin_deposit = ''
            items[0].save()
        except frappe.ValidationError:
            frappe.db.rollback(save_point='origin_tamper')
        else:
            raise AssertionError('El vínculo pudo retirarse por edición normal')

        review = receipt(marker + '-REVIEW')
        choices, _ = movements(review, (99.99,))
        result = apply(review, [choices[0].name])
        review.reload()
        assert result['status'] == review.origin_link_status == 'Pendiente de revisión'
        assert not review.accounting_sources and not frappe.db.get_value(api.ITEM, choices[0].name, 'origin_deposit')
        assert '0.01' in review.origin_link_notes

        # Late accounting receipt on day 30 backs the day 13 manual cash.
        manual = receipt(marker + '-CORE')
        evidence = dict(accounting_source_key=marker + '-CORE-KEY', source_date='2026-10-30',
            source_row=1, source_voucher=marker, source_account='1602', source_currency='USD',
            source_credit=100, source_debit=0, source_file=file.file_url, source_file_hash=marker,
            accounting_reference=manual.deposit_reference, source_description='DEPOSITO DEL 13/10/2026',
            accounting_classification='Depósito', tmov='02', tdoc='12')
        with source_writer():
            core = frappe.get_doc(dict(doctype=api.DEPOSIT, employer=company.name,
                bank_account=bank.name, deposit_reference=manual.deposit_reference,
                deposit_date=manual.deposit_date, deposit_currency='USD', deposit_amount=100,
                cash_identity_status='Revisar identidad', **evidence))
            append_source(core, evidence)
            core.insert()
        saved = snapshot(manual)
        result = apply(manual, [core.name], 'deposit')
        manual.reload()
        core.reload()
        assert result['status'] == 'Vinculado' and core.consolidated_deposit == manual.name
        assert snapshot(manual) == saved and manual.accounting_sources[0].source_date.isoformat() == '2026-10-30'
        assert api.link(manual.name, [core.name], 'old', 'Retry', 'deposit')['already_linked']
        report = [row for row in control(dict(month='2026-10-01', employer=company.name))[1]
                  if row.get('remittance_allocation') == manual.name]
        assert len(report) == 1 and report[0]['credit_usd'] == 100

        # Actual reconciled targets in a closed period are unchanged.
        from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture
        def bank_receipt(employer, reference, amount):
            return receipt(reference, amount, employer=employer)
        with patch('credinomina_reconciliation.testing.smoke_receivable_recovery.new_deposit', bank_receipt):
            owner, period, _, _, closed = fixture(marker + '-CLOSED')
        frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado')
        closed.reload()
        saved, targets = snapshot(closed), [row.as_dict() for row in closed.targets]
        choices, _ = movements(closed, (50, 40))
        assert apply(closed, [item.name for item in choices])['status'] == 'Vinculado'
        closed.reload()
        assert snapshot(closed) == saved and [row.as_dict() for row in closed.targets] == targets
        assert frappe.db.get_value(period.doctype, period.name, 'status') == 'Cerrado'
        return dict(linked_565_lines_once=True, distinct_ledger_vouchers_preserved=True,
                    core_receipt_later_posting=True, reimport_idempotent=True,
                    differences_pending=True, closed_distribution_preserved=True, rollback=True)
    finally:
        frappe.db.rollback()
        assert before == {table: frappe.db.count(table) for table in tables}, 'El ensayo dejó registros'
