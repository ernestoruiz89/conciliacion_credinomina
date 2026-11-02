"""Two connections: a stale exception cannot enter an already closed period."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period, reopen_period
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_exception.cn_reconciliation_exception import CNReconciliationException


def run():
    site, sites_path = frappe.local.site, frappe.local.sites_path
    if site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    names, ready, closed = {}, Event(), Event()
    marker = 'EXCEPTION-CLOSE-' + frappe.generate_hash(length=10)

    def writer(period_name, employer):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect(); frappe.set_user('Administrator')
            doc = frappe.get_doc(dict(doctype='CN Reconciliation Exception', period=period_name,
                employer=employer, exception_type='Caso concurrente', status='Abierta', blocks_closure=1,
                control_area='Financiera', description=marker))
            try:
                doc.insert()
                frappe.db.commit()
                names.setdefault(doc.doctype, []).append(doc.name)
                return 'exception committed'
            except frappe.ValidationError as error:
                assert 'Otra operación' in str(error), str(error)
                return 'stale exception rejected'
        finally:
            frappe.db.rollback(); frappe.destroy()

    def closer(period_name):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect(); frappe.set_user('Administrator')
            assert ready.wait(20), 'No comenzó la lectura anterior'
            result = close_period(period_name)
            frappe.db.commit()
            return result['status']
        finally:
            closed.set()
            frappe.db.rollback(); frappe.destroy()

    original_insert = CNReconciliationException.before_insert
    def stale_insert(doc):
        if doc.get('description') == marker:
            # Model an earlier picker/link read in the same transaction. The
            # company lock must detect this repeatable-read snapshot afterward.
            assert frappe.db.get_value('CN Reconciliation Period', doc.period, 'status') != 'Cerrado'
            ready.set()
            assert closed.wait(25), 'No terminó el cierre concurrente'
        original_insert(doc)

    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            names[company.doctype] = [company.name]
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            names[period.doctype] = [period.name]
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file='/private/files/' + marker + '.csv', status='Importado', currency='USD',
                historical_backfill=1, rows=[dict(event_type='Aplicacion', event_date='2025-04-30',
                    currency='USD', amount=100, amount_usd=100, effective=1, historical_period=period.name,
                    source_key=marker, client_number=marker, loan_number='999001-1')])).insert()
            names[source.doctype] = [source.name]
            engine._reconcile_sources(company.name)
            source.reload()
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker, deposit_currency='USD', deposit_amount=100,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=100)])).insert()
            names[deposit.doctype] = [deposit.name]
            deposit.submit(); reconcile_deposit(deposit)
            info = frappe.get_doc(dict(doctype='CN Reconciliation Exception', period=period.name,
                employer=company.name, exception_type='Seguimiento informativo', status='Abierta',
                control_area='Calidad de cobranza', blocks_closure=0, classification_reason='No cambia la cobertura financiera',
                description=marker + '-INFO')).insert()
            names[info.doctype] = [info.name]
            assert period.reload().status == 'Conciliado'
            journal = deposit.reload().allocation_detail
            frappe.db.commit()
            with patch.object(CNReconciliationException, 'before_insert', stale_insert):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writing = pool.submit(writer, period.name, company.name)
                    closing = pool.submit(closer, period.name)
                    outcomes = [writing.result(timeout=40), closing.result(timeout=40)]
            frappe.db.rollback()
            period.reload()
            assert outcomes == ['stale exception rejected', 'Cerrado'], outcomes
            assert period.status == 'Cerrado'
            assert frappe.db.count('CN Reconciliation Exception', dict(period=period.name,
                status=['in', ['Abierta', 'En revision']], blocks_closure=1)) == 0
            frozen = period.as_dict()
            # Existing nonblocking management may continue without changing
            # the closed period's balances, counts, links or timestamps.
            info.reload()
            info.description = marker + '-INFO: seguimiento después del cierre'
            info.append('follow_up_actions', dict(action_type='Contacto con empresa', details='Seguimiento informativo'))
            info.save()
            assert period.reload().as_dict() == frozen
            frappe.db.savepoint('closed_exception_delete')
            try:
                frappe.delete_doc(info.doctype, info.name)
            except frappe.ValidationError as error:
                assert 'cerrado' in str(error), str(error)
                frappe.db.rollback(save_point='closed_exception_delete')
            else:
                raise AssertionError('Se eliminó el historial de una excepción de período cerrado')
            assert frappe.db.exists(info.doctype, info.name)
            assert period.reload().as_dict() == frozen
            # A fresh blocker, registered after reopening, prevents another
            # real close; changing a label never pays or discards the debt.
            reopen_period(period.name, 'Validar bloqueo de excepción recién registrada')
            blocker = frappe.get_doc(dict(doctype='CN Reconciliation Exception', period=period.name,
                employer=company.name, exception_type='Caso pendiente', status='Abierta',
                description='Pendiente financiero para revisión')).insert()
            names[blocker.doctype].append(blocker.name)
            frappe.db.savepoint('blocking_exception_close')
            try:
                close_period(period.name)
            except frappe.ValidationError as error:
                assert 'excepciones que bloquean el cierre' in str(error), str(error)
                frappe.db.rollback(save_point='blocking_exception_close')
            else:
                raise AssertionError('Se cerró el período con una excepción bloqueante')
            assert period.reload().status != 'Cerrado'
            assert deposit.reload().allocation_detail == journal and deposit.allocated_usd == 100
            assert source.reload().rows[0].historical_remitted_usd == 100
            return dict(two_connections=True, outcomes=outcomes, no_blocking_exception_entered_closed_period=True,
                nonblocking_follow_up_preserves_closed_period=True, deletion_after_close_rejected=True,
                fresh_blocking_exception_prevents_close=True, cash_and_links_preserved=True, fixtures_removed=True)
    finally:
        frappe.db.rollback()
        # Only exact names created by this disposable fixture are eligible.
        for doctype, documents in reversed(list(names.items())):
            for field in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(field.options, dict(parent=['in', documents], parenttype=doctype))
            frappe.db.delete('CN Reconciliation Trace', dict(source_doctype=doctype, source_name=['in', documents]))
            frappe.db.delete('Version', dict(ref_doctype=doctype, docname=['in', documents]))
            frappe.db.delete('Comment', dict(reference_doctype=doctype, reference_name=['in', documents]))
            frappe.db.delete(doctype, dict(name=['in', documents]))
        frappe.db.commit()
