"""Inherited explanations must serialize on both sides of an operative payment."""
import json
from collections import defaultdict
from decimal import Decimal
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation.reconciliation import matching_exception_notes
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import (
    cn_accounting_import as engine,
)


@pytest.mark.parametrize('origin', ['application', 'deduction'])
def test_matching_notes_preserve_cents_as_json_numbers(origin):
    notes = matching_exception_notes(
        expected_usd=Decimal('100.01'), deducted_usd=Decimal('90.00'),
        applied_usd=Decimal('90.00'), complementary_usd=Decimal('0'),
        remitted_usd=Decimal('90.00'),
        **{f'{origin}_notes': [dict(comment='Diferencia explicada', exception_id='EX-1')]},
    )
    decoded = json.loads(json.dumps(notes, ensure_ascii=False))
    assert decoded[0]['gap_usd'] == 10.01
    assert isinstance(decoded[0]['gap_usd'], (int, float))
    assert decoded[0]['exception_id'] == 'EX-1'


@pytest.mark.parametrize('origin', ['application', 'deduction'])
def test_transfer_serializes_collection_and_deposit_without_changing_cash(origin):
    target = frappe._dict(name='C', parent='P', row_key='ROW-1', expected_usd=Decimal('100.01'),
        applied_usd=Decimal('90.00'), complementary_usd=Decimal('0'), remitted_usd=Decimal('90.00'),
        first_exception_comment='Aplicación parcial' if origin == 'application' else '', application_comment='')
    details = defaultdict(list, C=[dict(destino='Cobranza', referencia='D', importe_usd=90.0)])
    raw = json.dumps([dict(tipo='Cobranza', periodo='P', fila_id='ROW-1', importe_usd=90.0)])
    account = frappe._dict(name='D', allocation_detail=raw, amount_usd=90.0, allocated_usd=90.0)
    bank = frappe._dict(account)
    allocation = dict(deposit_meta={'D': dict(account=account, bank=bank)})
    exceptions = ([frappe._dict(name='EX-1', collection_row_id='C', exception_type='Deduccion parcial',
        description='Deducción parcial', resolution='Justificación confirmada', status='Resuelta')]
        if origin == 'deduction' else [])
    with patch.object(engine.frappe, 'get_all', return_value=exceptions):
        engine._transfer_matching_exception_notes({'C': target}, details, allocation)
        # A second reconciliation must preserve the same evidence and balances.
        first = (target.remittance_detail, account.allocation_detail, account.inherited_exception_comment)
        engine._transfer_matching_exception_notes({'C': target}, details, allocation)
    assert (target.remittance_detail, account.allocation_detail, account.inherited_exception_comment) == first
    collection_entry = json.loads(target.remittance_detail)[0]
    for deposit in (account, bank):
        deposit_entry = json.loads(deposit.allocation_detail)[0]
        assert deposit_entry['excepciones_heredadas'] == collection_entry['excepciones_heredadas']
        assert deposit_entry['importe_usd'] == 90.0
        assert deposit.amount_usd == deposit.allocated_usd == 90.0
    note = collection_entry['excepciones_heredadas'][0]
    assert note['gap_usd'] == 10.01
    assert note['comment'] == ('Aplicación parcial' if origin == 'application' else 'Justificación confirmada')
    assert note['exception_id'] == ('' if origin == 'application' else 'EX-1')
    assert target.applied_usd == target.remitted_usd == Decimal('90.00')
