"""Real report queries against deliberately stale projections; always rollback."""
import json

import frappe


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
    from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as transactions
    from credinomina_reconciliation.company_statement import load_detail, summarize
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.testing.smoke_receivable_recovery import new_deposit

    doctypes = ('CN Employer', 'CN Client', 'CN Reconciliation Period', 'CN Accounting Import',
                'CN Source Row', 'CN Remittance Allocation', 'CN Complementary Item')
    before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        frappe.set_user('Administrator')
        marker = 'PRIORITY-' + frappe.generate_hash(length=8)
        company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
            payroll_month='2025-04-01', reconciliation_mode='Historica', historical_scope='Fecha exacta',
            historical_application_date='2025-04-30')).insert()
        imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name, currency='USD',
            status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(source_key=marker,
                event_type='Aplicacion', event_date='2025-04-30', currency='USD', amount=100,
                amount_usd=100, effective=1, client_number=marker, client_name=marker,
                loan_number=marker + '-1', historical_period=period.name, processing_route='Historica')])).insert()
        _reconcile_sources(company.name)
        deposit = new_deposit(company.name, marker, 80)
        deposit.append('targets', dict(historical_application=imported.rows[0].name, amount_usd=80))
        deposit.save()
        reconcile_deposit(deposit)
        deposit.reload()
        source = frappe.get_doc('CN Source Row', imported.rows[0].name)
        assert source.historical_remitted_usd == 80

        # The real distribution is US$80. Only its saved label is stale.
        frappe.db.set_value('CN Source Row', source.name, 'deposit_match_status', 'Depósito conciliado', update_modified=False)
        matrix = transactions.execute(dict(year=2025, employer=company.name))
        assert matrix[1][0]['m04_state'] == 'Parcial' and matrix[1][0]['m04_percentage'] == 80
        assert matrix[4][0]['value'] == 0 and matrix[4][1]['value'] == 1
        detail = transactions.get_month_detail(2025, 4, company.name, state='Parcial')
        assert detail['filtered_count'] == 1
        assert detail['totals']['assigned_usd'] == 80 and detail['totals']['pending_usd'] == 20
        assert transactions.get_month_detail(2025, 4, company.name, state='Conciliado')['filtered_count'] == 0
        assert frappe.db.get_value('CN Source Row', source.name, 'deposit_match_status') == 'Depósito conciliado'

        core = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
            category='Por clasificar', review_action='Pendiente de revisión', posting_date='2025-04-30',
            currency='USD', amount=100, description=marker, accounting_source_key=marker,
            source_date='2025-04-30', source_currency='USD', source_debit=100, source_credit=0,
            source_voucher='AS-PRIORITY', source_description='Evidencia original de ensayo')).insert()
        frappe.db.set_value('CN Complementary Item', core.name, dict(category='Otros ingresos', docstatus=1), update_modified=False)
        journal = json.loads(deposit.allocation_detail)
        journal.append(dict(tipo='Partida complementaria', partida=core.name,
                            importe_usd=110, empresa=company.name))
        frappe.db.set_value('CN Remittance Allocation', deposit.name, 'allocation_detail', json.dumps(journal), update_modified=False)
        rows = load_detail(dict(employer=company.name, component='core_pending_usd'))
        row = next(row for row in rows if row['source_document'] == core.name)
        assert row['status'] == 'Revisar distribución' and row['observed_usd'] == 10
        assert row['core_pending_usd'] is None and summarize(rows)[0]['balance_usd'] is None
        assert frappe.db.get_value('CN Complementary Item', core.name, 'amount_usd') == 100
    finally:
        frappe.db.rollback()
        after = {doctype: frappe.db.count(doctype) for doctype in doctypes}
        assert before == after, (before, after)
    return dict(application_coverage_usd=80, application_pending_usd=20,
                inconsistent_core_net=None, observed_usd=10, rollback=True)
