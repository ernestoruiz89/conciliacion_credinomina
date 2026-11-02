"""Legacy recovery receipts must reduce debt using credit, never another debit."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.testing.smoke_period_cash_breakdown import _fixture


def _core(marker, employer, signed_amount):
    with imported_item(marker):
        return frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer,
            category=offsets.CATEGORY, review_action=offsets.CATEGORY,
            posting_date='2025-06-01', currency='USD', amount=signed_amount,
            description='Evidencia importada para revisar dirección de CxC',
            accounting_source_key=marker, source_account='160209013004', source_currency='USD',
            source_debit=max(signed_amount, 0), source_credit=max(-signed_amount, 0),
            source_date='2025-06-01', source_file=f'/private/files/{marker}.csv', source_row=1,
            source_file_hash=marker, source_voucher=marker, voucher=marker, voucher_line='1',
            accounting_classification='Movimiento interno')).insert()


def _legacy_receipt(marker, employer, original, signed_amount):
    receipt = _core(marker, employer, signed_amount)
    # Only inside this rollback fixture: reproduce receipts from the retired
    # recovery writer. New manual receipt creation remains prohibited.
    frappe.db.set_value(receipt.doctype, receipt.name, dict(accounting_source_key=None,
        docstatus=1, receivable_origin=original.name, receivable_operation=marker,
        receivable_method='Compensación', compensated_usd=0), update_modified=False)
    return receipt.reload()


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Reconciliation Period', 'CN Accounting Import', 'CN Source Row',
        'CN Remittance Allocation', 'CN Complementary Item', 'CN Complementary Offset')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            seed_subcategories()
            marker = 'RECOVERY-DIRECTION-' + frappe.generate_hash(length=8)
            employer, periods, imported, original, deposit = _fixture(marker, [100], [0])
            # The paid shortfall is real. Recreate only its historical manual
            # CxC classification, whose new creation is no longer supported.
            frappe.db.set_value(original.doctype, original.name, dict(accounting_source_key=None,
                subcategory='CxC a la empresa', subcategory_effect='CxC a la empresa'), update_modified=False)
            original.reload()
            negative = _legacy_receipt(marker + '-NEG', employer.name, original, -3)
            debit = _core(marker + '-DEBIT', employer.name, 3)
            offsets._validate_pair(negative, debit)  # General opposite signs alone are insufficient.
            protected = [*periods, imported, original, deposit, negative, debit]
            before = {(doc.doctype, doc.name): doc.reload().as_dict() for doc in protected}
            assert get_balance(original.name)['company_receivable_usd'] == 10
            frappe.db.savepoint('wrong_recovery_direction')
            try:
                offsets.confirm_compensation(negative.name, debit.name, 3, '2025-06-01',
                    'Un débito no debe pagar una CxC', 'a' * 32)
            except frappe.ValidationError as error:
                assert 'un destino positivo' in str(error), str(error)
                frappe.db.rollback(save_point='wrong_recovery_direction')
            else:
                raise AssertionError('Se redujo una CxC con un segundo débito')
            for doc in protected:
                assert doc.reload().as_dict() == before[(doc.doctype, doc.name)]
            assert get_balance(original.name)['company_receivable_usd'] == 10

            positive = _legacy_receipt(marker + '-POS', employer.name, original, 3)
            credit = _core(marker + '-CREDIT', employer.name, -3)
            # Core imports retain magnitudes; their source credit determines direction.
            frappe.db.set_value(credit.doctype, credit.name, dict(amount=3, amount_usd=3), update_modified=False)
            credit.reload()
            offsets.confirm_compensation(positive.name, credit.name, 3, '2025-06-01',
                'Crédito original que liquida parcialmente la CxC', 'b' * 32)
            assert get_balance(original.name)['company_receivable_usd'] == 7
            offsets.reverse_compensation(positive.name, 'b' * 32, '2025-06-02',
                'Reversión de ensayo conserva la deuda', 'c' * 32)
            assert get_balance(original.name)['company_receivable_usd'] == 10
            for doc in [*periods, imported, original, deposit, negative, debit]:
                assert doc.reload().as_dict() == before[(doc.doctype, doc.name)]
            return dict(negative_receipt_against_debit_rejected=True, original_receivable_usd=10,
                valid_imported_credit_reduces_to_usd=7, reversal_restores_usd=10,
                deposit_and_application_journals_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
