"""Undo can repair old overused cash without bypassing closure or credit guards."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.testing.smoke_period_cash_breakdown import _credit_fixture
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Client', 'CN Reconciliation Period', 'CN Accounting Import',
        'CN Source Row', 'CN Remittance Allocation', 'CN Complementary Item')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'UNDO-CASH-FLOOR-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD', historical_backfill=1,
                rows=[dict(event_type='Aplicacion', event_date='2025-04-30', currency='USD', amount=120,
                    amount_usd=120, effective=1, historical_period=period.name, source_key=marker,
                    client_number=marker, loan_number='99323-1')])).insert()
            engine._reconcile_sources(company.name)
            source.reload()
            paid = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker, deposit_currency='NIO',
                deposit_amount=3662.43, fx_rate=36.6243,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=100)])).insert()
            paid.submit(); reconcile_deposit(paid)
            other = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-05-01', deposit_reference=marker + '-OTHER', deposit_currency='USD',
                deposit_amount=20, targets=[dict(historical_application=source.rows[0].name, amount_usd=20)])).insert()
            other.submit(); reconcile_deposit(other)
            other_before = other.reload().as_dict()
            for keep_targets in (False, True):
                paid.reload()
                if not keep_targets:
                    paid.set('targets', []); paid.save()
                # Reproduce old persisted data created by the formerly allowed
                # FX edit: current bank equivalent 50, executed cash still 100.
                frappe.db.set_value(paid.doctype, paid.name, dict(fx_rate=73.2486, amount_usd=50), update_modified=False)
                paid.reload()
                assert paid.allocated_usd == 100 and paid.amount_usd == 50
                before = paid.as_dict()
                frappe.db.savepoint('normal_overused_cash_rejected')
                try:
                    reconcile_deposit(paid)
                except frappe.ValidationError as error:
                    assert 'efectivo ya distribuido o reservado' in str(error), str(error)
                    frappe.db.rollback(save_point='normal_overused_cash_rejected')
                else:
                    raise AssertionError('La conciliación normal aceptó efectivo insuficiente')
                assert paid.reload().as_dict() == before
                original_status = frappe.db.get_value(period.doctype, period.name, 'status')
                frappe.db.savepoint('closed_undo_rejected')
                frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado', update_modified=False)
                try:
                    reconcile_deposit(paid, undo=True)
                except frappe.ValidationError as error:
                    assert 'cerrado' in str(error).lower(), str(error)
                    frappe.db.rollback(save_point='closed_undo_rejected')
                else:
                    raise AssertionError('Se deshizo un período cerrado')
                assert frappe.db.get_value(period.doctype, period.name, 'status') == original_status
                assert paid.reload().as_dict() == before
                reconcile_deposit(paid, undo=True)
                paid.reload(); source.reload()
                assert (paid.deposit_amount, paid.fx_rate, paid.amount_usd) == (3662.43, 73.2486, 50)
                assert paid.allocated_usd == 0 and paid.unclassified_usd == 50 and paid.allocation_detail == '[]'
                assert source.rows[0].historical_remitted_usd == 20
                assert source.rows[0].historical_balance_usd == 100
                assert other.reload().as_dict() == other_before
                if not keep_targets:
                    # Re-establish the valid original deposit, then exercise the
                    # same legacy repair while its old planned target remains.
                    paid.fx_rate = 36.6243
                    paid.append('targets', dict(historical_application=source.rows[0].name, amount_usd=100))
                    paid.save(); reconcile_deposit(paid)
            seed_subcategories()
            fixture, origin = _credit_fixture(marker + '-CREDIT')
            credit = fixture[3]
            frappe.db.set_value(origin.doctype, origin.name,
                dict(deposit_amount=5, amount_usd=5), update_modified=False)
            reserved_before = origin.reload().as_dict()
            credit_before = credit.reload().as_dict()
            frappe.db.savepoint('reserved_undo_rejected')
            try:
                reconcile_deposit(origin, undo=True)
            except frappe.ValidationError as error:
                assert 'saldos a favor' in str(error).lower(), str(error)
                frappe.db.rollback(save_point='reserved_undo_rejected')
            else:
                raise AssertionError('Se liberó efectivo reservado como saldo a favor')
            assert origin.reload().as_dict() == reserved_before
            assert credit.reload().as_dict() == credit_before
            assert other.reload().as_dict() == other_before
            return dict(legacy_without_targets_repaired=True, legacy_with_overbudget_targets_repaired=True,
                bank_amount_and_fx_preserved=True, other_deposit_preserved=True,
                closed_period_undo_rejected=True, reserved_credit_undo_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
