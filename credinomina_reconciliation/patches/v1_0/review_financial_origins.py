"""Archive physical origins and flag incomplete controls. Never reconcile cash."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
from credinomina_reconciliation.deposit_accounting_sources import SOURCE_DOCTYPE
from credinomina_reconciliation.receivable_terms import inherited_deadline


def execute():
    groups = defaultdict(list)
    last = ''
    while True:
        rows = frappe.get_all('CN Remittance Allocation', filters={'accounting_source_key': ['is', 'set'],
            'name': ['>', last]}, fields=list(dict.fromkeys(['name', 'docstatus', 'employer', 'bank_account',
                'deposit_date', 'deposit_reference', 'deposit_voucher', 'deposit_currency', 'deposit_amount',
                'cash_identity_status', *EVIDENCE_FIELDS])), order_by='name asc', limit_page_length=500)
        if not rows:
            break
        for row in rows:
            if not frappe.db.exists(SOURCE_DOCTYPE, {'parent': row.name, 'accounting_source_key': row.accounting_source_key}):
                frappe.get_doc({'doctype': SOURCE_DOCTYPE, 'parent': row.name,
                    'parenttype': 'CN Remittance Allocation', 'parentfield': 'accounting_sources',
                    'idx': 1, 'docstatus': row.docstatus,
                    **{field: row.get(field) for field in EVIDENCE_FIELDS}}).db_insert()
            if row.docstatus != 2 and row.source_file_hash and row.deposit_voucher and row.deposit_reference:
                key = tuple(str(row.get(field) or '') for field in ('source_file_hash', 'employer', 'bank_account',
                    'deposit_date', 'deposit_reference', 'deposit_voucher', 'deposit_currency', 'deposit_amount'))
                groups[key].append(row)
        last = rows[-1].name
    for group in groups.values():
        if len(group) < 2:
            continue
        for row in group:
            # Confirmed distributions/closed periods are not rewritten. Only
            # unreconciled drafts become explicit identity-review cases.
            if row.docstatus == 0 and not row.cash_identity_status:
                frappe.db.set_value('CN Remittance Allocation', row.name, {
                    'cash_identity_status': 'Revisar identidad',
                    'cash_identity_reason': 'Migración: posibles orígenes del mismo efectivo. Revise con ' + ', '.join(other.name for other in group if other.name != row.name) + '. No se fusionaron documentos ni se alteraron importes.',
                }, update_modified=False)
    last = ''
    while True:
        items = frappe.get_all('CN Complementary Item', filters={'docstatus': ['!=', 2],
            'category': 'Ajuste de conciliación', 'subcategory_effect': 'CxC a la empresa', 'name': ['>', last]},
            fields=['name', 'employer', 'period', 'receivable_due_date', 'credit_assigned_to', 'credit_commitment_date'],
            order_by='name asc', limit_page_length=500)
        if not items:
            break
        for item in items:
            due, origin = inherited_deadline(item) if not item.receivable_due_date else (item.receivable_due_date, '')
            values = {'receivable_setup_pending': int(not all((due, item.credit_assigned_to, item.credit_commitment_date)))}
            if not item.receivable_due_date:
                values.update(receivable_due_date=due, receivable_due_origin=origin or 'Migración: vencimiento pendiente de verificar; no se tomó la fecha de gestión')
            frappe.db.set_value('CN Complementary Item', item.name, values, update_modified=False)
        last = items[-1].name
