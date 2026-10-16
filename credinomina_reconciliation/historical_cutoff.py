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
from credinomina_reconciliation.accounting_client_summary import collection_links, collection_link_issue
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


def reconstruct(context, deposits, items, offsets, cutoff_date, filters=None, *, include_bridge=False):
    """Pure projection. Inputs are copied, and no reconciliation/write is executed."""
    from credinomina_reconciliation.company_statement import build_detail, summarize

    cutoff = _day(cutoff_date)
    sources, imports, periods, collections, employers = deepcopy(context)
    deposits, items = deepcopy(deposits), deepcopy(items)
    all_sources = {row['name']: row for row in sources}
    live_items = {row['name']: row for row in items if row.get('docstatus') != 2}
    visible_deposits = {row['name']: row for row in deposits if row.get('docstatus') != 2}
    warnings = []
    # Audit the ORIGINAL links before projecting dates. A cut must not repair
    # over-allocation, negatives or shared capacity errors merely by clipping.
    current_links, link_issues, usage = {}, {}, defaultdict(lambda: money(0))
    for row in sources:
        parent = imports.get(row.get('parent'), {})
        if (row.get('historical_period') or parent.get('historical_period')
                or parent.get('historical_backfill') or row.get('processing_route') == 'Historica'):
            continue
        try:
            links = collection_links(row, converted_amount(row, 'USD'))
            current_links[row['name']] = links
            for link in links:
                usage[link.get('collection_row_id')] += max(money(link.get('amount_usd')), 0)
        except (ValueError, TypeError):
            link_issues[row['name']] = 'Detalle de distribución inválido; revise los vínculos de la aplicación.'
    for row in sources:
        if row['name'] in current_links:
            issue = collection_link_issue(current_links[row['name']], converted_amount(row, 'USD'), collections, usage)
            if issue:
                link_issues[row['name']] = issue
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
                        application_adjustment_history='[]', registration_exception='', review_action='', compensated_usd=0)
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
            if item.get('credit_usage_issue'):
                # Preserve the row as an unknown balance, never a fictitious zero.
                projected_items[name] = item
                continue
            from credinomina_reconciliation.credit_usage import consuming_offsets
            consuming = consuming_offsets(ledger)
            if money(item.get('credit_resolved_usd')) != (sum_money(row.get('importe_usd') for row in history)
                    + sum_money(row.get('amount_usd') for row in consuming)):
                raise ValueError(f'{name}: el saldo gestionado no coincide con su historial fechado.')
            resolved = (_dated_total(history, 'fecha', 'importe_usd', cutoff)
                        + _dated_total(consuming, 'compensation_date', 'amount_usd', cutoff))
            pending = money(item.get('amount_usd')) - resolved
            if pending < 0 or resolved < 0:
                raise ValueError(f'{name}: el historial de gestión produce un saldo inválido al corte.')
            item.update(credit_resolved_usd=money_float(resolved), credit_pending_usd=money_float(pending),
                        credit_management_status='Resuelto' if not pending else 'Parcialmente resuelto' if resolved else 'Pendiente')
        projected_items[name] = item

    adjustments = defaultdict(lambda: money(0))
    from credinomina_reconciliation.application_adjustment_links import links
    for item in projected_items.values():
        if (item.get('docstatus') == 1 and item.get('category') == 'Ajuste de aplicación'
                and item.get('related_application')):
            for link in links(item):
                adjustments[link.related_application] += money(link.application_adjustment_usd)
    eligible_sources = {}
    future_collections = set()
    collection_sources = defaultdict(set)
    for row in sources:
        if row.get('event_type') != 'Aplicacion' or not row.get('effective') or row.get('match_status') == 'Ignorado':
            continue
        links = current_links.get(row['name'], [])
        if row['name'] in link_issues:
            row['_cutoff_distribution_issue'] = link_issues[row['name']]
            warnings.append(f"{row['name']}: {link_issues[row['name']]}")
        if not links and row.get('collection_row_id') and row['name'] not in link_issues:
            links = [dict(collection_row_id=row['collection_row_id'], amount_usd=converted_amount(row, 'USD'))]
        in_cutoff = _day(row.get('event_date')) <= cutoff
        for link in links:
            key = link.get('collection_row_id')
            collection_sources[key].add(row['name'])
            if not in_cutoff:
                future_collections.add(key)
        if not in_cutoff:
            continue
        current_applied = converted_amount(row, 'USD')
        row.update(application_adjustment_usd=money_float(adjustments[row['name']]),
                   historical_remitted_usd=0, historical_detail=[])
        # A later application adjustment may have reduced today's links. The
        # restored excess stays pending, never silently disappears from the cut.
        applied = converted_amount(row, 'USD')
        if links and applied is not None and row['name'] not in link_issues:
            # Only a fully linked SINGLE claim can absorb the known dated
            # application adjustment without inventing an allocation order.
            if len(links) == 1 and money(links[0].get('amount_usd')) == money(current_applied):
                links = [dict(links[0], amount_usd=money_float(applied))]
            elif sum_money(link.get('amount_usd') for link in links) > money(applied):
                row['_cutoff_distribution_issue'] = 'No se puede atribuir el ajuste fechado entre varias cobranzas al corte; revise la distribución.'
                warnings.append(f"{row['name']}: {row['_cutoff_distribution_issue']}")
            row['application_allocation_detail'] = links
        eligible_sources[row['name']] = row
    projected_capacity = defaultdict(lambda: money(0))
    for row in eligible_sources.values():
        if row.get('_cutoff_distribution_issue'):
            continue
        for link in collection_links(row, converted_amount(row, 'USD')):
            projected_capacity[link.get('collection_row_id')] += money(link.get('amount_usd'))
    collection_keys = {}
    for name, row in collections.items():
        row.update(remittance_detail=[], rounding_adjustment_usd=0, fx_variance_usd=0)
        row['applied_usd'] = money_float(projected_capacity[name])
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

    from credinomina_reconciliation.credit_application import journal_allocations
    for entry in journal_allocations(projected_items.values(), set(eligible_sources), cutoff):
        source = eligible_sources[entry['application_id']]
        source['historical_remitted_usd'] = money_float(money(source['historical_remitted_usd']) + money(entry['importe_usd']))
        source['historical_detail'].append(dict(importe_usd=entry['importe_usd'], fecha=entry['fecha'],
            referencia=entry['item'], origen='Saldo a favor', saldo_a_favor=entry['item'],
            deposito_origen=entry['deposit'], operacion=entry['operation_id']))
        source['match_status'] = 'Conciliado'

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
                          adjustment_receivables=receivables, accounting_items=projected_items.values())
    result = dict(cutoff_date=str(cutoff), detail=detail, summary=summarize(detail),
                applications=applications, receivables=receivables, periods=periods,
                warnings=sorted(set(warnings)))
    if include_bridge:
        # Reuse the exact effective-date projection. Do not read today's totals
        # or the unfiltered allocation journal to explain a historical balance.
        result['bridge_context'] = dict(items=list(projected_items.values()),
            deposits=projected_deposits, distributions=dict(distributions),
            offsets=[row for row in offsets if _day(row.get('compensation_date')) <= cutoff],
            sources=[dict(row, bridge_applied_usd=converted_amount(row, 'USD'),
                employer=(periods.get(row.get('historical_period') or imports.get(row.get('parent'), {}).get('historical_period')) or {}).get('employer')
                    or imports.get(row.get('parent'), {}).get('employer') or row.get('portfolio_employer'))
                for row in eligible_sources.values()])
    return result


def load_cutoff(filters, *, include_bridge=False):
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
            'claim_id', 'accounting_exception', 'reason_type', 'subcategory']))
        items = list(records('CN Complementary Item', filters={'docstatus': ['!=', 2]}, fields=fields))
        # The reverse link is on the originating item. No unrestricted Exception read.
        origins = {item.get('accounting_exception'): item['name'] for item in items if item.get('accounting_exception')}
        for item in items:
            item['_registration_origin'] = origins.get(item.get('registration_exception'))
        offsets = list(child_records('CN Complementary Offset', [item['name'] for item in items],
            'CN Complementary Item', 'compensations', fields=['parent', 'compensation_date', 'amount_usd',
                'credit_effect', 'credit_management_item', 'credit_management_entry',
                *(['counterpart'] if include_bridge else [])]))
        deposits = list(records('CN Remittance Allocation', filters={'docstatus': ['!=', 2]}, fields=[
            'name', 'employer', 'docstatus', 'accounting_source_key', 'deposit_date', 'deposit_reference',
            'amount_usd', 'allocation_detail', 'result', 'notes', 'cash_identity_status', 'cash_identity_reason', 'consolidated_deposit',
            *(['deposit_currency', 'deposit_amount', 'fx_rate', 'deposit_voucher'] if include_bridge else [])]))
        # Current cash journals are discarded by reconstruct(). Read only the
        # links/capacities needed to audit them; replay cash from dated deposits.
        context = load_application_context(include_current_settlements=False)
        from credinomina_reconciliation.cutoff_evidence import require_cutoff_evidence
        require_cutoff_evidence(context, deposits, items, cutoff)
        return reconstruct(context, deposits, items, offsets, cutoff, filters, include_bridge=include_bridge)
    except ValueError as error:
        frappe.throw(str(error))


def cutoff_message(result):
    from html import escape
    from frappe.utils import formatdate
    message = ('Corte histórico al {0}, por fecha efectiva y vínculos vigentes. '
        'Excluye operaciones posteriores; conserva las correcciones y cancelaciones actuales. '
        'No reproduce lo que se conocía entonces. El estado de registro contable es el actual. La cobranza no es CxC. '
        'Desde/Hasta solo limitan la fecha de origen.').format(escape(formatdate(result['cutoff_date'])))
    if result['warnings']:
        message += '<br><strong>Advertencias del corte:</strong><ul>' + ''.join(
            '<li>' + escape(warning) + '</li>' for warning in result['warnings']) + '</ul>'
    return message
