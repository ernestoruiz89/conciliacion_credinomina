"""Real selector queries and nonblocking follow-up on the isolated site only."""
import frappe
from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture, new_deposit, rejects
from credinomina_reconciliation.receivable_recovery import search_recovery_destinations, get_recovery_destination


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    marker = 'REVIEW-GUARDS-' + frappe.generate_hash(length=8)
    company, period, source, origin, old_deposit = fixture(marker)
    available = new_deposit(company.name, marker + '-CASH', 7)
    results = search_recovery_destinations('CN Remittance Allocation', '', 'name', 0, 20,
        dict(item_name=origin.name, employer=company.name, method='Depósito'))
    assert [row[0] for row in results] == [available.name], results
    preview = get_recovery_destination(origin.name, company.name, 'Depósito', available.name)
    assert preview['available_usd'] == 7 and preview['suggested_usd'] == 7
    assert preview['receivable_usd'] == 10
    # A supervisor explicitly reclassifies quality follow-up. The original
    # amount, financial state and append-only history must survive closure.
    exception = frappe.get_doc(dict(doctype='CN Reconciliation Exception', period=period.name,
        employer=company.name, exception_type='Control de calidad', status='Abierta', amount_usd=20)).insert()
    exception.control_area = 'Calidad de cobranza'
    exception.blocks_closure = 0
    exception.classification_reason = 'Diferencia informativa; efectivo cubierto'
    exception.save()
    assert len(exception.follow_up_actions) == 1
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period
    close_period(period.name)
    period.reload()
    assert period.status == 'Cerrado'
    before = period.as_dict()
    exception.assigned_to = 'Administrator'
    exception.commitment_date = '2026-12-31'
    exception.next_action = 'Consultar calidad de la aplicación'
    exception.status = 'En revision'
    exception.save()
    period.reload()
    assert period.as_dict() == before, 'Informative follow-up altered closed period'
    assert exception.amount_usd == 20
    exception.amount_usd = 21
    rejects(lambda: exception.save(), 'Reabrir período')
    return dict(eligible_selector=True, available_before_after=True, supervisor_audit=True,
        closed_followup_without_financial_changes=True, closed_amount_edit_rejected=True, rollback=True)
