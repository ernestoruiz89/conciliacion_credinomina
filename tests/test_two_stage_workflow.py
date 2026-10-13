"""Acceptance invariants for collection quality and independent cash reconciliation."""
import copy
import json
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation.application_quality import collection_quality, application_cash_covered
from credinomina_reconciliation.period_totals import calculate_totals
from credinomina_reconciliation.legacy_employer_detail import archive_values, guard_legacy_evidence
from credinomina_reconciliation.patches.v1_0 import archive_employer_detail as migration
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller


@pytest.mark.parametrize('requested,applied,paid,gap,pending', [
    (100,80,80,-20,0), (100,100,80,0,20), (100,120,120,20,0),
    (100,0,0,-100,0), (0,80,80,80,0),
])
def test_quality_and_cash_are_independent(requested, applied, paid, gap, pending):
    row = frappe._dict(name='C', expected_usd=requested, applied_usd=applied,
                      deducted_usd=9999, deduction_status='Importes inconsistentes', remitted_usd=paid,
                      remittance_detail=json.dumps([dict(importe_usd=paid, destino='Cobranza')]))
    original = copy.deepcopy(row)
    assert collection_quality(row, 'Detalle de empresa')['quality_difference_usd'] == gap
    totals = calculate_totals(dict(reconciliation_mode='Operativa', applied_usd=applied, collection_rows=[row]))
    assert totals == dict(applied_total_usd=applied, remitted_total_usd=paid, pending_usd=pending)
    assert row == original


def test_signed_complement_destination_does_not_change_quality_or_fake_coverage():
    for net, complement, cash in [(90,10,100), (100,-10,90)]:
        row = dict(expected_usd=100, applied_usd=net, remitted_usd=cash,
                   remittance_detail=json.dumps([dict(destino='Cobranza', importe_usd=net),
                                                dict(destino='Partida complementaria', importe_usd=complement)]))
        assert application_cash_covered(row) == net
        assert collection_quality(row)['quality_difference_usd'] == net - 100


def test_excel_counts_direct_and_collection_link_once():
    from credinomina_reconciliation.control_export import _period_credit_cash, _period_pending
    source = dict(name='H', currency='USD', amount=100, collection_row_id='C',
                  historical_remitted_usd=80, historical_balance_usd=20)
    row = dict(name='C', applied_usd=100, remitted_usd=80,
               remittance_detail=[dict(importe_usd=80, aplicacion_directa='H')])
    period = dict(reconciliation_mode='Operativa', rows=[row], historical_rows=[source])
    assert _period_credit_cash(period) == 80
    assert _period_pending(period) == 20


def test_archive_and_migration_are_idempotent_without_financial_writes():
    row = frappe._dict(name='C', client_name='Cliente', deducted_usd=80, deduction_status='Deduccion parcial')
    pending = frappe._dict(doctype='CN Provisional Adjustment', name='PENDING', basis='Detalle de empresa', state='Aprobado')
    real = frappe._dict(doctype='CN Provisional Adjustment', name='REAL', basis='Detalle de empresa', state='Materializado')
    period = frappe._dict(name='P', status='Parcial', application_basis='Detalle de empresa',
                         employer_response_file='/private/files/empresa.xlsx', applied_usd=80, remitted_usd=60,
                         collection_rows=[row], provisional_adjustments=[pending, real])
    closed = frappe._dict(period, name='CLOSED', status='Cerrado')
    def save(dt, name, field, value=None, **kwargs):
        assert kwargs == {'update_modified': False}
        if dt == 'CN Reconciliation Period':
            assert field == 'legacy_employer_detail'
            (period if name == 'P' else closed)[field] = value
        else:
            assert name == 'PENDING'
            pending.update(field)
    db = Mock(set_value=Mock(side_effect=save))
    with patch.object(migration.frappe, 'get_all', return_value=['P','CLOSED']), \
         patch.object(migration.frappe, 'get_doc', side_effect=lambda dt,n: period if n=='P' else closed), \
         patch.object(migration.frappe, 'db', db):
        migration.execute()
        evidence = period.legacy_employer_detail
        migration.execute()
    assert period.legacy_employer_detail == evidence
    assert (period.applied_usd,period.remitted_usd) == (80,60)
    assert real.state == 'Materializado'
    assert pending.state == 'Pendiente de revisión'
    assert json.loads(evidence)['parent']['employer_response_file'] == '/private/files/empresa.xlsx'
    assert not archive_values(frappe._dict(collection_rows=[]))


def test_legacy_evidence_cannot_be_edited_through_api():
    before = frappe._dict(employer_response_file='/private/files/original.xlsx', collection_rows=[])
    current = frappe._dict(before, get_doc_before_save=lambda: before)
    current.employer_response_file='/private/files/replaced.xlsx'
    with patch.object(frappe,'throw',side_effect=ValueError), pytest.raises(ValueError):
        guard_legacy_evidence(current)


@pytest.mark.parametrize('field,value', [
    ('deduction_basis', 'Detalle de empresa'), ('deducted_usd', 80),
    ('deduction_recognition_reference', 'OLD'), ('employer_response_file', '/private/files/old.xlsx'),
])
def test_new_period_cannot_receive_legacy_fields_through_api(field, value):
    current = frappe._dict(collection_rows=[], get_doc_before_save=lambda: None, **{field: value})
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        guard_legacy_evidence(current)


def test_stale_proposals_without_file_still_get_audit_warning():
    period = frappe._dict(provisional_adjustments=[frappe._dict(basis='Detalle de empresa', state='Aprobado')])
    assert 'regenérelas' in archive_values(period)['review'][0]


def test_zero_amount_legacy_note_is_visible_in_audit():
    period = frappe._dict(collection_rows=[frappe._dict(deduction_match_note='Respuesta recibida sin importe')])
    assert archive_values(period)['rows'][0]['deduction_match_note'] == 'Respuesta recibida sin importe'


@pytest.mark.parametrize('action,args', [
    ('import_employer_response',('P',)), ('recognize_collection_as_employer_detail',('P','2026-10-01',1)),
    ('revert_deposit_recognition',('P',)), ('get_recognizable_deposits',('P',)),
    ('recognize_collection_from_deposit',('P','D','justificación')),
])
def test_retired_actions_cannot_write(action,args):
    with patch.object(frappe,'throw',side_effect=ValueError) as reject, patch.object(frappe,'get_doc') as read:
        with pytest.raises(ValueError): getattr(controller,action)(*args)
        assert 'Distribución de Depósito' in reject.call_args.args[0]
        read.assert_not_called()
