"""Read-only, effective-date reconstruction using currently valid reconciliation links.

This is NOT a replay of what a user knew on a past day. Corrections/cancellations
remain effective; dated compensation and credit-management reversals are replayed.
Never read current aggregate balances as if they were historical balances.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
import json

import frappe

from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.complementary_balances import FIELDS, CREDIT_CATEGORIES, financial_balance
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables
from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _day(value):
    if not value:
        raise ValueError('No se puede reconstruir el corte: hay una operación sin fecha efectiva.')
    return date.fromisoformat(str(value)[:10])


def _journal(value, name):
    try:
        entries = json.loads(value or '[]') if isinstance(value, str) else value or []
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ValueError()
        return entries
    except (TypeError, ValueError):
        raise ValueError(f'No se puede reconstruir el corte: historial ilegible en {name}.') from None


def _dated_total(entries, field, amount, cutoff):
    # Validate dates even after the cutoff: an undated entry cannot be excluded safely.
    return sum_money(row.get(amount) for row in entries if _day(row.get(field)) <= cutoff)


def reconstruct(context, deposits, items, offsets, cutoff_date, filters=None):
    """Pure projection. Inputs are copied, and no reconciliation/write is executed."""
    from credinomina_reconciliation.company_statement import build_detail, summarize

    cutoff = _day(cutoff_date)
    sources, imports, periods, collections, employers = deepcopy(context)
    deposits, items = deepcopy(deposits), deepcopy(items)
    all_sources = {row['name']: row for row in sources}
    live_items = {row['name']: row for row in items if row.get('docstatus') != 2}
    visible_deposits = {row['name']: row for row in deposits if row.get('docstatus') != 2}
    warnings = []
    histories = defaultdict(list)
    for row in offsets:
        histories[row['parent']].append(row)

    projected_items = {}
    for name, item in live_items.items():
        if _day(item.get('source_date') or item.get('posting_date')) > cutoff:
            continue
        effective = _day(item.get('posting_date')) <= cutoff
        ledger = histories[name]
        if money(item.get('compensated_usd')) != sum_money(row.get('amount_usd') for row in ledger):
            raise ValueError(f'{name}: el saldo compensado no coincide con su historial; no se puede reconstruir el corte.')
        item['compensated_usd'] = money_float(_dated_total(ledger, 'compensation_date', 'amount_usd', cutoff))
        if not effective:
            # A later classification/use must not remove an earlier core movement.
            item.update(category='Por clasificar', related_application='', application_adjustment_usd=0,
                        registration_exception='', review_action='', compensated_usd=0)
        if item.get('registration_exception'):
            origin = live_items.get(item.get('_registration_origin'))
            if not origin:
                raise ValueError(f'{name}: no está disponible el origen del registro contable verificado.')
            if _day(origin.get('posting_date')) > cutoff:
                item['registration_exception'] = ''
        if item.get('category') in CREDIT_CATEGORIES and item.get('docstatus') == 1:
            deposit = visible_deposits.get(item.get('registered_deposit'))
            if not deposit:
                raise ValueError(f'{name}: falta un depósito relacionado accesible para reconstruir el saldo a favor.')
            if _day(deposit.get('deposit_date')) > cutoff:
                continue
            history = _journal(item.get('credit_history'), name)
            if money(item.get('credit_resolved_usd')) != sum_money(row.get('importe_usd') for row in history):
                raise ValueError(f'{name}: el saldo gestionado no coincide con su historial fechado.')
            resolved = _dated_total(history, 'fecha', 'importe_usd', cutoff)
            pending = money(item.get('amount_usd')) - resolved
            if pending < 0 or resolved < 0:
                raise ValueError(f'{name}: el historial de gestión produce un saldo inválido al corte.')
            item.update(credit_resolved_usd=money_float(resolved), credit_pending_usd=money_float(pending),
                        credit_management_status='Resuelto' if not pending else 'Parcialmente resuelto' if resolved else 'Pendiente')
        projected_items[name] = item

    adjustments = defaultdict(lambda: money(0))
    for item in projected_items.values():
        if (item.get('docstatus') == 1 and item.get('category') == 'Ajuste de aplicación'
                and item.get('related_application')):
            adjustments[item['related_application']] += money(item.get('application_adjustment_usd'))
    eligible_sources = {}
    future_collections = set()
    collection_sources = defaultdict(set)
    for row in sources:
        links = _journal(row.get('application_allocation_detail'), row['name'])
        if not links and row.get('collection_row_id'):
            links = [dict(collection_row_id=row['collection_row_id'], amount_usd=converted_amount(row, 'USD'))]
        in_cutoff = _day(row.get('event_date')) <= cutoff
        for link in links:
            key = link.get('collection_row_id')
            collection_sources[key].add(row['name'])
            if not in_cutoff:
                future_collections.add(key)
        if not in_cutoff:
            continue
        row.update(application_adjustment_usd=money_float(adjustments[row['name']]),
                   historical_remitted_usd=0, historical_detail=[])
        # A later application adjustment may have reduced today's links. The
        # restored excess stays pending, never silently disappears from the cut.
        applied = converted_amount(row, 'USD')
        if links and applied is not None:
            remaining = money(applied)
            clipped = []
            for link in links:
                amount = min(max(money(link.get('amount_usd')), money(0)), remaining)
                clipped.append(dict(link, amount_usd=money_float(amount)))
                remaining -= amount
            if len(clipped) == 1 and remaining:
                clipped[0]['amount_usd'] = money_float(applied)
            row['application_allocation_detail'] = clipped
        eligible_sources[row['name']] = row
    collection_keys = {}
    for name, row in collections.items():
        row.update(remittance_detail=[], rounding_adjustment_usd=0, fx_variance_usd=0)
        collection_keys[(row['parent'], row.get('row_key'))] = name

    credits_by_deposit = defaultdict(list)
    for item in projected_items.values():
        if (item.get('docstatus') == 1 and item.get('category') in CREDIT_CATEGORIES
                and item.get('result') == 'Saldo a favor documentado'):
            credits_by_deposit[item.get('registered_deposit')].append(item)
    projected_deposits, distributions = [], defaultdict(list)
    for deposit in deposits:
        if deposit.get('docstatus') == 2 or not (deposit.get('docstatus') == 1 or deposit.get('accounting_source_key')):
            continue
        if _day(deposit.get('deposit_date')) > cutoff:
            continue
        deposit.update(allocated_usd=0, justified_surplus_usd=0, result='Pendiente al corte')
        projected_deposits.append(deposit)
        if deposit.get('docstatus') != 1:
            continue
        credits = credits_by_deposit[deposit['name']]
        surplus = sum_money(item.get('amount_usd') for item in credits)
        deposit['justified_surplus_usd'] = money_float(surplus)
        entries = []
        for entry in _journal(deposit.get('allocation_detail'), deposit['name']):
            kind, key = entry.get('tipo'), None
            if kind == 'Aplicacion historica':
                key = entry.get('aplicacion_id')
                if key not in all_sources:
                    raise ValueError(f"{deposit['name']}: no está disponible una aplicación vinculada; revise sus permisos y vínculos.")
                if key not in eligible_sources:
                    continue
            elif kind == 'Cobranza':
                key = collection_keys.get((entry.get('periodo'), entry.get('fila_id')))
                if not key:
                    raise ValueError(f"{deposit['name']}: no está disponible la cobranza vinculada.")
                if not (collection_sources[key] & eligible_sources.keys()):
                    continue
                if key in future_collections:
                    slices = _journal(entry.get('aplicaciones_fifo'), deposit['name'])
                    if slices:
                        if (sum_money(part.get('amount_usd') for part in slices) != money(entry.get('importe_usd'))
                                or any(part.get('application_id') not in collection_sources[key]
                                       or money(part.get('amount_usd')) < 0 for part in slices)):
                            raise ValueError(f"{deposit['name']}: el desglose FIFO no coincide con las aplicaciones vinculadas.")
                        amount = sum_money(part.get('amount_usd') for part in slices
                                           if part['application_id'] in eligible_sources)
                        entry = dict(entry, importe_usd=money_float(amount))
                        if not amount:
                            continue
                    else:
                        # The stored claim spans both sides of the cut, but no
                        # source-level split is preserved. Retain unassigned cash.
                        warnings.append(f"{deposit['name']}: distribución operativa con aplicaciones posteriores al corte; importe conservado sin asignar.")
                        continue
            elif kind == 'Partida complementaria':
                key = entry.get('partida')
                if key not in live_items:
                    raise ValueError(f"{deposit['name']}: no está disponible una partida vinculada.")
                if key not in projected_items or _day(projected_items[key].get('posting_date')) > cutoff:
                    continue
            elif kind == 'Movimiento de conciliación':
                item = projected_items.get(entry.get('movimiento'))
                if not item or _day(item.get('posting_date')) > cutoff:
                    continue
                claim = item.get('claim_id') or ''
                key = claim[2:]
                if not ((claim.startswith('H:') and key in eligible_sources)
                        or (claim.startswith('C:') and key in collections and key not in future_collections
                            and collection_sources[key] & eligible_sources.keys())):
                    continue
            else:
                raise ValueError(f"{deposit['name']}: tipo de distribución no reconocido para el corte.")
            entries.append((entry, key))
        assigned = sum_money(entry.get('importe_usd') for entry, key in entries)
        if deposit.get('amount_usd') is None:
            raise ValueError(f"{deposit['name']}: falta la conversión a US$ para reconstruir la distribución.")
        if surplus > money(deposit['amount_usd']):
            raise ValueError(f"{deposit['name']}: los saldos a favor superan el depósito al corte.")
        if assigned + surplus > money(deposit['amount_usd']) or assigned < 0:
            warnings.append(f"{deposit['name']}: la distribución requiere una partida posterior al corte; efectivo conservado sin asignar, sin inventar distribución por cliente.")
            entries, assigned = [], money(0)
        deposit['allocated_usd'] = money_float(assigned)
        for entry, key in entries:
            amount, kind = money(entry.get('importe_usd')), entry['tipo']
            if kind == 'Partida complementaria':
                distributions[key].append(dict(deposit=deposit['name'], date=str(deposit['deposit_date']),
                    amount_usd=money_float(amount), period=entry.get('periodo') or '',
                    client=entry.get('cliente') or '', employer=entry.get('empresa') or ''))
                continue
            is_historical = kind == 'Aplicacion historica' or (kind == 'Movimiento de conciliación'
                and projected_items[entry['movimiento']].get('claim_id', '').startswith('H:'))
            detail = dict(importe_usd=money_float(amount), fecha=str(deposit['deposit_date']),
                          referencia=deposit['name'], diferencia_usd=entry.get('diferencia_usd') or 0)
            if is_historical:
                source = eligible_sources[key]
                source['historical_remitted_usd'] = money_float(money(source['historical_remitted_usd']) + amount)
                source['historical_detail'].append(detail)
                source['match_status'] = 'Conciliado'
            else:
                collections[key]['remittance_detail'].append(detail)
                collections[key]['rounding_adjustment_usd'] = money_float(
                    money(collections[key]['rounding_adjustment_usd']) + money(detail['diferencia_usd']))

    balances = {name: financial_balance(item, distributions[name]) for name, item in projected_items.items()}
    core = []
    for name, item in projected_items.items():
        if item.get('accounting_source_key'):
            item['_balance'] = balances[name]
            item['_signed_pending_usd'] = signed_pending(item, balances[name])
            core.append(item)
    applications = application_balances(list(eligible_sources.values()), imports, periods, collections,
                                        employers, cutoff, include_settled=True)
    receivables = build_receivables(list(projected_items.values()), balances,
        settlements=list(projected_items.values()), settlement_balances=balances)
    detail = build_detail(applications, core, projected_items.values(), projected_deposits, filters,
                          adjustment_receivables=receivables)
    return dict(cutoff_date=str(cutoff), detail=detail, summary=summarize(detail),
                applications=applications, receivables=receivables, periods=periods,
                warnings=sorted(set(warnings)))


def load_cutoff(filters):
    """Permission-scoped, batched reads; cross-company payments are not date-scoped early."""
    from frappe.utils import getdate, nowdate
    required = ('CN Accounting Import', 'CN Reconciliation Period', 'CN Remittance Allocation', 'CN Complementary Item')
    if any(not frappe.has_permission(doctype, 'read') for doctype in required):
        frappe.throw('Necesita permiso de lectura de aplicaciones, períodos, depósitos y partidas complementarias.', frappe.PermissionError)
    try:
        cutoff = _day(filters.get('cutoff_date'))
        if cutoff > getdate(nowdate()):
            raise ValueError('La fecha de corte no puede ser futura.')
        if filters.get('to_date') and _day(filters['to_date']) > cutoff:
            raise ValueError('Hasta no puede ser posterior a la fecha de corte.')
        if filters.get('from_date') and _day(filters['from_date']) > cutoff:
            raise ValueError('Desde no puede ser posterior a la fecha de corte.')
        if filters.get('from_date') and filters.get('to_date') and _day(filters['from_date']) > _day(filters['to_date']):
            raise ValueError('La fecha Desde no puede ser posterior a Hasta.')
        fields = list(dict.fromkeys([*FIELDS, 'accounting_source_key', 'source_date', 'source_debit', 'source_credit',
            'source_voucher', 'source_row', 'source_description', 'source_client_name', 'client_name', 'client_number',
            'loan_number', 'reference', 'voucher', 'registered_deposit', 'credit_history', 'credit_resolved_usd',
            'claim_id', 'accounting_exception']))
        items = list(records('CN Complementary Item', filters={'docstatus': ['!=', 2]}, fields=fields))
        # The reverse link is on the originating item. No unrestricted Exception read.
        origins = {item.get('accounting_exception'): item['name'] for item in items if item.get('accounting_exception')}
        for item in items:
            item['_registration_origin'] = origins.get(item.get('registration_exception'))
        offsets = list(child_records('CN Complementary Offset', [item['name'] for item in items],
            'CN Complementary Item', 'compensations', fields=['parent', 'compensation_date', 'amount_usd']))
        deposits = list(records('CN Remittance Allocation', filters={'docstatus': ['!=', 2]}, fields=[
            'name', 'employer', 'docstatus', 'accounting_source_key', 'deposit_date', 'deposit_reference',
            'amount_usd', 'allocation_detail', 'result', 'notes']))
        return reconstruct(load_application_context(), deposits, items, offsets, cutoff, filters)
    except ValueError as error:
        frappe.throw(str(error))


def cutoff_message(result):
    from html import escape
    from frappe.utils import formatdate
    message = ('Corte histórico al {0}, por fecha efectiva y vínculos vigentes. '
        'Excluye operaciones posteriores; conserva las correcciones y cancelaciones actuales. '
        'No reproduce lo que se conocía entonces. La cobranza no es CxC. '
        'Desde/Hasta solo limitan la fecha de origen.').format(escape(formatdate(result['cutoff_date'])))
    if result['warnings']:
        message += '<br><strong>Advertencias del corte:</strong><ul>' + ''.join(
            '<li>' + escape(warning) + '</li>' for warning in result['warnings']) + '</ul>'
    return message
