"""Read real stored funding before closure; disposable fixtures, always rollback."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.period_closure import validate_direct_applications
from credinomina_reconciliation.testing.smoke_period_cash_breakdown import _fixture


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Reconciliation Period', 'CN Accounting Import', 'CN Source Row',
        'CN Remittance Allocation', 'CN Complementary Item')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            seed_subcategories()
            _employer, periods, imported, _complement, deposit = _fixture(
                'CLOSURE-FUNDING-' + frappe.generate_hash(length=8), [100], [0])
            period, source = periods[0], imported.rows[0]
            immutable = [period, deposit, _complement]
            snapshots = {(doc.doctype, doc.name): doc.reload().as_dict() for doc in immutable}
            validate_direct_applications(period)
            # Legacy evidence can retain native NIO and a documented USD rate.
            # The close guard must compare USD funding against USD debt.
            frappe.db.set_value('CN Source Row', source.name, dict(currency='NIO', amount=3662.43,
                manual_fx_rate=36.6243, equivalent_currency='', equivalent_amount=0,
                fx_basis='', historical_remitted_usd=100, historical_balance_usd=0,
                deposit_match_status='Depósito conciliado'), update_modified=False)
            validate_direct_applications(period)
            rejected = []
            for label, changes in (
                    ('shortfall', dict(historical_remitted_usd=99.99)),
                    ('overcoverage', dict(historical_remitted_usd=100.01)),
                    ('missing_fx', dict(historical_remitted_usd=100, manual_fx_rate=0))):
                frappe.db.set_value('CN Source Row', source.name, changes, update_modified=False)
                before = frappe.get_doc('CN Source Row', source.name).as_dict()
                try:
                    validate_direct_applications(period)
                except frappe.ValidationError:
                    rejected.append(label)
                else:
                    raise AssertionError('El cierre aceptó ' + label)
                assert frappe.get_doc('CN Source Row', source.name).as_dict() == before
            for doc in immutable:
                assert doc.reload().as_dict() == snapshots[(doc.doctype, doc.name)]
            return dict(usd_and_documented_nio_funding_accepted=True, rejected=rejected,
                saved_settled_status_does_not_bypass_arithmetic=True,
                period_deposit_complement_and_journal_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
