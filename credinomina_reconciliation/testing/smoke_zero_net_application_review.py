"""Report legacy zero-net overcoverage without modifying its financial evidence."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.application_aging import application_balances, balance_status
from credinomina_reconciliation.client_position import load_position
from credinomina_reconciliation.company_statement import load_detail
from credinomina_reconciliation.control_kpis import load_application_figures
from credinomina_reconciliation.complementary_subcategories import seed_subcategories
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
            employer, periods, imported, complementary, deposit = _fixture(
                'ZERO-NET-REVIEW-' + frappe.generate_hash(length=8), [100], [0])
            # Simulate inconsistent legacy derived evidence, not a permitted
            # adjustment: today's adjustment flow rejects reduction below paid.
            frappe.db.set_value('CN Source Row', imported.rows[0].name,
                dict(application_adjustment_usd=100, net_applied_usd=0), update_modified=False)
            documents = [*periods, imported, complementary, deposit]
            before = {(doc.doctype, doc.name): doc.reload().as_dict() for doc in documents}
            journal = deposit.allocation_detail
            context = load_application_context(employer.name)
            rows = application_balances(*context, '2026-11-01', include_reviews=True)
            assert len(rows) == 1 and balance_status(rows[0]) == 'Revisar distribución', rows
            assert (rows[0]['applied_usd'], rows[0]['paid_usd'], rows[0]['amount_usd']) == (0, 100, 0), rows
            position = load_position([], dict(employer=employer.name, position_type='Aplicación', only_open=1))
            assert len(position) == 1 and position[0]['operational_status'] == 'Revisar distribución', position
            assert position[0]['remitted_usd'] == 100, position
            detail = load_detail(dict(employer=employer.name, component='application_pending_usd'))
            assert len(detail) == 1 and detail[0]['status'] == 'Revisar distribución', detail
            figures = load_application_figures(None, employer.name, '2026-11-01')
            assert figures['review_count'] == 1 and figures['pending_usd'] == 0, figures
            for doc in documents:
                assert doc.reload().as_dict() == before[(doc.doctype, doc.name)], (doc.doctype, doc.name)
            assert deposit.allocation_detail == journal and sum(row['importe_usd'] for row in json.loads(journal)) == 90
            return dict(zero_net_keeps_existing_paid_usd=100, company_and_client_reports_show_review=True,
                kpi_review_count=1, no_new_debt_or_cash_created=True, evidence_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
