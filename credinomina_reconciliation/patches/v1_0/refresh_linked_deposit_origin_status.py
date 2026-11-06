"""Remove linked accounting origins from pending financial-state filters only."""
import frappe

from credinomina_reconciliation.deposit_accounting_sources import refresh_linked_origin_status
from credinomina_reconciliation.report_records import _pages


def execute():
    for deposit in _pages(frappe.get_all, 'CN Remittance Allocation', filters={'docstatus': ['in', [0, 1]]},
            or_filters=[['cash_identity_status', '=', 'Origen vinculado'],
                        ['consolidated_deposit', 'is', 'set']],
            fields=['name', 'docstatus', 'cash_identity_status', 'consolidated_deposit', 'financial_status'],
            order_by='name asc'):
        refresh_linked_origin_status(deposit)
