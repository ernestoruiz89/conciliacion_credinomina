"""One identified client row across periods, with independent unassigned amounts."""
from collections import defaultdict

from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.rounding import money, money_float


ASSIGNED = ('application_usd', 'deposit_usd', 'deposit_difference_usd', 'credit_used_usd',
            'cash_adjustment_usd', 'conversion_usd', 'open_usd')
UNIDENTIFIED = {'', 'sin identificar', 'sin cliente individual'}


def _text(values, separator=' · '):
    return separator.join(dict.fromkeys(text for value in values if (text := clean_text(value))))


def _total(values, *, magnitude=False):
    values = list(values)
    if any(value is None for value in values):
        return None
    return money_float(sum((abs(money(value)) if magnitude else money(value) for value in values), money(0)))


def consolidate(details, *, client_by_detail=None):
    """Unknown identities stay separate; a person's surplus never settles a claim."""
    by_loan, by_name, by_national_id = defaultdict(set), defaultdict(set), defaultdict(set)
    national_ids_by_number = defaultdict(set)
    for row in details:
        company = row.get('employer') or ''
        number = canonical_identifier(row.get('client_number'))
        if not number or row.get('_company_conversion'):
            continue
        loan = canonical_credit_number(row.get('loan_number'))
        name = clean_text(row.get('client_name')).casefold()
        national_id = canonical_identifier(row.get('national_id'))
        if national_id:
            by_national_id[national_id].add(number)
            national_ids_by_number[number].add(national_id)
        if loan:
            by_loan[company, loan].add(number)
        if name not in UNIDENTIFIED:
            by_name[company, name].add(number)
    groups = defaultdict(list)
    for index, row in enumerate(details):
        company = row.get('employer') or ''
        number = canonical_identifier(row.get('client_number'))
        name = clean_text(row.get('client_name')).casefold()
        loan = canonical_credit_number(row.get('loan_number'))
        national_id = canonical_identifier(row.get('national_id'))
        if row.get('_company_conversion'):
            identity = ('company_difference',)
        elif number:
            identity = ('client', number)
        else:
            candidates = []
            if loan and by_loan[company, loan]:
                candidates.append(by_loan[company, loan])
            if name not in UNIDENTIFIED and by_name[company, name]:
                candidates.append(by_name[company, name])
            if national_id and by_national_id[national_id]:
                candidates.append(by_national_id[national_id])
            matches = set.intersection(*candidates) if candidates else set()
            if national_id:
                matches = {n for n in matches if not national_ids_by_number[n] or national_ids_by_number[n] == {national_id}}
            if len(matches) == 1:
                identity = ('client', next(iter(matches)))
            elif national_id and not candidates:
                identity = ('national_id', national_id)
            elif not loan and name in UNIDENTIFIED:
                identity = ('unattributed',)
            else:
                # Equal labels alone must not merge two unidentified people.
                identity = ('unidentified', index)
        # CN Client numbers are globally unique. A verified number may span
        # historical employers; keep their names without repeating the person.
        groups['' if identity[0] == 'client' else company, identity].append(row)

    output = []
    for (company, identity), children in groups.items():
        assigned = [row for row in children if not row.get('_pending_receipt')]
        deposits = [row for row in children if row.get('_pending_receipt')
                    and row.get('source_doctype') == 'CN Remittance Allocation']
        complements = [row for row in children if row.get('_pending_receipt')
                       and row.get('source_doctype') == 'CN Complementary Item']
        collection = [row for row in assigned if row.get('collection_control') != 'No aplica']
        row = dict(employer=_text(child.get('employer') for child in children) or None,
            usd_currency='USD', _client_consolidated=True,
            client_number=next((child['client_number'] for child in children if child.get('client_number')), ''),
            client_name=next((child['client_name'] for child in children
                              if clean_text(child.get('client_name')).casefold() not in UNIDENTIFIED), 'Sin cliente individual'),
            name=_text(child.get('name') for child in children),
            payroll_month=_text(sorted({str(child['payroll_month'])[:7] for child in children if child.get('payroll_month')})),
            collection_cycle=_text(child.get('collection_cycle') for child in children),
            closure_state=_text(child.get('closure_state') for child in children),
            collection_usd=_total(child.get('collection_usd') for child in collection) if collection else None,
            collection_difference_usd=_total(child.get('collection_difference_usd') for child in collection) if collection else None,
            deposit_unallocated_usd=_total(child.get('deposit_usd') for child in deposits),
            complementary_unallocated_usd=_total((child.get('cash_adjustment_usd') for child in complements), magnitude=True),
            **{field: _total(child.get(field) for child in assigned) for field in ASSIGNED})
        loans = {}
        for child in children:
            loan = canonical_credit_number(child.get('loan_number'))
            if loan:
                loans.setdefault(loan, child.get('loan_number_display') or child['loan_number'])
        row['loan_number'] = _text(loans.values())
        statuses = _text(child.get('portfolio_credit_status') for child in children)
        if len({child.get('portfolio_credit_status') for child in children if child.get('portfolio_credit_status')}) > 1:
            statuses = _text(f"{child.get('loan_number') or 'Sin crédito'} · {str(child.get('payroll_month') or '')[:7] or 'Sin mes'}: {child['portfolio_credit_status']}"
                for child in children if child.get('portfolio_credit_status'))
        row['portfolio_credit_status'] = statuses
        for field in ('comments', 'first_exception_comment', 'difference_reason', 'observations'):
            row[field] = _text((child.get(field) for child in children), '\n')
        if len({child.get('employer') for child in children}) > 1:
            provenance = _text((f"Empresa {child.get('employer')}; período {child.get('name') or 'sin asignar'}; crédito {child.get('loan_number') or 'sin identificar'}."
                for child in children), '\n')
            row['observations'] = _text([row['observations'], provenance], '\n')
        for child in complements:
            value = child.get('cash_adjustment_usd')
            note = (f"{child.get('source_document')}: remanente con signo US$ {money(value):+.2f}."
                    if value is not None else f"{child.get('source_document')}: remanente sin determinar.")
            row['observations'] = _text([row['observations'], note], '\n')
        if identity[0] == 'unidentified':
            row['observations'] = _text([row['observations'], 'Cliente sin identificador único; no se fusionó con otros registros.'], '\n')
        states = {child.get('collection_control') for child in collection}
        row['collection_control'] = ('No aplica' if not collection else 'Revisar' if row['collection_usd'] is None
            else 'Con diferencias' if states - {'Conforme'} else 'Conforme')
        unknown = any(row[field] is None for field in (*ASSIGNED, 'deposit_unallocated_usd', 'complementary_unallocated_usd'))
        has_pending = bool(deposits or complements)
        row['has_difference'] = has_pending or any(child.get('has_difference') for child in children)
        coverage_states = {child.get('cash_control') for child in assigned}
        row['cash_control'] = ('Revisar: importes sin determinar' if unknown
            else 'Revisar: cobertura y asignación pendientes' if row['open_usd'] and has_pending
            else 'Revisar: pendiente de aplicar' if has_pending
            else 'Pendiente' if row['open_usd'] else 'Con excedente' if 'Con excedente' in coverage_states
            else 'Sin aplicación' if coverage_states == {'Sin aplicación'} else 'Cubierta')
        if client_by_detail is not None:
            client_by_detail.update((id(child), row) for child in assigned)
        output.append(row)
    return sorted(output, key=lambda row: (row.get('employer') or '', row['client_name'].casefold(), row['client_number']))
