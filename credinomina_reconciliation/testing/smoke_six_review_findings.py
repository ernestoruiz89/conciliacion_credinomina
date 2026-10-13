"""SQL / controllers / report queries for the six fixes; isolated rollback only."""
import frappe


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    from credinomina_reconciliation.accounting_batch_store import save_state
    from credinomina_reconciliation.accounting_preview import save_preview, get_unidentified_page
    from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
    from credinomina_reconciliation.paying_employers import choose_detail_client
    from credinomina_reconciliation.period_pending import get_period_pending
    from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute
    frappe.set_user('Administrator')
    tables = [row[0] for row in frappe.db.sql("show tables like 'tabCN%'")]
    def counts():
        return {table: frappe.db.sql('select count(*) from `' + table.replace('`', '``') + '`')[0][0] for table in tables}
    before = counts()
    marker = 'SIX-' + frappe.generate_hash(length=8)
    try:
        a, b = [frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + suffix, employer_code=marker + suffix,
                                   payroll_frequency='Mensual')).insert() for suffix in ('A', 'B')]
        period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=a.name, payroll_month='2025-04-01',
            reconciliation_mode='Historica', historical_scope='Fecha exacta', historical_application_date='2025-04-15')).insert()
        client = frappe.get_doc(dict(doctype='CN Client', employer=a.name, client_number=marker, client_name='Ana de ensayo')).insert()
        document = frappe.get_doc(dict(doctype='CN Accounting Import', employer=a.name, source_file='/private/files/test.csv',
            currency='USD', historical_period=period.name, status='Importado', rows=[dict(event_type='Aplicacion',
                event_date='2025-04-15', source_key=marker, effective=1, client=client.name, client_number=marker,
                client_name=client.client_name, loan_number='1234567-1', currency='USD', amount=100,
                source_account=marker, source_currency='USD', source_debit=100, source_credit=0, source_row=257)])).insert()
        row = document.rows[0]
        frappe.db.set_value('CN Source Row', row.name, dict(match_status='Conciliado', historical_period=period.name,
            historical_remitted_usd=80, historical_balance_usd=20, net_applied_usd=100, deposit_match_status='Depósito conciliado'))
        _columns, rows, _message, _, _kpis = execute(dict(from_date='2025-04-01', to_date='2025-04-30', source_account=marker))
        assert len(rows) == 1 and rows[0]['state'] == 'Parcial' and rows[0]['pending_usd'] == 20, rows
        assert rows[0]['source_row'] == 257 and rows[0]['source_row_id'] == row.name
        pending = get_period_pending(period.name)['rows']
        assert any(issue.get('source_row') == row.name and issue['pending'] == 20 for issue in pending), pending
        # A transfer today cannot erase verified company membership in the past.
        frappe.db.set_value('CN Client', client.name, 'employer', b.name)
        clients = [frappe.get_doc('CN Client', client.name).as_dict()]
        evidence = load_detail_loan_clients([{'loan_number':'1234567'}], clients, {a.name})
        selected, reason = choose_detail_client({'loan_number':'1234567'}, clients, a.name, {a.name}, evidence)
        assert selected and selected['name'] == client.name and selected['employer'] == a.name, reason
        name_evidence = load_detail_loan_clients([{'client_name':client.client_name}], clients, {a.name})
        selected, reason = choose_detail_client({'client_name':client.client_name}, clients, a.name, {a.name}, name_evidence)
        assert selected and selected['name'] == client.name, reason
        deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=a.name,
            deposit_date='2025-05-01', deposit_reference=marker, deposit_currency='USD', deposit_amount=20,
            detail_rows=[dict(loan_number='1234567-1', deducted_usd=20)])).insert()
        detail = deposit.detail_rows[0]
        assert detail.client == client.name and detail.employer == a.name and detail.client_number == marker
        from credinomina_reconciliation.remittance_selection import get_pending_targets
        selection = get_pending_targets(deposit.name, detail_row_name=detail.name)
        assert selection['detail_client']['name'] == client.name and selection['detail_client']['employer'] == a.name
        assert any(candidate.get('historical_application') == row.name for candidate in selection['rows']), selection
        # Frozen preview data are stored in separate bounded blobs, not in the
        # financial records or every status response. Review every physical row.
        token = marker + '-batch'
        save_state(token, dict(user='Administrator', phase='preview', status='Vista previa', options={}, summary={}))
        plan = dict(groups=[dict(employer='NO IDENTIFICADA', rows=[dict(source_row=i, client_name=f'Cliente {i}', amount_usd=1)]) for i in range(1,604)])
        chunks = save_preview(token, plan)
        assert save_preview(token, plan) == chunks  # Retried checkpoint does not duplicate rows.
        save_state(token, dict(user='Administrator', phase='preview', status='Vista previa', options={}, summary={},
                               preview_chunks=chunks, draft_assignments={'501':a.name}))
        page = get_unidentified_page(token, start=500)
        assert chunks == 3 and page['count'] == 603 and page['rows'][0]['row'] == 501
        assert page['rows'][0]['employer'] == a.name
        assert get_unidentified_page(token, search='Cliente 603')['rows'][0]['row'] == 603
        return dict(application_state='Parcial', pending_usd=20, original_row=257,
                    historical_identity=True, preview_rows=603, preview_chunks=chunks, result='OK; rollback')
    finally:
        frappe.db.rollback()
        assert counts() == before, 'La prueba cambió registros guardados de las tablas CN'
