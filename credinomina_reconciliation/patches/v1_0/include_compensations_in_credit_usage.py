"""Recalculate available credit without rewriting either financial journal."""
import frappe

from credinomina_reconciliation.client_credit import CREDIT_CATEGORIES, RESULT
from credinomina_reconciliation.credit_usage import totals


def execute():
    updates = []
    issues = []
    for name in frappe.get_all('CN Complementary Item', filters={
            'docstatus': 1, 'category': ['in', CREDIT_CATEGORIES], 'result': RESULT},
            pluck='name', order_by='name', limit_page_length=0):
        item = frappe.get_doc('CN Complementary Item', name, for_update=True)
        try:
            updates.append((name, totals(item)))
        except frappe.ValidationError as exc:
            issues.append(f'{name}: {exc}')
    # Do not silently discard an overuse created under the old independent rules.
    # Validate every record before updating caches; a retry is idempotent.
    if issues:
        frappe.throw('Revise los saldos a favor antes de migrar sus compensaciones:\n' + '\n'.join(issues))
    for name, values in updates:
        frappe.db.set_value('CN Complementary Item', name, values)
        frappe.clear_document_cache('CN Complementary Item', name)
