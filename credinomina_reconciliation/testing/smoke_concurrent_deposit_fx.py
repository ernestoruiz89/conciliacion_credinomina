"""Two connections: a submitted FX edit invalidates an earlier cash snapshot."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    site, sites_path = frappe.local.site, frappe.local.sites_path
    if site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    names, ready, changed = {}, Event(), Event()

    def stale_reader(deposit_name):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect(); frappe.set_user('Administrator')
            deposit = frappe.get_doc('CN Remittance Allocation', deposit_name)
            assert deposit.amount_usd == 100
            ready.set()
            assert changed.wait(20), 'No finalizó la corrección de tasa'
            try:
                reconcile_deposit(deposit)
                frappe.db.commit()
                return 'accepted'
            except frappe.ValidationError as error:
                assert 'Otra operación' in str(error), str(error)
                return 'stale FX snapshot rejected'
        finally:
            frappe.db.rollback(); frappe.destroy()

    def fx_writer(deposit_name):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect(); frappe.set_user('Administrator')
            assert ready.wait(20), 'No inició la lectura anterior'
            deposit = frappe.get_doc('CN Remittance Allocation', deposit_name)
            deposit.fx_rate = 73.2486
            deposit.save()
            assert deposit.amount_usd == 50
            frappe.db.commit()
            return 'FX correction committed'
        finally:
            changed.set()
            frappe.db.rollback(); frappe.destroy()

    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CONCURRENT-FX-' + frappe.generate_hash(length=10)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker)).insert()
            names[company.doctype] = [company.name]
            client = frappe.get_doc(dict(doctype='CN Client', client_number=marker,
                client_name=marker, employer=company.name)).insert()
            names[client.doctype] = [client.name]
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            names[period.doctype] = [period.name]
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD', historical_backfill=1,
                rows=[dict(event_type='Aplicacion', event_date='2025-04-30', currency='USD', amount=100,
                    amount_usd=100, effective=1, historical_period=period.name, source_key=marker,
                    client=client.name, client_number=marker, client_name=marker, loan_number='99322-1')])).insert()
            names[source.doctype] = [source.name]
            engine._reconcile_sources(company.name)
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker, deposit_currency='NIO', deposit_amount=3662.43,
                fx_rate=36.6243, apply_fifo=1, detail_periods=[dict(period=period.name)],
                detail_file=f'/private/files/{marker}.csv', detail_source_file=f'/private/files/{marker}.csv',
                detail_hash=marker, detail_rows=[dict(source_row=2, client=client.name, client_number=marker,
                    client_name=marker, loan_number='99322-1', deducted_usd=100)])).insert()
            names[deposit.doctype] = [deposit.name]
            deposit.submit()
            frappe.db.commit()
            with ThreadPoolExecutor(max_workers=2) as pool:
                reader = pool.submit(stale_reader, deposit.name)
                writer = pool.submit(fx_writer, deposit.name)
                outcomes = [reader.result(timeout=30), writer.result(timeout=30)]
            frappe.db.rollback()
            current = frappe.get_doc(deposit.doctype, deposit.name)
            assert outcomes[0] == 'stale FX snapshot rejected', (outcomes, current.amount_usd, current.allocated_usd)
            assert current.amount_usd == 50 and current.allocated_usd == 0, current.as_dict()
            reconcile_deposit(current)
            current.reload(); source.reload()
            assert current.amount_usd == 50 and current.allocated_usd <= 50
            assert source.rows[0].historical_remitted_usd <= 50
            assert sum(entry['importe_usd'] for entry in json.loads(current.allocation_detail or '[]')) == current.allocated_usd
            assert current.amount_usd == current.allocated_usd + current.justified_surplus_usd + current.unclassified_usd
            return dict(two_connections=True, outcomes=outcomes, fresh_retry_within_current_cash=True,
                deposit_usd=current.amount_usd, allocated_usd=current.allocated_usd,
                application_paid_usd=source.rows[0].historical_remitted_usd,
                unclassified_usd=current.unclassified_usd, fixtures_removed=True)
    finally:
        frappe.db.rollback()
        # Only this disposable fixture's exact document names are removed.
        for doctype, documents in reversed(list(names.items())):
            for field in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(field.options, dict(parent=['in', documents], parenttype=doctype))
            frappe.db.delete('CN Reconciliation Trace', dict(source_doctype=doctype, source_name=['in', documents]))
            frappe.db.delete('Version', dict(ref_doctype=doctype, docname=['in', documents]))
            frappe.db.delete('Comment', dict(reference_doctype=doctype, reference_name=['in', documents]))
            frappe.db.delete(doctype, dict(name=['in', documents]))
        frappe.db.commit()
