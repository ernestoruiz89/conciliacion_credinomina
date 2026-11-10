"""Read-only, non-overlapping portions of confirmed undistributed deposits."""
from collections import Counter, defaultdict
from decimal import Decimal
import json
import frappe

from credinomina_reconciliation.allocation_origin import MANUAL
from credinomina_reconciliation.remittance_selection import target_key
from credinomina_reconciliation.remittance_target_summary import read_targets
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float, sum_money


def remaining_cash(deposit):
    return money(deposit.get('amount_usd')) - money(deposit.get('allocated_usd')) - money(deposit.get('justified_surplus_usd'))


def _manual_key(entry):
    if entry.get('origen') == MANUAL and not entry.get('reclassification_operation_id'):
        return target_key(entry)


def _confirmed_manual_cash(key, payments, targets, saved_instructions):
    """Only return unlabelled cash whose signed amounts and recipient rows are proved."""
    expected = Counter()
    for target in targets:
        row = target.get('detail_row')
        matches = saved_instructions.get(target.get('name'), [])
        if target.get('result') != 'Aplicada' or len(matches) != 1 or matches[0][0] != row:
            return None
        # A target can be edited after reconciliation. Its original instruction
        # must still identify the same detail, destination and amount.
        match = matches[0][1]
        kind, _, name = str(match.get('claim_id') or '').partition(':')
        if kind != key[0] or not name or (kind != 'C' and name != key[1]):
            return None
        try:
            amount = money(target.get('amount_usd'))
            if not amount or amount != money(match.get('amount_usd')):
                return None
        except (ValueError, TypeError, ArithmeticError):
            return None
        expected[row, amount] += 1
    if not expected:
        return None
    missing = [amount for row, amount in payments if not row]
    if len(expected) == 1:
        (row, amount), count = next(iter(expected.items()))
        if (all((not explicit or explicit == row) and paid * amount > 0 for explicit, paid in payments)
                and sum_money(paid for _, paid in payments) == amount * count):
            return {row: sum_money(missing)}
        return None
    # Shared destinations require the exact recorded amounts. Consume payments
    # with explicit row IDs first so their cash cannot be attributed twice.
    explicit = Counter((row, amount) for row, amount in payments if row)
    if explicit - expected:
        return None
    remaining = expected - explicit
    by_amount, by_row = Counter(), defaultdict(lambda: money(0))
    for (row, amount), count in remaining.items():
        by_amount[amount] += count
        by_row[row] += amount * count
    return by_row if by_amount == Counter(missing) else None


def _executed_by_row(deposit, rows, targets):
    """Planned targets are not paid cash, even when a row's pending cache is zero."""
    amounts = defaultdict(lambda: money(0))
    try:
        raw = deposit.get('allocation_detail') or '[]'
        ledger = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(ledger, list) or any(not isinstance(entry, dict) for entry in ledger):
            return amounts, False
        total, complete = money(0), True
        payments, missing = defaultdict(list), set()
        for entry in ledger:
            if not Decimal(str(entry.get('importe_usd'))).is_finite():
                return amounts, False
            amount = money(entry.get('importe_usd'))
            if not amount.is_finite() or entry.get('tipo') not in {
                    'Aplicacion historica', 'Cobranza', 'Partida complementaria', 'Movimiento de conciliación'}:
                return defaultdict(lambda: money(0)), False
            total += amount
            detail = entry.get('fila_detalle')
            key = _manual_key(entry) if targets else None
            if key and amount:
                payments[key].append((detail, amount))
            if detail in rows:
                amounts[detail] += amount
            elif amount:
                if not detail and key and all(key):
                    missing.add(key)
                else:
                    complete = False
        if total != money(deposit.get('allocated_usd')):
            return amounts, False
        if not missing:
            return amounts, complete
        instructions, saved = defaultdict(list), defaultdict(list)
        for target in targets:
            instructions[target_key(target)].append(target)
        for row in rows.values():
            for match in read_targets(row.get('matched_targets')) or ():
                if match.get('instruction_id'):
                    saved[match['instruction_id']].append((row['name'], match))
        for key in missing:
            cash = _confirmed_manual_cash(key, payments[key], instructions[key], saved)
            if cash is None:
                complete = False
            else:
                for detail, amount in cash.items():
                    amounts[detail] += amount
        return amounts, complete
    except (ValueError, TypeError, ArithmeticError):
        return defaultdict(lambda: money(0)), False


def build(deposits, details, targets=()):
    grouped = defaultdict(list)
    for row in details:
        if row.get('row_kind') != 'Diferencia documentada':
            grouped[row['parent']].append(row)
    targets_by_deposit = defaultdict(list)
    for target in targets:
        targets_by_deposit[target['parent']].append(target)
    output = []
    for deposit in deposits:
        if deposit.get('cash_identity_status') in {'Origen vinculado', 'Revisar identidad'}:
            continue
        remaining = remaining_cash(deposit)
        if remaining <= 0:
            continue
        rows = grouped[deposit['name']]
        executed, complete = _executed_by_row(deposit, {row['name']: row for row in rows}, targets_by_deposit[deposit['name']])
        pending_by_row = {row['name']: money(row.get('amount_usd')) - executed[row['name']]
                          - money(row.get('client_credit_usd')) - money(row.get('company_credit_usd')) for row in rows}
        # A detail is attributable only when the complete set of positive
        # remainders fits in the actual unclassified cash pool.
        pending = [row for row in rows if pending_by_row[row['name']] > 0]
        consistent = (complete and money(deposit.get('unclassified_usd')) == remaining
                      and sum_money(row.get('amount_usd') for row in rows) <= money(deposit.get('amount_usd'))
                      and all(0 <= pending_by_row[row['name']] <= money(row.get('amount_usd')) for row in rows)
                      and sum_money(money(row.get('client_credit_usd')) + money(row.get('company_credit_usd')) for row in rows)
                          <= money(deposit.get('justified_surplus_usd'))
                      and sum_money(pending_by_row[row['name']] for row in pending) <= remaining)
        base = dict(position_type='Depósito sin asignar', event_date=deposit.get('deposit_date'),
                    source_doctype='CN Remittance Allocation', source_document=deposit['name'],
                    employer=deposit.get('employer'), operational_status='Pendiente' if consistent else 'Revisar distribución', is_open=True,
                    usd_currency='USD')
        attributed = money(0)
        if consistent:
            for row in pending:
                if not any(row.get(field) for field in ('client_number', 'national_id', 'loan_number', 'client_name')):
                    continue
                amount = pending_by_row[row['name']]
                attributed += amount
                output.append(dict(base, client_name=row.get('client_name'), client_number=row.get('client_number'),
                    national_id=row.get('national_id'), loan_number=row.get('loan_number'),
                    deposit_pending_usd=money_float(amount), source_rows=row['name'],
                    observation='Importe pendiente del detalle del depósito.'))
        else:
            for row in pending:
                if not any(row.get(field) for field in ('client_number', 'national_id', 'loan_number', 'client_name')):
                    continue
                output.append(dict(base, client_name=row.get('client_name'), client_number=row.get('client_number'),
                    national_id=row.get('national_id'), loan_number=row.get('loan_number'),
                    deposit_pending_usd=None, deposit_detail_pending_usd=money_float(pending_by_row[row['name']]),
                    source_rows=row['name'], observation='Importe del detalle por comprobar; efectivo individual sin determinar. Revise la distribución del depósito.'))
        remainder = remaining - attributed
        if remainder > 0:
            output.append(dict(base, client_name='Sin cliente individual', deposit_pending_usd=money_float(remainder),
                observation=('Efectivo pendiente sin atribución individual.' if consistent else
                             'Revise la distribución: el detalle no permite atribuir el efectivo pendiente por cliente.')))
    return output


def load_manual_targets(deposits):
    parents = [d['name'] for d in deposits if d.get('docstatus') == 1 and d.get('allocation_detail')]
    return child_records('CN Remittance Target', parents, 'CN Remittance Allocation', 'targets',
        fields=['name', 'parent', 'detail_row', 'historical_application', 'complementary_item', 'period', 'row_key',
                'amount_usd', 'result'])


def load(employer=None):
    if not frappe.has_permission('CN Remittance Allocation', 'read'):
        return []
    deposits = list(records('CN Remittance Allocation', filters={'docstatus': 1, **({'employer': employer} if employer else {})},
        fields=['name', 'docstatus', 'employer', 'deposit_date', 'amount_usd', 'allocated_usd', 'justified_surplus_usd',
                'unclassified_usd', 'cash_identity_status', 'allocation_detail']))
    deposits = [d for d in deposits if remaining_cash(d) > 0]
    details = child_records('CN Remittance Detail', [d['name'] for d in deposits], 'CN Remittance Allocation', 'detail_rows',
        fields=['name', 'parent', 'row_kind', 'client_name', 'client_number', 'national_id', 'loan_number',
                'amount_usd', 'client_credit_usd', 'company_credit_usd', 'matched_targets'])
    return build(deposits, details, load_manual_targets(deposits))
