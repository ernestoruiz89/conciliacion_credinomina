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


def _legacy_manual_details(rows, targets, ledger):
    """Reuse reconciliation's proof for old applied targets with an empty display cache."""
    from credinomina_reconciliation.deposit_reconciliation import _legacy_manual_rows

    empty = {name for name, row in rows.items() if read_targets(row.get('matched_targets') or '[]') == []}
    if not empty:
        return {}
    allocations = []
    for entry in ledger:
        key = target_key(entry)
        if key and key[0] in {'H', 'X'}:
            allocations.append(dict(claim_id=key[0] + ':' + key[1], amount_usd=entry.get('importe_usd'),
                origin=entry.get('origen'), detail_row=entry.get('fila_detalle'),
                reclassification_operation_id=entry.get('reclassification_operation_id')))
    document = frappe._dict(detail_rows=[frappe._dict(row) for row in rows.values()],
                            targets=[frappe._dict(target) for target in targets])
    verified = _legacy_manual_rows(document, allocations)
    return {claim: row for claim, row in verified.items() if row in empty}


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
        legacy = _legacy_manual_details(rows, targets, ledger)
        for key in missing:
            cash = _confirmed_manual_cash(key, payments[key], instructions[key], saved)
            legacy_row = legacy.get('H:' + key[1]) if key[0] == 'H' else None
            if cash is None and legacy_row and all(not detail for detail, _ in payments[key]):
                cash = {legacy_row: sum_money(amount for _, amount in payments[key])}
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
        if deposit.get('consolidated_deposit') or deposit.get('cash_identity_status') in {'Origen vinculado', 'Revisar identidad'}:
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
        base = dict(position_type='Depósito con saldo pendiente', event_date=deposit.get('deposit_date'),
                    source_doctype='CN Remittance Allocation', source_document=deposit['name'],
                    employer=deposit.get('employer'), operational_status='Pendiente' if consistent else 'Revisar distribución', is_open=True,
                    usd_currency='USD')
        attributed = money(0)
        for row in pending:
            beneficiary = row.get('employer') or base['employer']
            if beneficiary == base['employer'] and not any(
                    row.get(field) for field in ('client', 'client_number', 'national_id', 'loan_number', 'client_name')):
                continue
            amount = pending_by_row[row['name']]
            position = dict(base, employer=beneficiary,
                client_name=row.get('client_name') or 'Sin cliente individual', client_number=row.get('client') or row.get('client_number'),
                national_id=row.get('national_id'), loan_number=row.get('loan_number'), source_rows=row['name'],
                deposit_detail_pending_usd=money_float(amount), difference_reason=row.get('difference_reason'))
            if consistent:
                attributed += amount
                position.update(deposit_pending_usd=money_float(amount), observation='Importe pendiente del detalle del depósito.')
            else:
                position.update(deposit_pending_usd=None,
                    observation='Importe del detalle por comprobar; efectivo individual sin determinar. Revise la distribución del depósito.')
            output.append(position)
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
    scope = {}
    if employer:
        # Stored beneficiaries can differ from the payer, even after a paying
        # agreement changes. Only permission-checked parents supply balances.
        parents = frappe.get_all('CN Remittance Detail', filters={'employer': employer,
            'parenttype': 'CN Remittance Allocation', 'parentfield': 'detail_rows'},
            pluck='parent', group_by='parent', order_by='parent asc', limit_page_length=0)
        scope['or_filters'] = [['employer', '=', employer]]
        if parents:
            scope['or_filters'].append(['name', 'in', parents])
    deposits = list(records('CN Remittance Allocation', filters={'docstatus': 1, 'consolidated_deposit': ['is', 'not set']},
        fields=['name', 'docstatus', 'employer', 'deposit_date', 'amount_usd', 'allocated_usd', 'justified_surplus_usd',
                'unclassified_usd', 'cash_identity_status', 'allocation_detail'], **scope))
    deposits = [d for d in deposits if remaining_cash(d) > 0]
    details = child_records('CN Remittance Detail', [d['name'] for d in deposits], 'CN Remittance Allocation', 'detail_rows',
        fields=['name', 'parent', 'row_kind', 'employer', 'client', 'client_name', 'client_number', 'national_id', 'loan_number',
                'amount_usd', 'linked_usd', 'client_credit_usd', 'company_credit_usd', 'matched_targets', 'difference_reason'])
    positions = build(deposits, details, load_manual_targets(deposits))
    return [row for row in positions if not employer or row['employer'] == employer]
