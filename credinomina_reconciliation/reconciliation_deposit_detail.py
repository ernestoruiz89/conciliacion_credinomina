"""Read-only receipt detail and undistributed cash, without inventing period splits."""
from collections import defaultdict
from decimal import Decimal
import json

import frappe

from credinomina_reconciliation.company_conversion import KIND as COMPANY_DIFFERENCE
from credinomina_reconciliation.deposit_accounting_sources import is_linked_origin
from credinomina_reconciliation.report_records import records, child_records, require_complete_evidence, PAGE_SIZE
from credinomina_reconciliation.rounding import money, money_float, sum_money


def build(periods, deposits, details, period_links):
    selected = {period['name'] for period in periods}
    by_deposit, links = defaultdict(list), defaultdict(set)
    for row in details:
        if row.get('row_kind') != 'Diferencia documentada':
            by_deposit[row['parent']].append(row)
    for link in period_links:
        if link.get('period'):
            links[link['parent']].add(link['period'])
    output = []
    for deposit in deposits:
        if deposit.get('docstatus') != 1 or is_linked_origin(deposit):
            continue
        rows = by_deposit[deposit['name']]
        if not rows:
            continue
        paid, row_periods, ledger_periods = defaultdict(lambda: money(0)), defaultdict(set), set()
        valid, total, company_difference = True, money(0), money(0)
        try:
            ledger = json.loads(deposit.get('allocation_detail') or '[]')
            if not isinstance(ledger, list) or any(not isinstance(entry, dict) for entry in ledger):
                raise ValueError()
            names = {row['name'] for row in rows}
            for entry in ledger:
                if not Decimal(str(entry.get('importe_usd'))).is_finite():
                    raise ValueError()
                amount = money(entry.get('importe_usd'))
                total += amount
                if entry.get('periodo'):
                    ledger_periods.add(entry['periodo'])
                if entry.get('tipo') == COMPANY_DIFFERENCE:
                    proof = entry.get('conversion_empresa') or {}
                    if (proof.get('deposit') != deposit['name'] or money(proof.get('amount_usd')) != amount
                            or amount != money(-.01) or not proof.get('application_ids')):
                        raise ValueError()
                    company_difference += amount
                    continue
                if entry.get('tipo') not in {'Aplicacion historica', 'Cobranza', 'Partida complementaria', 'Movimiento de conciliación'}:
                    raise ValueError()
                name = entry.get('fila_detalle')
                if name in names:
                    paid[name] += amount
                    if entry.get('periodo'):
                        row_periods[name].add(entry['periodo'])
                elif amount:
                    valid = False
            valid = valid and total == money(deposit.get('allocated_usd'))
        except (ValueError, TypeError, ArithmeticError):
            valid = False
        amounts = {row['name']: money(row.get('amount_usd')) for row in rows}
        pending = {row['name']: amounts[row['name']] - paid[row['name']]
                   - money(row.get('client_credit_usd')) - money(row.get('company_credit_usd')) for row in rows}
        remaining = money(deposit.get('amount_usd')) - money(deposit.get('allocated_usd')) - money(deposit.get('justified_surplus_usd'))
        valid = (valid and remaining >= 0 and remaining == money(deposit.get('unclassified_usd'))
                 and money(deposit.get('allocated_usd')) >= 0 and money(deposit.get('justified_surplus_usd')) >= 0
                 and all(paid[row['name']] >= 0 and money(row.get('client_credit_usd')) >= 0
                         and money(row.get('company_credit_usd')) >= 0 for row in rows)
                 and all(row.get('amount_usd') is not None and 0 <= pending[row['name']] <= amounts[row['name']] for row in rows)
                 and sum_money(amounts.values()) <= money(deposit.get('amount_usd')) - company_difference
                 and sum_money(money(row.get('client_credit_usd')) + money(row.get('company_credit_usd')) for row in rows)
                     <= money(deposit.get('justified_surplus_usd'))
                 and sum_money(pending.values()) <= remaining)
        for row in rows:
            scope = row_periods[row['name']] or links[deposit['name']] or ledger_periods
            reference = f"{deposit['name']} · fila {row.get('source_row') or row['name']}"
            # Disclose ambiguity on affected periods, but never repeat the full
            # receipt amount or allocate it by proportions.
            destinations = scope & selected
            for period in sorted(destinations):
                ambiguous = len(scope) != 1
                note = (f'{reference}: detalle sin atribución única a un período; no se repartió entre períodos.' if ambiguous else
                        f'{reference}: revise la distribución antes de atribuir el efectivo pendiente al cliente.' if not valid else
                        f'{reference}: importe del detalle US$ {amounts[row["name"]]:.2f}; recibido sin distribuir US$ {pending[row["name"]]:.2f}.'
                        if pending[row['name']] > 0 else '')
                output.append(dict(period=period, source=dict(row),
                    detail_received_usd=None if ambiguous or row.get('amount_usd') is None else money_float(amounts[row['name']]),
                    detail_unassigned_usd=None if ambiguous or not valid else money_float(pending[row['name']]),
                    observation=note))
    return output


def load(periods):
    if not periods:
        return []
    if not frappe.has_permission('CN Remittance Allocation', 'read'):
        return [dict(period=period['name'], source=dict(name='DETAIL-UNKNOWN', client_name='Detalle del depósito no disponible'),
                     detail_received_usd=None, detail_unassigned_usd=None,
                     unavailable=True,
                     observation='Importe del detalle no determinable: falta permiso para consultar depósitos.') for period in periods]
    from credinomina_reconciliation.remittance_periods import deposit_names_for_periods
    deposits = {}
    names = sorted(period['name'] for period in periods)
    for start in range(0, len(names), PAGE_SIZE):
        batch = names[start:start + PAGE_SIZE]
        direct = deposit_names_for_periods(batch)
        patterns = ['%' + json.dumps(name, ensure_ascii=ascii_only).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                    for name in batch for ascii_only in (False, True)]
        alternatives = [['allocation_detail', 'like', pattern] for pattern in sorted(set(patterns))]
        if direct:
            alternatives.append(['name', 'in', direct])
        filters = {'docstatus': 1, 'consolidated_deposit': ['is', 'not set']}
        found = list(records('CN Remittance Allocation', filters=filters, or_filters=alternatives,
            fields=['name', 'docstatus', 'employer', 'amount_usd', 'allocated_usd', 'justified_surplus_usd',
                    'unclassified_usd', 'cash_identity_status', 'consolidated_deposit', 'allocation_detail']))
        require_complete_evidence('CN Remittance Allocation', [row['name'] for row in found],
                                  filters=filters, or_filters=alternatives)
        deposits.update({row['name']: row for row in found})
    details = list(child_records('CN Remittance Detail', deposits, 'CN Remittance Allocation', 'detail_rows',
        fields=['name', 'parent', 'source_row', 'row_kind', 'employer', 'client', 'client_number', 'client_name', 'national_id',
                'loan_number', 'amount_usd', 'client_credit_usd', 'company_credit_usd']))
    period_links = list(child_records('CN Remittance Period', deposits, 'CN Remittance Allocation', 'detail_periods',
                                     fields=['parent', 'period']))
    return build(periods, list(deposits.values()), details, period_links)
