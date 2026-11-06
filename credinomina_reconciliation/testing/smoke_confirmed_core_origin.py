"""Rollback-only: a confirmed core origin is evidence, never a second receipt."""
from hashlib import sha256
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import manual_origin_link as api
from credinomina_reconciliation.deposit_accounting_sources import source_writer, append_source, assert_usable_cash
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money


def financial_state(document):
    from credinomina_reconciliation.deposit_accounting_sources import IDENTITY_FIELDS
    values = document.as_dict()
    for field in (*IDENTITY_FIELDS, 'accounting_sources', 'modified', 'modified_by'):
        values.pop(field, None)
    return values


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'), \
                patch('frappe.model.rename_doc.sync_user_settings'):
            marker = 'CONFIRMED-CORE-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            bank = frappe.get_doc(dict(doctype='CN Bank Account', account_name=marker, bank_name='BAC',
                account_number=marker, currency='NIO', active=1)).insert()
            file = save_file(marker + '.csv', b'Original evidence', company.doctype, company.name, is_private=1)
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2026-08-01', reconciliation_mode='Historica')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                historical_backfill=1, historical_period=period.name, status='Importado', currency='USD',
                source_file=file.file_url, rows=[dict(source_key=marker, event_type='Aplicacion',
                    event_date='2026-08-13', currency='USD', amount=269.61, amount_usd=269.61, effective=1,
                    client_number=marker, client_name='Cliente de prueba', loan_number=marker,
                    processing_route='Historica', historical_period=period.name)])).insert()
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
            _reconcile_sources(company.name, preserve_deposits=True)
            manual = frappe.get_doc(dict(doctype=api.DEPOSIT, employer=company.name, bank_account=bank.name,
                deposit_date='2026-10-23', deposit_reference=marker, deposit_currency='NIO', deposit_amount=11696.37,
                fx_rate=36.6243, allow_manual_other_periods=1,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=269.61)])).insert()
            manual.submit()
            reconcile_deposit(manual)
            assert money(manual.reload().allocated_usd) == money(269.61)
            assert money(manual.unallocated_usd) == money(49.75)
            key = sha256(marker.encode()).hexdigest()
            evidence = dict(accounting_source_key=key, source_date='2026-10-23', source_row=1712,
                source_voucher='001014917', source_account='160209013004', source_currency='NIO',
                source_credit=11696.37, source_debit=0, source_fx_rate=36.6243, source_file=file.file_url,
                source_file_hash=key, accounting_reference=marker, source_description='Depósito del core de prueba',
                accounting_classification='Depósito', tmov='02', tdoc='12')
            with source_writer():
                core = frappe.get_doc(dict(doctype=api.DEPOSIT, employer=company.name, bank_account=bank.name,
                    deposit_date='2026-10-23', deposit_reference=marker, deposit_voucher='001014917',
                    deposit_currency='NIO', deposit_amount=11696.37, fx_rate=36.6243, **evidence))
                append_source(core, evidence)
                core.insert()
            core.submit()
            core.reload()
            assert core.docstatus == 1 and not money(core.allocated_usd)
            before = financial_state(manual)
            source_before, period_before = document_state(source.reload()), document_state(period.reload())
            preserved_core = {field: core.get(field) for field in ('docstatus', 'amount_usd', 'deposit_amount',
                'fx_rate', 'deposit_date', 'deposit_reference', 'allocated_usd', 'allocation_detail', 'result')}
            assert core.name in {row.name for row in api.candidates(manual.name, 'deposit', manual.deposit_reference)}
            preview = api.preview(manual.name, [core.name], 'deposit')
            assert not preview['issues'], preview
            count = frappe.db.count('CN Deposit Accounting Source')
            with patch('credinomina_reconciliation.deposit_accounting_sources.refresh_linked_origin_status', side_effect=RuntimeError('Injected failure')):
                try:
                    api.link(manual.name, [core.name], preview['token'], 'Mismo comprobante', 'deposit')
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Failure not injected')
            assert frappe.db.count('CN Deposit Accounting Source') == count
            assert not core.reload().consolidated_deposit
            assert financial_state(manual.reload()) == before
            preview = api.preview(manual.name, [core.name], 'deposit')
            result = api.link(manual.name, [core.name], preview['token'], 'Mismo comprobante', 'deposit')
            assert result['status'] == 'Vinculado'
            assert core.reload().consolidated_deposit == manual.name
            assert core.financial_status == 'Origen contable vinculado'
            assert all(core.get(field) == value for field, value in preserved_core.items())
            assert financial_state(manual.reload()) == before
            for _ in range(2):
                assert api.link(manual.name, [core.name], 'old-token', 'Retry', 'deposit')['already_linked']
                reconcile_deposit(manual)
                assert financial_state(manual.reload()) == before
                assert document_state(source.reload()) == source_before
                assert document_state(period.reload()) == period_before
            _reconcile_sources(company.name, preserve_deposits=True)
            assert financial_state(manual.reload()) == before
            assert document_state(source.reload()) == source_before
            assert core.reload().docstatus == 1 and core.financial_status == 'Origen contable vinculado'
            from credinomina_reconciliation.control_deposits import get_cash_deposits
            cash = get_cash_deposits(2026, company.name)
            assert len(cash) == 1 and cash[0]['name'] == manual.name, cash
            from credinomina_reconciliation.company_statement import build_detail, summarize
            statement = summarize(build_detail([], [], [], [manual.as_dict(), core.as_dict()]))
            assert money(statement[0]['deposit_pending_usd']) == money(-49.75), statement
            try:
                assert_usable_cash(core)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError('Linked confirmed core reused as cash')
            assert core.name not in {row.name for row in api.candidates(manual.name, 'deposit', manual.deposit_reference)}
            return dict(confirmed_origin_retained=True, assigned_usd=269.61, pending_usd=49.75,
                cash_counted_once=True, repeated_without_duplicates=True, failure_rolled_back=True, rollback=True)
    finally:
        frappe.db.rollback()
