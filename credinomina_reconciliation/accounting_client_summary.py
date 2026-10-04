"""Read-only, import-scoped customer balances; never invent a cash split."""
import json
from collections import defaultdict

import frappe

from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.rounding import money, money_float, sum_money


def entries(value):
    result = json.loads(value or '[]') if isinstance(value, str) else value or []
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise ValueError('Detalle de distribución inválido; revise la conciliación.')
    return result


def collection_links(row, net):
    if row.get('match_status') not in {'Conciliado', 'Enlace provisional'}:
        return []
    links = entries(row.get('application_allocation_detail'))
    if not links and row.get('collection_row_id'):
        links = [{'collection_row_id': row.get('collection_row_id'), 'amount_usd': net}]
    return links


def build_summary(document, collections):
    customers, claims = {}, defaultdict(list)

    def add(group, net, paid=0, rounding=0, reason=''):
        for field, value in [('applied_usd', net), ('assigned_usd', paid), ('rounding_usd', rounding)]:
            group[field] = None if value is None or group[field] is None else group[field] + money(value)
        balance = None if any(v is None for v in (net, paid, rounding)) else max(money(net) + money(rounding) - money(paid), money(0))
        group['balance_usd'] = None if balance is None or group['balance_usd'] is None else group['balance_usd'] + balance
        if net is not None and paid is not None and rounding is not None and money(paid) > money(net) + money(rounding):
            reason = 'Asignación mayor al aplicado neto; revise la distribución.'
        if reason:
            group['observations'].add(reason)

    for index, row in enumerate(document.get('rows') or []):
        if row.get('event_type') != 'Aplicacion' or not row.get('effective') or row.get('match_status') == 'Ignorado':
            continue
        identity = next(((field, str(row.get(field))) for field in ('client_number', 'client', 'national_id', 'loan_number') if row.get(field)), ('row', row.get('name') or index))
        group = customers.setdefault(identity, dict(client_name=row.get('client_name') or 'Sin identificar',
            client_number=row.get('client_number') or '', loans=set(), applications=0,
            applied_usd=money(0), assigned_usd=money(0), rounding_usd=money(0), balance_usd=money(0), observations=set()))
        group['applications'] += 1
        if row.get('loan_number'):
            group['loans'].add(row.get('loan_number'))
        net = converted_amount(row, 'USD')
        if net is None:
            add(group, None, None, None, 'Falta conversión a US$.')
            continue
        historical = row.get('historical_period') or document.get('historical_period') or document.get('historical_backfill') or row.get('processing_route') == 'Historica'
        if historical:
            linked = row.get('match_status') == 'Conciliado' and (row.get('historical_period') or document.get('historical_period'))
            add(group, net, row.get('historical_remitted_usd') or 0 if linked else 0,
                sum_money(item.get('diferencia_usd') for item in entries(row.get('historical_detail'))) if linked else 0)
            continue
        links = collection_links(row, net)
        linked = sum_money(link.get('amount_usd') for link in links)
        if linked > money(net) or any(money(link.get('amount_usd')) < 0 for link in links):
            add(group, net, None, None, 'Vínculos de aplicación inconsistentes; revise la conciliación.')
            continue
        for link in links:
            claims[link.get('collection_row_id')].append((group, money(link.get('amount_usd'))))
        add(group, money(net) - linked)

    for claim, parts in claims.items():
        collection = collections.get(claim)
        local = sum_money(amount for _, amount in parts)
        groups = {id(group) for group, _ in parts}
        cash = sum_money(item.get('importe_usd') for item in entries(collection.get('remittance_detail'))
                         if item.get('destino') != 'Partida complementaria') if collection else 0
        rounding = money(collection.get('rounding_adjustment_usd')) if collection else 0
        uncertain = not collection or len(groups) > 1 or ((cash or rounding) and local != money(collection.get('applied_usd')))
        if uncertain:
            for group, amount in parts:
                add(group, amount, None, None, 'Distribución compartida o no disponible: no se puede atribuir el depósito a esta importación. Consulte el período.')
        else:
            add(parts[0][0], local, cash, rounding)

    rows = []
    for group in customers.values():
        group['status'] = ('Revisar' if group['observations'] else 'Parcial' if group['balance_usd'] > 0 and group['assigned_usd'] > 0
                           else 'Pendiente' if group['balance_usd'] > 0 else 'Conciliado')
        rows.append({**group, 'loans': sorted(group['loans']), 'observations': sorted(group['observations']),
                     **{field: None if group[field] is None else money_float(group[field]) for field in ('applied_usd', 'assigned_usd', 'rounding_usd', 'balance_usd')}})
    rows.sort(key=lambda row: (row['client_name'].casefold(), row['client_number']))
    return rows


@frappe.whitelist()
def get_client_summary(import_name):
    document = frappe.get_doc('CN Accounting Import', import_name)
    document.check_permission('read')
    ids = set()
    for row in document.rows:
        if row.event_type == 'Aplicacion' and row.effective and not (row.historical_period or document.historical_period or document.historical_backfill or row.processing_route == 'Historica'):
            ids.update(link.get('collection_row_id') for link in collection_links(row, converted_amount(row, 'USD')))
    collections, readable = {}, {}
    names = sorted(name for name in ids if name)
    for offset in range(0, len(names), 500):
        for row in frappe.get_all('CN Collection Row', filters={'name': ['in', names[offset:offset + 500]], 'parenttype': 'CN Reconciliation Period', 'parentfield': 'collection_rows'},
                                 fields=['name', 'parent', 'applied_usd', 'remittance_detail', 'rounding_adjustment_usd'], limit_page_length=0):
            if row.parent not in readable:
                readable[row.parent] = frappe.has_permission('CN Reconciliation Period', 'read', doc=row.parent)
            if readable[row.parent]:
                collections[row.name] = row
    return {'rows': build_summary(document, collections)}
