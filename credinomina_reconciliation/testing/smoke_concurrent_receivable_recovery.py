"""Two SQL connections: a new collection races a reversed receipt's offset."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import frappe

from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.complementary_compensation import confirm_compensation, reverse_compensation
from credinomina_reconciliation.receivable_recovery import apply_recovery
from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture, new_deposit, credit_item, rejects


def run():
    site, sites_path = frappe.local.site, frappe.local.sites_path
    if site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    names, old_reader, committed = {}, Event(), Event()
    item_name = receipt_name = company_name = cash_name = credit_name = None

    def worker(stale):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect()
            frappe.set_user('Administrator')
            frappe.get_doc('CN Complementary Item', receipt_name)  # establish this connection's read view
            if stale:
                old_reader.set()
                assert committed.wait(25), 'El cobro no terminó'
            else:
                assert old_reader.wait(25), 'La conexión competidora no abrió su lectura'
            try:
                if stale:
                    confirm_compensation(receipt_name, credit_name, 10, '2025-06-01', 'Recompensar', 'd' * 32)
                else:
                    apply_recovery(item_name, company_name, 'Depósito', cash_name, 10,
                        '2025-06-01', 'Cobro completo', 'e' * 32)
                frappe.db.commit()
                assert not stale, 'Se aceptó el consumo de una CxC ya cobrada'
                return 'Cobro único confirmado'
            except frappe.ValidationError as exc:
                assert stale and 'Otra operación' in str(exc), str(exc)
                return 'Compensación concurrente rechazada por saldo desactualizado'
        finally:
            if not stale:
                committed.set()
            frappe.db.rollback()
            frappe.destroy()

    try:
        marker = 'RACE-CXC-' + frappe.generate_hash(length=8)
        company, period, source, item, original = fixture(marker)
        item_name, company_name = item.name, company.name
        names = {company.doctype: [company.name], period.doctype: [period.name], source.doctype: [source.name],
                 item.doctype: [item.name], original.doctype: [original.name]}
        credit = credit_item(company.name, 10, marker)
        credit_name = credit.name
        names[item.doctype].append(credit.name)
        operation = 'a' * 32
        receipt_name = apply_recovery(item_name, company_name, 'Compensación', credit.name,
            10, '2025-05-10', 'Compensación original', operation)['name']
        reverse_compensation(receipt_name, operation, '2025-06-01', 'Corregir vínculo', 'b' * 32)
        assert get_balance(item_name)['company_receivable_usd'] == 10
        cash = new_deposit(company.name, marker + '-CASH', 10)
        cash_name = cash.name
        names[original.doctype].append(cash.name)
        frappe.db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(worker, True)
            winner = pool.submit(worker, False)
            outcomes = [winner.result(timeout=35), old.result(timeout=35)]
        frappe.db.rollback()
        value = get_balance(item_name)
        assert value['company_receivable_usd'] == 0, value
        assert sum(row['effective_usd'] for row in value['recoveries']) == 10
        rejects(lambda: confirm_compensation(receipt_name, credit_name, 10,
            '2025-06-01', 'Reintento fresco', 'f' * 32), 'supera la CxC')
        return dict(two_connections=True, original_cxc=10, recovered=10, pending=0,
            outcomes=outcomes, fresh_retry_blocked=True, fixtures_removed=True)
    finally:
        frappe.db.rollback()
        if item_name:
            names.setdefault('CN Complementary Item', []).extend(frappe.get_all('CN Complementary Item',
                filters={'receivable_origin': item_name}, pluck='name', limit_page_length=0))
        # These exact names belong only to this committed, synthetic fixture.
        for doctype, documents in reversed(list(names.items())):
            for field in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(field.options, {'parenttype': doctype, 'parent': ['in', documents]})
            frappe.db.delete('Version', {'ref_doctype': doctype, 'docname': ['in', documents]})
            frappe.db.delete('Comment', {'reference_doctype': doctype, 'reference_name': ['in', documents]})
            frappe.db.delete(doctype, {'name': ['in', documents]})
        frappe.db.commit()
