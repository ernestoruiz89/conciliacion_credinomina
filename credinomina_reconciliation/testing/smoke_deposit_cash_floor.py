"""Submitted FX edits preserve already executed cash, even without targets."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Reconciliation Period', 'CN Accounting Import',
        'CN Source Row', 'CN Remittance Allocation')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CASH-FLOOR-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD',
                historical_backfill=1, rows=[dict(event_type='Aplicacion', event_date='2025-04-30',
                    currency='USD', amount=100, amount_usd=100, effective=1,
                    historical_period=period.name, source_key=marker, client_number=marker,
                    loan_number='99321-1')])).insert()
            engine._reconcile_sources(company.name)
            source.reload()
            paid = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker + '-PAID', deposit_currency='NIO',
                deposit_amount=3662.43, fx_rate=36.6243,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=100)])).insert()
            paid.submit()
            reconcile_deposit(paid)
            paid.reload(); source.reload(); period.reload()
            assert paid.allocated_usd == 100 and source.rows[0].historical_remitted_usd == 100
            cash_journal = paid.allocation_detail
            # A plain edit may remove planned targets, but must retain actual
            # execution. Its cash remains protected independently of that plan.
            paid.set('targets', [])
            paid.save(); paid.reload()
            assert paid.allocated_usd == 100 and paid.allocation_detail == cash_journal
            paid_before = paid.as_dict()
            source_before, period_before = source.as_dict(), period.as_dict()
            frappe.db.savepoint('cash_floor_rejected')
            paid.fx_rate = 73.2486  # Same native cash would now be only USD 50.
            try:
                paid.save()
            except frappe.ValidationError as error:
                assert 'efectivo ya distribuido o reservado' in str(error), str(error)
                frappe.db.rollback(save_point='cash_floor_rejected')
            else:
                raise AssertionError('Se redujo el efectivo por debajo de su distribución confirmada')
            assert paid.reload().as_dict() == paid_before
            assert source.reload().as_dict() == source_before
            assert period.reload().as_dict() == period_before
            unpaid = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker + '-UNPAID', deposit_currency='NIO',
                deposit_amount=3662.43, fx_rate=36.6243)).insert()
            unpaid.submit()
            unpaid.fx_rate = 73.2486
            unpaid.save(); unpaid.reload()
            assert unpaid.amount_usd == 50 and unpaid.allocated_usd == 0
            assert paid.reload().as_dict() == paid_before
            # Cancellation re-enters the same transaction's cash pool while
            # Frappe performs its normal submitted -> cancelled transition.
            paid.append('targets', dict(historical_application=source.rows[0].name, amount_usd=100))
            paid.save()
            unpaid.cancel()
            assert unpaid.reload().docstatus == 2
            assert paid.reload().allocated_usd == 100 and paid.allocation_detail == cash_journal
            assert source.reload().rows[0].historical_remitted_usd == 100
            return dict(removed_targets_do_not_release_executed_cash=True,
                lower_usd_from_fx_rejected=True, unpaid_fx_correction_allowed=True,
                submit_and_cancel_keep_other_cash=True,
                deposits_source_links_and_period_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
