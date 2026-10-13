"""Verify real report queries with inconsistent evidence; isolated site, rollback."""
import io
import json
from datetime import datetime

import frappe
from openpyxl import load_workbook


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado cn-reconciliation-test.local')
    from credinomina_reconciliation.application_context import load_application_context
    from credinomina_reconciliation.company_statement import load_detail, summarize
    from credinomina_reconciliation.complementary_balances import get_balance
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    from credinomina_reconciliation.control_export import build_control_workbook
    from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as aging
    from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa import antiguedad_de_saldos_por_empresa as grouped
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement
    from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as transactions
    from credinomina_reconciliation.testing.smoke_receivable_recovery import new_deposit

    doctypes = ('CN Employer', 'CN Client', 'CN Reconciliation Period', 'CN Accounting Import',
                'CN Source Row', 'CN Collection Row', 'CN Remittance Allocation', 'CN Complementary Item')
    before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        frappe.set_user('Administrator')
        marker = 'INTEGRITY-' + frappe.generate_hash(length=8)
        employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name, client_number=marker, client_name=marker)).insert()
        period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
            payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual',
            collection_rows=[dict(client=client.name, client_name=client.client_name, row_key=marker,
                                 loan_number=marker+'-1', expected_usd=100)])).insert()
        imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
            currency='USD', status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(
                source_key=marker, source_row=2, event_type='Aplicacion', event_date='2026-09-30',
                currency='USD', amount=100, amount_usd=100, effective=1, client_number=client.name,
                client_name=client.client_name, loan_number=marker+'-1', processing_route='Operativa')])).insert()
        claim = period.collection_rows[0].name
        frappe.db.set_value('CN Collection Row', claim, 'applied_usd', 150, update_modified=False)
        frappe.db.set_value('CN Source Row', imported.rows[0].name, dict(match_status='Conciliado',
            collection_row_id=claim, application_allocation_detail=json.dumps([dict(collection_row_id=claim,amount_usd=150)])), update_modified=False)
        context = load_application_context(employer.name)
        assert context[3][claim]['applied_usd'] == 150
        detail = load_detail(dict(employer=employer.name, component='application_pending_usd'))
        assert len(detail) == 1 and detail[0]['application_pending_usd'] is None
        assert detail[0]['status'] == 'Revisar distribución'
        assert summarize(detail)[0]['balance_usd'] is None
        assert transactions.get_month_detail(2026,9,employer.name,state='Revisar distribución')['filtered_count'] == 1
        aged = aging.execute(dict(employer=employer.name, as_of_date='2026-10-12', balance_type='Aplicado pendiente de depósito'))
        assert aged[1][0]['applied_usd'] == 100 and aged[1][0]['amount_usd'] is None
        assert aged[4][0]['value'] == 'N/D'
        aggregated = grouped.execute(dict(employer=employer.name, as_of_date='2026-10-12', balance_type='Aplicado pendiente de depósito'))
        assert aggregated[1][0]['amount_usd'] is None and aggregated[1][0]['known_amount_usd'] == 0

        origin = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
            reference=marker, posting_date='2026-09-30', currency='USD', amount=-10,
            category='Ajuste de conciliación', subcategory='CxC a la empresa', description='CxC de ensayo')).insert()
        receipt = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
            reference=marker+'-receipt', posting_date='2026-10-01', currency='USD', amount=15,
            category='Compensación entre partidas', description='Compensación de ensayo')).insert()
        frappe.db.set_value('CN Complementary Item', origin.name, 'docstatus', 1, update_modified=False)
        frappe.db.set_value('CN Complementary Item', receipt.name,
            dict(docstatus=1, receivable_origin=origin.name, compensated_usd=15), update_modified=False)
        deposit = new_deposit(employer.name, marker+'-deposit', 90)
        frappe.db.set_value('CN Remittance Allocation', deposit.name, 'allocation_detail', json.dumps([
            dict(tipo='Partida complementaria',partida=origin.name,importe_usd=-10,empresa=employer.name)]), update_modified=False)
        receivables = load_receivables(employer.name)
        assert len(receivables) == 1 and receivables[0]['receivable_usd'] is None
        assert receivables[0]['receivable_status'] == 'Revisar cobros' and receivables[0]['observed_usd'] == 5
        value = get_balance(origin.name)
        assert value['company_receivable_usd'] is None and value['receivable_issue_count'] == 1
        report = statement.execute(dict(employer=employer.name, view_mode='Resumen'))
        book = load_workbook(io.BytesIO(build_control_workbook(dict(year=2026, periods=[],
            adjustment_receivables=receivables, company_statement=dict(columns=report[0],rows=report[1],message=report[2])),
            exceptions=[], actions=[], employer_label=employer.name, generated_at=datetime(2026,10,12))), data_only=True)
        assert book['Resumen']['I5'].value == 'N/D'
        assert book['Partidas y excepciones']['H5'].value == 'N/D'
        assert 'exceso no es un saldo a favor' in book['Partidas y excepciones']['P5'].value
        return dict(application_original_usd=100, invalid_link_usd=150, application_balance=None,
                    receivable_original_usd=10, excessive_recovery_usd=15, receivable_balance=None,
                    excel_unknown=True)
    finally:
        frappe.db.rollback()
        assert before == {doctype: frappe.db.count(doctype) for doctype in doctypes}
