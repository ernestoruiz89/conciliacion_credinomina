"""Bank cash displayed on application rows of Estado de Cuenta Operativo."""
from collections import defaultdict

from credinomina_reconciliation.reconciliation_report_cash import journal_evidence
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _show(row, amount, note):
    row['deposit_cash_usd'] = money_float(amount) if amount is not None else None
    row['observation'] = ' · '.join(filter(None, [row.get('observation'), note]))


def apply_cash_breakdown(applications, periods, collections):
    """Check the complete period before any client/date filter removes recipients."""
    by_period, claims = defaultdict(list), defaultdict(list)
    for row in applications:
        period = row.get('period')
        if period not in periods:
            continue
        row.setdefault('deposit_cash_usd', row.get('paid_usd'))
        by_period[period].append(row)
        if row.get('collection_row_id'):
            collection = collections[row['collection_row_id']]
            key = (period, 'Cobranza', collection.get('row_key'))
        else:
            key = (period, 'Aplicacion historica', row.get('source_rows'))
        claims[key].append(row)

    funded_by_period, to_verify, legacy = {}, [], set()
    for name, rows in by_period.items():
        period = periods[name]
        funded = [row for row in rows if row.get('deposit_cash_usd') is not None and money(row['deposit_cash_usd']) > 0]
        if not funded or 'cash_breakdown_known' not in period:
            continue
        funded_by_period[name] = funded
        if not period.get('cash_breakdown_known') and not period.get('cash_breakdown_note'):
            # Older periods lack the cached split. The executed deposit
            # journals can still prove each collection/application's cash.
            legacy.add(name)
            to_verify.append(period)
        elif len(funded) > 1 and (period.get('negative_complementary_usd') or period.get('conversion_coverage_usd')):
            to_verify.append(period)

    offsets, journal_paid, uncertain = journal_evidence(to_verify, claims, include_company_difference=True)
    expected = defaultdict(dict)
    for key, rows in claims.items():
        if key[0] in legacy and all(row.get('deposit_cash_usd') is not None for row in rows):
            amount = sum_money(row['deposit_cash_usd'] for row in rows)
            if amount:
                expected[key[0]][key] = amount

    for name, funded in funded_by_period.items():
        period = periods[name]
        covered = sum_money(row['deposit_cash_usd'] for row in funded)
        portions = offsets[name]
        if name in legacy:
            known = name not in uncertain and expected[name] == journal_paid[name]
            offset = sum_money(amount for amount, _ in portions)
            cash = covered + offset
        else:
            cash = money(period.get('deposit_coverage_usd'))
            offset = money(period.get('negative_complementary_usd')) - money(period.get('conversion_coverage_usd'))
            known = period.get('cash_breakdown_known') and period.get('deposit_coverage_usd') is not None
        if not known or cash < 0 or offset > 0 or covered + offset != cash:
            for row in funded:
                _show(row, None, period.get('cash_breakdown_note') or 'El efectivo asignado no se pudo comprobar con el total del período.')
            continue
        if not offset:
            continue
        if name in uncertain or sum_money(amount for amount, _ in portions) != offset:
            portions = [(offset, {id(row): row for row in funded})]
        # Check each group against its own gross coverage. A mismatch leaves
        # only the verified period total, with no individual attribution.
        individual = {id(row): money(row['deposit_cash_usd']) for row in funded}
        for amount, recipients in portions:
            if len(recipients) == 1:
                key = next(iter(recipients))
                individual[key] = individual.get(key, 0) + amount
        if any(value < 0 for value in individual.values()) or any(
                sum_money(individual.get(key) for key in recipients) < -amount for amount, recipients in portions if len(recipients) > 1):
            portions = [(offset, {id(row): row for row in funded})]
        for index, (amount, recipients) in enumerate(portions):
            for row in recipients.values():
                if len(recipients) == 1:
                    row['_bank_basis'] = money(row.get('_bank_basis', row['deposit_cash_usd'])) + amount
                    _show(row, None if row.get('_shared_bank_cash') else row['_bank_basis'],
                          f'Cobertura adicional al efectivo US$ {-amount:.2f}.')
                else:
                    row.setdefault('_bank_basis', row['deposit_cash_usd'])
                    row.setdefault('_shared_bank_cash', []).append((name, index, len(recipients), amount))
                    _show(row, None, f'Cobertura compartida de US$ {-amount:.2f}. El efectivo por aplicación no se puede individualizar.')


def selected_cash(rows):
    """A shared bank total is exact only when all its member rows are selected."""
    values, groups = [], defaultdict(list)
    for row in rows:
        values.append({'remitted_usd': row.get('_bank_basis', row.get('remitted_usd'))})
        for group in row.get('_shared_bank_cash', ()):
            groups[group].append(values[-1])
    for (_, _, count, amount), members in groups.items():
        if count == len(members):
            values.append({'remitted_usd': amount})
        else:
            for member in members:
                member['remitted_usd'] = None
    return values
