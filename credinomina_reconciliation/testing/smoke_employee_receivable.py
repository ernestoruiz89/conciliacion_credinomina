"""Rollback-only real creation, repayment, offset and reversal of employee debt."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.complementary_compensation import reverse_compensation
from credinomina_reconciliation.company_statement import load_detail
from credinomina_reconciliation.receivable_recovery import apply_recovery
from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture, new_deposit, credit_item, rejects


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'EMPLOYEE-CXC-' + frappe.generate_hash(length=8)
            company, period, imported, item, deposit = fixture(marker, employee=True)
            assert get_balance(item.name)['company_receivable_usd'] == 0
            assert str(item.receivable_due_date) == '2025-05-15'
            original = deposit.allocation_detail
            rows = load_detail({'employer': company.name, 'component': 'employee_receivable_usd'})
            assert len(rows) == 1 and rows[0]['employee_receivable_usd'] == 10
            assert not load_detail({'employer': company.name, 'component': 'company_receivable_usd'})
            cash = new_deposit(company.name, marker + '-CASH', 3)
            paid = apply_recovery(item.name, company.name, 'Depósito', cash.name, 3,
                '2025-05-10', 'Cobro parcial del empleado', 'a' * 32)
            receipt = frappe.get_doc('CN Complementary Item', paid['name'])
            assert receipt.client_number == item.client_number and receipt.credit_client == item.credit_client
            assert receipt.loan_number == item.loan_number
            assert get_balance(item.name)['employee_receivable_usd'] == 7
            counterpart = credit_item(company.name, 4, marker)
            rejects(lambda: apply_recovery(item.name, company.name, 'Compensación', counterpart.name,
                4, '2025-05-10', 'Contrapartida sin cliente', 'b' * 32), 'mismo cliente')
            counterpart.reload()
            counterpart.client_number, counterpart.loan_number = item.client_number, item.loan_number
            counterpart.save()
            offset = apply_recovery(item.name, company.name, 'Compensación', counterpart.name,
                4, '2025-05-10', 'Compensación del mismo empleado', 'c' * 32)
            assert get_balance(item.name)['employee_receivable_usd'] == 3
            reverse_compensation(offset['name'], 'c' * 32, '2025-06-01', 'Revertir ensayo', 'd' * 32)
            assert get_balance(item.name)['employee_receivable_usd'] == 7
            receipt.reload()
            receipt.cancel()
            assert get_balance(item.name)['employee_receivable_usd'] == 10
            deposit.reload()
            assert deposit.allocation_detail == original
            return dict(employee_cxc=10, after_cash=7, after_offset=3, after_reversals=10,
                company_cxc=0, debtor_preserved=True, foreign_offset_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
