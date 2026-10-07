"""Real SQL: recover, offset and reverse a deposit's transferred receivable.

Synthetic records, restricted to the isolated test site; run() always rolls back.
"""
import frappe

from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.complementary_compensation import reverse_compensation
from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.receivable_recovery import apply_recovery
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import create_complementary_item


def fixture(marker):
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
    seed_subcategories()
    company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
    period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
        payroll_month='2025-04-01', reconciliation_mode='Historica', historical_scope='Fecha exacta',
        historical_application_date='2025-04-30')).insert()
    imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
        currency='USD', status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(
            source_key=marker, event_type='Aplicacion', event_date='2025-04-30', currency='USD',
            amount=100, amount_usd=100, effective=1, client_number=marker, client_name=marker,
            loan_number=marker + '-1', historical_period=period.name, processing_route='Historica')])).insert()
    _reconcile_sources(company.name)
    deposit = new_deposit(company.name, marker, 90)
    values = dict(period=period.name, category='Cuenta por Cobrar a la Empresa',
        posting_date='2025-05-01', currency='USD', amount=-10,
        subcategory='Otro ajuste sin CxC', description='CxC de ensayo por depósito insuficiente')
    for amount in (0, 10):
        rejects(lambda: create_complementary_item(deposit.name, str(deposit.modified), values | {'amount': amount}))
    created = create_complementary_item(deposit.name, str(deposit.modified), values)
    item = frappe.get_doc('CN Complementary Item', created['name'])
    assert created['company_receivable'] and item.docstatus == 1
    assert item.category == 'Ajuste de conciliación' and item.subcategory_effect == 'CxC a la empresa'
    deposit.reload()
    assert len(deposit.targets) == 1 and deposit.targets[0].amount_usd == -10
    deposit.append('targets', dict(historical_application=imported.rows[0].name, amount_usd=100))
    deposit.save()
    reconcile_deposit(deposit)
    deposit.reload()
    assert deposit.result == 'Conciliado' and deposit.allocated_usd == 90, deposit.as_dict()
    assert get_balance(item.name)['company_receivable_usd'] == 10
    return company, period, imported, item, deposit


def new_deposit(company, reference, amount):
    deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company,
        deposit_reference=reference, deposit_date='2025-05-10', deposit_currency='USD', deposit_amount=amount)).insert()
    deposit.submit()
    return deposit


def credit_item(company, amount, marker):
    return frappe.get_doc(dict(doctype='CN Complementary Item', employer=company,
        category='Compensación entre partidas', review_action='Compensación entre partidas',
        posting_date='2025-05-01', currency='USD', amount=-amount, description=marker)).insert()


def rejects(action, text=None):
    frappe.db.savepoint('recovery_reject')
    try:
        action()
    except frappe.ValidationError as exc:
        frappe.db.rollback(save_point='recovery_reject')
        if text:
            assert text in str(exc), str(exc)
    else:
        raise AssertionError('La operación debía rechazarse')


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        marker = 'RECOVERY-' + frappe.generate_hash(length=8)
        company, period, imported, item, deposit = fixture(marker)
        from credinomina_reconciliation.application_context import load_application_context
        original_distribution = deposit.allocation_detail
        for field, value in [('effective', 0), ('match_status', 'Ignorado'),
                             ('match_status', 'Sin coincidencia'), ('historical_remitted_usd', 0),
                             ('historical_detail', '[]'), ('historical_balance_usd', 100),
                             ('remittance_allocation', deposit.name), ('complementary_item', item.name)]:
            imported.reload()
            imported.rows[0].set(field, value)
            rejects(lambda: imported.save(), 'dinero asignado')
            imported.reload()
            assert imported.rows[0].effective == 1
            assert imported.rows[0].historical_remitted_usd == 100
            assert len(load_application_context(company.name)[0]) == 1
        imported.status = 'Borrador'
        rejects(lambda: imported.save(), 'dinero asignado')
        imported.reload()
        assert imported.status in {'Importado', 'Importado con excepciones'}
        assert len(load_application_context(company.name)[0]) == 1
        deposit.reload()
        assert deposit.allocation_detail == original_distribution
        cash = new_deposit(company.name, marker + '-CASH', 3)
        args = (item.name, company.name, 'Depósito', cash.name, 3, '2025-05-10', 'Cobro parcial', 'a' * 32)
        result = apply_recovery(*args)
        assert apply_recovery(*args)['name'] == result['name']
        assert get_balance(item.name)['company_receivable_usd'] == 7
        cash.reload()
        assert cash.allocated_usd == 3
        receipt = frappe.get_doc('CN Complementary Item', result['name'])
        assert receipt.accounting_status == 'No requiere registro' and not receipt.accounting_source_key
        counterpart = credit_item(company.name, 4, marker)
        operation = 'b' * 32
        offset = apply_recovery(item.name, company.name, 'Compensación', counterpart.name,
            4, '2025-05-10', 'Compensación parcial', operation)
        assert get_balance(item.name)['company_receivable_usd'] == 3
        rejects(lambda: apply_recovery(item.name, company.name, 'Compensación', counterpart.name,
            4, '2025-05-10', 'Exceso', 'c' * 32), 'supera')
        reverse_compensation(offset['name'], operation, '2025-06-01', 'Corregir compensación', 'd' * 32)
        value = get_balance(item.name)
        assert value['company_receivable_usd'] == 7
        history = next(row for row in value['recoveries'] if row['name'] == offset['name'])
        assert history['effective_usd'] == 0 and history['financial_status'] == 'Revertida'
        # Even with an open period, used accounting evidence cannot disappear.
        rejects(lambda: frappe.delete_doc(imported.doctype, imported.name), 'dinero asignado')
        imported.reload()
        imported.rows = []
        rejects(lambda: imported.save(), 'dinero asignado')
        imported.reload()
        # A recovery cannot survive elimination of its original shortfall.
        deposit.reload()
        deposit.targets = []
        deposit.save()
        rejects(lambda: reconcile_deposit(deposit), 'sin su faltante original')
        deposit.reload()
        receipt.reload()
        receipt.cancel()
        value = get_balance(item.name)
        assert value['company_receivable_usd'] == 10
        history = next(row for row in value['recoveries'] if row['name'] == receipt.name)
        assert history['financial_status'] == 'Cancelada' and history['effective_usd'] == 0
        rejects(lambda: apply_recovery(*args), 'cancelado')
        imported.reload()
        assert imported.rows[0].amount_usd == 100
        return dict(original_cxc=10, after_cash=7, after_offset=3, after_offset_reversal=7,
            after_cash_cancel=10, idempotent=True, immutable_paid_origin=True,
            cancelled_and_reversed_history=True, registered_without_second_core_entry=True, rolled_back=True)
    finally:
        frappe.db.rollback()
