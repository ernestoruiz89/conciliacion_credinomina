"""Read-only decomposition of unassigned deposits using their classified detail."""
from collections import defaultdict
import json

from credinomina_reconciliation.rounding import money, money_float, sum_money

DETAIL_FIELDS = ['name', 'parent', 'idx', 'source_row', 'row_kind', 'employer', 'client_name', 'client_number',
    'loan_number', 'amount_usd', 'deducted_usd', 'deducted_nio', 'client_credit_usd', 'company_credit_usd',
    'difference_amount_usd', 'difference_reason', 'difference_notes']
COMPONENTS = {'A Favor Cliente': 'client_credit_usd', 'A Favor Empresa': 'company_credit_usd'}


def attach_details(deposits):
    from credinomina_reconciliation.report_records import child_records
    by_name = {row['name']: row for row in deposits}
    for deposit in deposits:
        deposit['_statement_detail_rows'] = []
    if by_name:
        for row in child_records('CN Remittance Detail', by_name, 'CN Remittance Allocation', 'detail_rows',
                                 fields=DETAIL_FIELDS, order_by='idx asc, name asc'):
            by_name[row['parent']]['_statement_detail_rows'].append(row)


def prepare_details(deposit, entries=None, credits=None):
    """Use settled cash only; historical callers supply their dated projection."""
    rows = deposit.get('_statement_detail_rows') or []
    if not rows or deposit.get('docstatus') != 1:
        return {}
    if entries is None:
        entries = deposit.get('allocation_detail') or []
        if isinstance(entries, str):
            try:
                entries = json.loads(entries)
            except (TypeError, ValueError):
                return {}
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        return {}
    ordinary = {row['name']: row for row in rows if row.get('row_kind') != 'Diferencia documentada'}
    assigned = sum_money(entry.get('importe_usd') for entry in entries)
    if assigned != money(deposit.get('allocated_usd')):
        return {}  # Do not attribute stale or incomplete cash evidence to clients.
    by_row = defaultdict(lambda: money(0))
    for entry in entries:
        key = entry.get('fila_detalle')
        if money(entry.get('importe_usd')) and key not in ordinary:
            return {}  # Old aggregate distributions have no reliable client split.
        by_row[key] += money(entry.get('importe_usd'))
    reserved = defaultdict(lambda: money(0))
    if credits is None:
        for row in ordinary.values():
            reserved[row['name']] = money(row.get('client_credit_usd')) + money(row.get('company_credit_usd'))
    else:
        for item in credits:
            reserved[item.get('credit_detail_row')] += money(item.get('amount_usd'))
    prepared = []
    for row in ordinary.values():
        pending = money(row.get('amount_usd')) - by_row[row['name']] - reserved[row['name']]
        if row.get('difference_reason') and pending > 0:
            prepared.append(dict(row, _classified_pending_usd=money_float(pending)))
    # Only an explicit company difference matching the original detail may
    # explain a positive residual; never add a generic balancing receivable.
    original_gap = money(deposit.get('amount_usd')) - sum_money(row.get('amount_usd') for row in ordinary.values())
    companies = [row for row in rows if row.get('row_kind') == 'Diferencia documentada'
                 and money(row.get('difference_amount_usd')) == original_gap]
    return dict(rows=prepared, company=companies[0] if len(companies) == 1 else None, original_gap=money_float(original_gap))


def expand(detail, treatments):
    """Move detail credits out of the aggregate once, retaining the signed total."""
    from credinomina_reconciliation.company_statement import FIELDS

    result = []
    for source in detail:
        data = source.get('_deposit_detail_classification') or {}
        if (source.get('_component') != 'deposit_pending_usd' or source.get('statement_scope') != 'Gestión'
                or source.get('management_amount_usd') is None):
            result.append(source)
            continue
        classified = [row for row in data.get('rows', []) if treatments.get(row.get('difference_reason')) in COMPONENTS]
        if not classified:
            result.append(source)
            continue
        amount = money(source['management_amount_usd'])
        total = sum_money(row['_classified_pending_usd'] for row in classified)
        residual = amount + total
        company = data.get('company')
        if amount > 0 or (residual > 0 and (not company or residual != -money(data.get('original_gap')))):
            result.append(dict(source, observation='; '.join(filter(None, [source.get('observation'),
                'El detalle clasificado supera el efectivo disponible sin una diferencia de empresa coincidente; revise la distribución.']))))
            continue
        for row in classified:
            component = COMPONENTS[treatments[row['difference_reason']]]
            signed = -money_float(row['_classified_pending_usd'])
            result.append(dict(source, **{field: signed if field == component else 0.0 for field in FIELDS},
                management_amount_usd=signed, _component=component, _deposit_detail_classification=None,
                position_type='Saldo a favor del cliente' if component == 'client_credit_usd' else 'Saldo a favor de la empresa',
                client_name=row.get('client_name'), client_number=row.get('client_number'), loan_number=row.get('loan_number'),
                difference_reason=row['difference_reason'], detail_row_id=row['name'],
                source_rows=str(row.get('source_row') or row.get('idx') or ''),
                observation='; '.join(filter(None, ['Remanente clasificado del detalle del depósito', row.get('difference_notes')]))))
        if residual:
            change = dict(source, **{field: money_float(residual) if field == 'deposit_pending_usd' else 0.0 for field in FIELDS},
                management_amount_usd=money_float(residual), _deposit_detail_classification=None)
            if residual > 0:
                change.update(position_type='Diferencia de empresa en el detalle', client_name=source.get('employer'),
                    difference_reason=company.get('difference_reason') or '', _deposit_classification_residual=True,
                    observation='El detalle supera el depósito. Esta diferencia conserva el saldo original del depósito; no crea una CxC ni una partida manual.')
            result.append(change)
    return result
