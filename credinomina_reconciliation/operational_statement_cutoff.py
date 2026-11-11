"""Operational statement from effective-date evidence, never today's cash totals."""
from collections import defaultdict
from copy import deepcopy

from credinomina_reconciliation.client_position import build_position, selected_position_types
from credinomina_reconciliation.historical_cutoff import load_cutoff
from credinomina_reconciliation.operational_statement_cash import apply_cash_breakdown
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.unallocated_deposit_position import build as build_deposits, load_manual_targets, remaining_cash


def load_snapshot(filters):
    return load_cutoff(dict(cutoff_date=filters['cutoff_date'],
        from_date=filters.get('from_month'), to_date=filters.get('to_month')), include_position=True)


def pending_deposits(snapshot):
    evidence = snapshot['position_context']
    deposits = [dict(deposit) for deposit in evidence['deposits'] if deposit.get('docstatus') == 1
        and deposit.get('cash_identity_status') not in {'Origen vinculado', 'Revisar identidad'}
        and not deposit.get('consolidated_deposit') and remaining_cash(deposit) > 0]
    if not deposits:
        return []
    # A currently documented credit may not exist at the cutoff. Only dated
    # reservations reduce the detail; refunds do not release its original cash.
    reserved = defaultdict(lambda: defaultdict(lambda: money(0)))
    for deposit in deposits:
        deposit['unclassified_usd'] = money_float(remaining_cash(deposit))
        for credit in evidence['credits_by_deposit'].get(deposit['name'], []):
            key = credit.get('credit_detail_row')
            field = 'company_credit_usd' if credit.get('category') == 'Saldo a favor de la empresa' else 'client_credit_usd'
            reserved[key][field] += money(credit.get('amount_usd'))
    details = []
    for row in child_records('CN Remittance Detail', [deposit['name'] for deposit in deposits],
            'CN Remittance Allocation', 'detail_rows', fields=['name', 'parent', 'row_kind', 'employer', 'client',
                'client_name', 'client_number', 'national_id', 'loan_number', 'amount_usd', 'linked_usd', 'matched_targets', 'difference_reason']):
        details.append(dict(row, **{field: money_float(reserved[row['name']][field])
            for field in ('client_credit_usd', 'company_credit_usd')}))
    return build_deposits(deposits, details, load_manual_targets(deposits))


def build(collections, snapshot, filters):
    kinds = selected_position_types(filters.get('position_type'))
    wants = lambda kind: kinds is None or kind in kinds
    evidence = snapshot['position_context']
    periods = snapshot['periods']
    applications = deepcopy(snapshot['applications']) if wants('Aplicación') else []
    if applications:
        # Force proof from this dated journal. Cached period totals are current.
        cash_periods = {name: dict(period, cash_breakdown_known=0, cash_breakdown_note='')
                        for name, period in periods.items()}
        apply_cash_breakdown(applications, cash_periods, evidence['collections'], deposits=evidence['deposits'])
    items = evidence['items'] if wants('Partida complementaria') else []
    receivables = deepcopy(snapshot['receivables']) if wants('CxC por ajuste') else []
    scope = {'employer': filters['employer']} if filters.get('employer') else {}
    clients = list(records('CN Client', filters=scope,
        fields=['name', 'client_number', 'client_name', 'national_id', 'employer'])) if items or receivables else []
    by_name = {client['name']: client for client in clients}
    by_number = {(client.get('employer'), client.get('client_number')): client for client in clients}
    for row in receivables:
        client = by_name.get(row.get('credit_client')) or by_number.get((row.get('employer'), row.get('client_number')))
        if client:
            row.update(client=client['name'], client_number=client.get('client_number'),
                client_name=row.get('client_name') or client.get('client_name'), national_id=client.get('national_id'))
    return build_position(collections, applications, items, evidence['balances'], clients, filters,
        period_cycles={name: period.get('collection_cycle') or ('Mensual' if period.get('reconciliation_mode') == 'Operativa' else '')
                       for name, period in periods.items()}, adjustment_receivables=receivables,
        unallocated_deposits=pending_deposits(snapshot) if wants('Depósito con saldo pendiente') else [])
