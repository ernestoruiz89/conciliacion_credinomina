"""Duplicating a receipt keeps planned targets, never executed cash or credits."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Reconciliation Period', 'CN Accounting Import',
        'CN Source Row', 'CN Remittance Allocation', 'CN Complementary Item')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'COPY-CASH-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD', historical_backfill=1,
                rows=[dict(event_type='Aplicacion', event_date='2025-04-30', currency='USD', amount=100,
                    amount_usd=100, effective=1, historical_period=period.name, source_key=marker,
                    client_number=marker, loan_number='99118-1')])).insert()
            engine._reconcile_sources(employer.name)
            imported.reload()
            original = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference=marker, deposit_date='2025-04-30', deposit_currency='USD', deposit_amount=100,
                targets=[dict(historical_application=imported.rows[0].name, amount_usd=100)])).insert()
            original.submit()
            reconcile_deposit(original)
            original.reload(); imported.reload(); period.reload()
            assert original.allocated_usd == 100 and original.allocation_detail != '[]'
            frozen = {(doc.doctype, doc.name): doc.as_dict() for doc in (original, imported, period)}
            duplicate = frappe.copy_doc(original, ignore_no_copy=False)
            duplicate.deposit_reference = marker + '-NEW'
            duplicate.insert()
            duplicate.submit(); duplicate.reload()
            assert duplicate.allocated_usd == 0, duplicate.as_dict()
            assert json.loads(duplicate.allocation_detail or '[]') == []
            assert duplicate.result == 'Pendiente' and duplicate.unclassified_usd == 100
            assert len(duplicate.targets) == 1 and duplicate.targets[0].result != 'Aplicada'
            assert duplicate.targets[0].historical_application == imported.rows[0].name
            # Raw JSON input and a later draft edit must not inject another
            # execution merely by copying read-only monetary fields.
            raw = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference=marker + '-RAW', deposit_date='2025-04-30', deposit_currency='USD', deposit_amount=100,
                allocated_usd=70, justified_surplus_usd=30, result='Conciliado',
                allocation_detail=original.allocation_detail)).insert()
            raw.allocated_usd = 100
            raw.result = 'Conciliado'
            raw.allocation_detail = original.allocation_detail
            raw.save()
            raw.submit(); raw.reload()
            assert (raw.allocated_usd, raw.justified_surplus_usd, raw.unclassified_usd) == (0, 0, 100)
            assert raw.result == 'Pendiente' and json.loads(raw.allocation_detail or '[]') == []
            engine._reconcile_sources(employer.name)
            for doc in (original, imported, period):
                assert doc.reload().as_dict() == frozen[(doc.doctype, doc.name)], (doc.doctype, doc.name)
            assert duplicate.reload().allocated_usd == raw.reload().allocated_usd == 0
            return dict(duplicated_receipt_has_no_executed_cash=True, planned_targets_preserved=True,
                raw_draft_values_cannot_confirm_payment=True, original_cash_and_links_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
