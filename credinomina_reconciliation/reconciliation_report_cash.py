"""Separate cash from signed complementary funding at the report's actual grain."""
from collections import defaultdict
from decimal import Decimal

import frappe

from credinomina_reconciliation.accounting_client_summary import entries
from credinomina_reconciliation.reconciliation_deposit_detail import load_period_deposits
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _journal_offsets(periods, claims, client_by_detail):
    """Keep each deposit whole. An offset is attributable only to a sole recipient."""
    offsets = defaultdict(list)
    if not periods or not frappe.has_permission('CN Remittance Allocation', 'read'):
        return offsets
    for deposit in load_period_deposits(periods).values():
        if deposit.get('cash_identity_status') == 'Origen vinculado':
            continue
        try:
            ledger = entries(deposit.get('allocation_detail'))
            amounts = [Decimal(str(entry['importe_usd'])) for entry in ledger]
            if not all(amount.is_finite() for amount in amounts) or sum_money(amounts) != money(deposit['allocated_usd']):
                continue
        except (KeyError, ValueError, ArithmeticError, TypeError):
            continue
        offset = sum_money(amount for entry, amount in zip(ledger, amounts)
                           if entry.get('tipo') == 'Partida complementaria' and amount < 0)
        if not offset:
            continue
        recipients, linked_periods = {}, set()
        for entry, amount in zip(ledger, amounts):
            if amount <= 0:
                continue
            period = entry.get('periodo')
            key = (period, entry.get('tipo'), entry.get('aplicacion_id') or entry.get('fila_id'))
            detail = claims.get(key)
            if detail is None:
                break
            recipient = client_by_detail.get(id(detail), detail)
            recipients[id(recipient)] = recipient
            linked_periods.add(period)
        else:
            if len(linked_periods) == 1 and recipients:
                offsets[linked_periods.pop()].append((offset, recipients))
    return offsets


def _show_split(row, offset, note):
    if offset is not None and row['deposit_usd'] is not None and money(row['deposit_usd']) + offset < 0:
        offset = None
        note += ' La contrapartida supera el efectivo asignado. Revise la distribución.'
    for field in ('deposit_usd', 'cash_adjustment_usd'):
        row[field] = None if offset is None or row[field] is None else money_float(money(row[field]) + offset)
    row['deposit_difference_usd'] = (None if row['deposit_usd'] is None or row['application_usd'] is None
        else money_float(money(row['application_usd']) - money(row['deposit_usd'])))
    row['has_difference'] = True
    if row['deposit_usd'] is None:
        row['cash_control'] = 'Revisar: desglose de efectivo'
    row['observations'] = ' · '.join(filter(None, [row.get('observations'), note]))


def apply_period_cash(periods, summary, detail, claims, *, client_by_detail):
    by_period = defaultdict(list)
    for row in detail:
        if not row.get('_pending_receipt') and not row.get('_company_conversion') and row.get('deposit_usd') != 0:
            by_period[row['name']].append(row)
    multiple = {}
    for period, total in zip(periods, summary):
        if 'cash_breakdown_known' not in period:
            continue
        offset = money(period.get('negative_complementary_usd'))
        saved_cash = money(period.get('deposit_coverage_usd'))
        verified = (period['cash_breakdown_known'] and total['deposit_usd'] is not None
                    and offset <= 0 and money(total['deposit_usd']) + offset == saved_cash)
        if verified and not offset:
            continue
        children = by_period[period['name']]
        if not children and not period.get('cash_breakdown_note') and not offset and not saved_cash:
            continue
        if not verified:
            offset = None
        note = (f"Complementarias negativas del período {period['name']}: US$ {offset:.2f}."
                if offset is not None else period.get('cash_breakdown_note') or
                'El desglose de efectivo del período no se pudo comprobar.')
        _show_split(total, offset, note)
        targets = [client_by_detail.get(id(child), child) for child in children]
        recipients = {id(target): target for target in targets}
        if offset is not None and len(recipients) > 1:
            multiple[period['name']] = (period, offset, recipients)
        else:
            for target in recipients.values():
                _show_split(target, offset, note)
    offsets = _journal_offsets([data[0] for data in multiple.values()], claims, client_by_detail)
    for period_name, (_period, total_offset, recipients) in multiple.items():
        portions = offsets[period_name]
        if sum_money(amount for amount, _ in portions) != total_offset:
            portions = [(total_offset, recipients)]
        for amount, targets in portions:
            unique = len(targets) == 1
            note = (f"Complementaria negativa del período {period_name}: US$ {amount:.2f}." if unique else
                    f"El período {period_name} tiene una complementaria negativa compartida de US$ {amount:.2f}. "
                    "El efectivo individual no se puede separar de esa cobertura.")
            for target in targets.values():
                _show_split(target, amount if unique else None, note)
    if not client_by_detail:
        counts = defaultdict(int)
        for row in detail:
            if row.get('has_difference') and not (row.get('_company_conversion') or row.get('_detail_unavailable') or row.get('_pending_receipt')):
                counts[row['name']] += 1
        for row in summary:
            row['difference_count'] = counts[row['name']]
