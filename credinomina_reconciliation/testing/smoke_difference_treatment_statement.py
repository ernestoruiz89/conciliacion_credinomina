"""Rollback-only statement classification test using real catalog and ledger rows."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'REASON-' + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            reason = frappe.get_doc(dict(doctype='CN Difference Reason', reason_name=marker)).insert()
            assert not reason.treatment
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
                    category='Por clasificar', review_action='Pendiente de revisión', currency='USD',
                    amount=12.34, posting_date='2025-05-01', source_date='2025-05-01',
                    source_currency='USD', source_debit=0, source_credit=12.34,
                    accounting_source_key=marker, voucher=marker, voucher_line=marker,
                    source_voucher=marker, description='Crédito del core clasificado para reporte')).insert()
            frappe.db.set_value(item.doctype, item.name, dict(docstatus=1, difference_reason=reason.name))
            filters = dict(employer=company.name, statement_scope='Gestión')
            baseline = execute(filters)[1][0]
            assert baseline['other_credit_usd'] == -12.34, baseline
            for treatment, field in [('A Favor Cliente', 'client_credit_usd'), ('A Favor Empresa', 'company_credit_usd')]:
                reason.treatment = treatment
                reason.save()
                for cutoff in (None, '2025-06-30'):
                    scope = dict(filters, **({'cutoff_date': cutoff} if cutoff else {}))
                    summary = execute(scope)[1][0]
                    assert summary[field] == -12.34, summary
                    assert summary['other_credit_usd'] == 0 and summary['core_pending_usd'] == 0, summary
                    assert summary['management_net_usd'] == baseline['management_net_usd'], summary
                    detail = execute(dict(scope, view_mode='Detalle', component=field))[1]
                    assert len(detail) == 2 and detail[0]['source_document'] == item.name, detail
                    assert detail[0]['difference_treatment'] == treatment
                    assert detail[-1]['statement_amount_usd'] == -12.34, detail
            return dict(default_empty=True, credit_moved_once=True, historical=True, matching_drill=True, rolled_back=True)
    finally:
        frappe.db.rollback()
