"""Rollback-only recovery: generic companies and real existing role permissions."""
import frappe

from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.receivable_recovery import apply_recovery, preview_recovery
from credinomina_reconciliation.testing.smoke_receivable_recovery import new_deposit, rejects


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        marker = 'RECOVERY-SCOPE-' + frappe.generate_hash(length=8)
        companies, applications = [], []
        for suffix in ('A', 'B'):
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + suffix,
                employer_code=marker + suffix)).insert()
            companies.append(company.name)
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica',
                historical_scope='Fecha exacta', historical_application_date='2025-04-30')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                currency='USD', status='Importado', source_file=f'/private/files/{marker}{suffix}.csv',
                rows=[dict(source_key=marker + suffix, event_type='Aplicacion', event_date='2025-04-30',
                    currency='USD', amount=100, amount_usd=100, effective=1, client_name=marker + suffix,
                    client_number=marker + suffix, loan_number=marker + suffix + '-1',
                    historical_period=period.name, processing_route='Historica')])).insert()
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
            _reconcile_sources(company.name)
            applications.append(source.rows[0].name)
        a, b = companies
        item = frappe.get_doc(dict(doctype='CN Complementary Item', employer=a,
            category='Ajuste de conciliación', subcategory='CxC a la empresa',
            posting_date='2025-05-01', currency='USD', amount=-30, reference=marker,
            generic_distribution=1, description='CxC genérica de dos empresas',
            distribution_companies=[dict(employer=b)]))
        item.flags.defer_reconciliation = True
        item.insert(); item.submit()
        originals = []
        for company, application, shortage in zip(companies, applications, (10, 20)):
            deposit = new_deposit(company, marker + company[-1], 100 - shortage)
            deposit.append('targets', dict(historical_application=application, amount_usd=100))
            deposit.append('targets', dict(complementary_item=item.name, employer=company,
                amount_usd=-shortage))
            deposit.save(); reconcile_deposit(deposit); deposit.reload()
            assert deposit.result == 'Conciliado' and deposit.allocated_usd == 100 - shortage
            originals.append(deposit.allocation_detail)
        assert {row['employer']: row['receivable_usd'] for row in preview_recovery(item.name)['companies']} == {a: 10, b: 20}
        cash_a, cash_b = new_deposit(a, marker + '-CASH-A', 15), new_deposit(b, marker + '-CASH-B', 20)
        rejects(lambda: apply_recovery(item.name, a, 'Depósito', cash_a.name, 11,
            '2025-05-10', 'No usar la deuda de B', '1' * 32), 'supera')
        rejects(lambda: apply_recovery(item.name, a, 'Depósito', cash_b.name, 5,
            '2025-05-10', 'Pagador no autorizado', '2' * 32), 'pueda pagar')
        first = apply_recovery(item.name, a, 'Depósito', cash_a.name, 7,
            '2025-05-10', 'Cobro de A', '3' * 32)
        assert {row['employer']: row['receivable_usd'] for row in preview_recovery(item.name)['companies']} == {a: 3, b: 20}
        second = apply_recovery(item.name, b, 'Depósito', cash_b.name, 12,
            '2025-05-10', 'Cobro de B', '4' * 32)
        assert get_balance(item.name)['company_receivable_usd'] == 11
        frappe.get_doc('CN Complementary Item', first['name']).cancel()
        assert {row['employer']: row['receivable_usd'] for row in preview_recovery(item.name)['companies']} == {a: 10, b: 8}
        cash_a.reload()
        assert any(row.complementary_item == first['name'] for row in cash_a.targets)
        rejects(lambda: apply_recovery(item.name, a, 'Depósito', cash_a.name, 1,
            '2025-05-10', 'Primero corregir destinos cancelados', '7' * 32), 'retire o corrija')
        assert not frappe.db.exists('CN Complementary Item', {'receivable_operation': '7' * 32})
        # Explicit operator correction, not silent deletion during cancellation.
        cash_a.reload()
        cash_a.targets = [row for row in cash_a.targets if row.complementary_item != first['name']]
        cash_a.save()
        assert frappe.db.get_value('CN Complementary Item', first['name'], 'docstatus') == 2
        users = {}
        for role in ('Operador Credinomina', 'Supervisor Credinomina'):
            email = marker.lower() + ('-op' if role.startswith('Operador') else '-sup') + '@test.invalid'
            user = frappe.get_doc(dict(doctype='User', email=email, first_name=marker,
                user_type='System User', enabled=1, send_welcome_email=0, roles=[dict(role=role)]))
            user.flags.no_welcome_mail = True
            user.insert()
            users[role] = email
        role_results = {}
        for role, email in users.items():
            frappe.set_user(email)
            assert preview_recovery(item.name)['companies']
            assert frappe.has_permission('CN Accounting Import', 'write')
            assert frappe.has_permission('CN Complementary Item', 'submit') == role.startswith('Supervisor')
            role_results[role] = dict(read=True, write=True, submit=role.startswith('Supervisor'))
            if role.startswith('Operador'):
                try:
                    apply_recovery(item.name, a, 'Depósito', cash_a.name, 1,
                        '2025-05-10', 'Operador no confirma cobros', '5' * 32)
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError('El operador confirmó un cobro sin permiso submit')
                assert not frappe.db.exists('CN Complementary Item', {'receivable_operation': '5' * 32})
        frappe.set_user(users['Supervisor Credinomina'])
        apply_recovery(item.name, a, 'Depósito', cash_a.name, 1,
            '2025-05-10', 'Supervisor confirma cobro', '6' * 32)
        frappe.set_user('Administrator')
        assert get_balance(item.name)['company_receivable_usd'] == 17
        return dict(generic_cxc_split_correct=True, no_cross_company_netting=True,
            unauthorized_payer_rejected=True, reversal_keeps_other_company_payment=True,
            canceled_destination_explained_before_writes=True,
            roles=role_results, existing_permissions_unchanged=True, rolled_back=True)
    finally:
        frappe.set_user('Administrator')
        frappe.db.rollback()
