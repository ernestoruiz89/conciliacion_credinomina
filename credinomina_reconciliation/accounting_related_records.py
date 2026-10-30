"""Pending applications, deposits, and complementary items for an identified client."""

from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.accounting_client_summary import _client_group_key
from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.complementary_balances import (
    FIELDS as COMPLEMENTARY_FIELDS, load_balances, reconciliation_state,
)
from credinomina_reconciliation.deposit_presentation import reconciliation_status
from credinomina_reconciliation.parsers import canonical_identifier
from credinomina_reconciliation.report_records import PAGE_SIZE, records
from credinomina_reconciliation.rounding import money, money_float


def _identity(document, source_row_name):
    applications = [(index, row) for index, row in enumerate(document.rows or [])
                    if row.event_type == 'Aplicacion' and row.effective and row.match_status != 'Ignorado']
    selected = next(((index, row) for index, row in applications if row.name == source_row_name), None)
    if selected is None:
        frappe.throw(_('La fila no es una aplicación efectiva de esta importación.'))
    key = _client_group_key(selected[1], selected[0])
    peers = [row for index, row in applications if _client_group_key(row, index) == key]
    return _identity_values(selected[1], peers)


def _identity_values(selected, peers):
    names = {str(row.client_name).strip() for row in peers if row.client_name and row.client_name != 'Sin identificar'}
    return {
        'client_name': selected.client_name or _('Sin identificar'),
        'client_number': selected.client_number or '',
        'numbers': {canonical_identifier(row.client_number) for row in peers if row.client_number},
        'number_values': {str(row.client_number).strip() for row in peers if row.client_number},
        'clients': {str(row.client) for row in peers if row.client},
        'national_ids': {canonical_identifier(row.national_id) for row in peers if row.national_id},
        'national_values': {str(row.national_id).strip() for row in peers if row.national_id},
        'names': {name_key(value) for value in names},
        'name_values': names,
    }


def _matches(candidate, identity, *name_fields):
    identifiers = (
        (canonical_identifier(candidate.get('client_number')), identity['numbers']),
        (str(candidate.get('client') or ''), identity['clients']),
        (canonical_identifier(candidate.get('national_id')), identity['national_ids']),
    )
    if any(value and known and value not in known for value, known in identifiers):
        return False
    if any(value and value in known for value, known in identifiers):
        return True
    return any(name_key(candidate.get(field)) in identity['names']
               for field in name_fields if candidate.get(field) and name_key(candidate.get(field)))


def _visible_named_rows(doctype, employer, fields, identity, matches, *, include_number=True):
    found = {}
    queries = [('client_number', identity['number_values'])] if include_number else []
    queries.extend((field, identity['name_values']) for field in matches)
    for field, values in queries:
        for value in values:
            for row in records(doctype, filters={'employer': employer, 'docstatus': ['!=', 2], field: value}, fields=fields):
                found[row.name] = row
    return [row for row in found.values() if _matches(row, identity, *matches)]


def _application_rows(employer, identity):
    """Pending application rows from imports the caller may read."""
    fields = ['name', 'parent', 'source_row', 'event_date', 'reference', 'client',
              'client_name', 'client_number', 'national_id', 'loan_number',
              'amount_usd', 'net_applied_usd', 'application_adjustment_usd',
              'historical_period', 'historical_balance_usd', 'match_status',
              'deposit_match_status', 'collection_period', 'difference_reason',
              'collection_row_id', 'application_allocation_detail']
    conditions = [('client_number', identity['number_values']),
                  ('client', identity['clients']),
                  ('national_id', identity['national_values']),
                  ('client_name', identity['name_values'])]
    candidates = {}
    for field, values in conditions:
        for value in values:
            for row in frappe.get_all('CN Source Row', filters={
                    'parenttype': 'CN Accounting Import', 'parentfield': 'rows',
                    'event_type': 'Aplicacion', 'effective': 1, field: value},
                    fields=fields, limit_page_length=0):
                candidates[row.name] = row
    matched = [row for row in candidates.values() if row.match_status != 'Ignorado'
               and _matches(row, identity, 'client_name')]
    parent_names = sorted({row.parent for row in matched})
    readable = set()
    for offset in range(0, len(parent_names), PAGE_SIZE):
        readable.update(row.name for row in records('CN Accounting Import', filters={
            'name': ['in', parent_names[offset:offset + PAGE_SIZE]],
            'employer': employer, 'docstatus': ['!=', 2]}, fields=['name']))

    settled = {'Depósito conciliado', 'Aplicación compensada', 'Conciliada: depósito + ajuste'}
    result = []
    for row in matched:
        if row.parent not in readable or row.deposit_match_status in settled:
            continue
        net = money(row.net_applied_usd if row.net_applied_usd is not None else
                    money(row.amount_usd) - money(row.application_adjustment_usd))
        historical = bool(row.historical_period)
        pending = money(row.historical_balance_usd) if historical else None
        if historical and pending <= 0:
            continue
        if net <= 0:
            continue
        result.append(dict(type='Aplicación', document=row.parent,
            date=str(row.event_date or ''), reference=row.reference or '',
            source_row=row.source_row, row_name=row.name,
            period=row.historical_period or row.collection_period or '',
            difference_reason=row.difference_reason or '',
            client_name=row.client_name or '', loan_number=row.loan_number or '',
            amount_usd=money_float(net),
            pending_usd=money_float(pending) if pending is not None else None,
            status=row.deposit_match_status or 'Pendiente'))
    from credinomina_reconciliation.application_deposit_links import load_links
    from credinomina_reconciliation.application_complementary_links import load_links as load_complementary_links
    sources = [candidates[row['row_name']] for row in result]
    deposits = load_links(sources)
    complementary_items = load_complementary_links(sources)
    for row in result:
        row['deposits'] = deposits.get(row['row_name'], [])
        row['complementary_items'] = complementary_items.get(row['row_name'], [])
    return result


def _deposit_rows(employer, identity):
    candidates = {}
    conditions = [('client_number', identity['number_values']),
                  ('client', identity['clients']),
                  ('national_id', identity['national_values']),
                  ('client_name', identity['name_values'])]
    child_fields = ['name', 'parent', 'source_row', 'client', 'client_number', 'national_id', 'client_name',
                    'loan_number', 'amount_usd', 'pending_usd', 'match_status', 'difference_reason']
    for field, values in conditions:
        for value in values:
            for row in frappe.get_all('CN Remittance Detail', filters={
                    'parenttype': 'CN Remittance Allocation', 'parentfield': 'detail_rows', field: value},
                    fields=child_fields, limit_page_length=0):
                candidates[row.name] = row
    by_parent = defaultdict(list)
    for row in candidates.values():
        if _matches(row, identity, 'client_name'):
            by_parent[row.parent].append(row)

    fields = ['name', 'employer', 'docstatus', 'deposit_date', 'deposit_reference', 'amount_usd',
              'allocated_usd', 'justified_surplus_usd', 'unclassified_usd', 'detail_count',
              'cash_identity_status', 'result', 'detail_status', 'financial_status',
              'source_client_name', 'source_loan_number']
    parent_names = sorted(by_parent)
    parents = {}
    for offset in range(0, len(parent_names), PAGE_SIZE):
        for row in records('CN Remittance Allocation', filters={
                'name': ['in', parent_names[offset:offset + PAGE_SIZE]],
                'employer': employer, 'docstatus': ['!=', 2]}, fields=fields):
            parents[row.name] = row
    # A deposit without a payment detail can still identify the client in its
    # imported accounting origin. Never attribute an entire multi-client deposit.
    for row in _visible_named_rows('CN Remittance Allocation', employer, fields, identity,
                                   ('source_client_name',), include_number=False):
        if row.name not in parents and not row.detail_count and not frappe.db.exists(
                'CN Remittance Detail', {'parent': row.name, 'parenttype': 'CN Remittance Allocation',
                                         'parentfield': 'detail_rows'}):
            parents[row.name] = row

    result = []
    for parent in parents.values():
        if parent.cash_identity_status == 'Origen vinculado':
            continue
        state = reconciliation_status(parent)
        for detail in by_parent.get(parent.name, []):
            pending = money(detail.pending_usd)
            if pending == 0 and detail.match_status in {'Conciliada', 'No deducido'}:
                continue
            result.append(dict(type='Depósito', document=parent.name, date=str(parent.deposit_date or ''),
                reference=parent.deposit_reference or '', source_row=detail.source_row,
                row_name=detail.name, docstatus=parent.docstatus,
                difference_reason=detail.difference_reason or '',
                client_name=detail.client_name or '',
                loan_number=detail.loan_number or '', amount_usd=money_float(detail.amount_usd),
                pending_usd=money_float(pending), status=detail.match_status or state))
        if by_parent.get(parent.name) or parent.detail_count:
            continue
        if state in {'Conciliado', 'Conciliado con saldo a favor', 'No participa'}:
            continue
        pending = money(parent.amount_usd) if parent.docstatus == 0 else money(parent.unclassified_usd)
        result.append(dict(type='Depósito', document=parent.name, date=str(parent.deposit_date or ''),
            row_name='', docstatus=parent.docstatus,
            reference=parent.deposit_reference or '', client_name=parent.source_client_name or '',
            loan_number=parent.source_loan_number or '', amount_usd=money_float(parent.amount_usd),
            pending_usd=money_float(pending) if pending > 0 else None, status=state))
    return result


def _complementary_rows(employer, identity):
    fields = list(dict.fromkeys([*COMPLEMENTARY_FIELDS, 'client_name', 'client_number',
                                 'loan_number', 'source_client_name', 'reference']))
    items = _visible_named_rows('CN Complementary Item', employer, fields, identity,
                                ('client_name', 'source_client_name'))
    if not items:
        return []
    balances = load_balances(items)
    result = []
    for item in items:
        balance = balances[item.name]
        pending = money(balance['pending_usd'])
        state = reconciliation_state(balance)
        if pending <= 0 and state != 'Revisar distribución':
            continue
        result.append(dict(type='Partida complementaria', document=item.name,
            docstatus=item.docstatus, period=item.period or '', category=item.category or '',
            direction=('Crédito' if money(item.source_credit) > 0 and not money(item.source_debit)
                       else 'Débito' if money(item.source_debit) > 0 and not money(item.source_credit) else ''),
            date=str(item.posting_date or ''), reference=item.get('reference') or '',
            client_name=item.client_name or item.source_client_name or '',
            loan_number=item.loan_number or '', amount_usd=balance['original_usd'],
            pending_usd=balance['pending_usd'], status=state))
    return result


@frappe.whitelist()
def get_related_pending(import_name, source_row_name):
    document = frappe.get_doc('CN Accounting Import', import_name)
    document.check_permission('read')
    identity = _identity(document, source_row_name)
    return dict(_related_pending(document.employer, identity), employer=document.employer,
                context={'import_name': import_name, 'source_row_name': source_row_name})


@frappe.whitelist()
def get_related_pending_for_remittance(remittance_name, detail_row_name):
    document = frappe.get_doc('CN Remittance Allocation', remittance_name)
    document.check_permission('read')
    detail = next((row for row in document.detail_rows or [] if row.name == detail_row_name), None)
    if detail is None:
        frappe.throw(_('La fila no pertenece a este depósito.'))
    identity = _identity_values(detail, [detail])
    if not any((identity['numbers'], identity['clients'], identity['national_ids'], identity['names'])):
        frappe.throw(_('Identifique al cliente de esta fila antes de consultar sus pendientes.'))
    employer = detail.employer or document.employer
    return dict(_related_pending(employer, identity), employer=employer,
                context={'remittance_name': remittance_name, 'detail_row_name': detail_row_name})


def _related_pending(employer, identity):
    applications = _application_rows(employer, identity)
    rows = [*_deposit_rows(employer, identity),
            *_complementary_rows(employer, identity)]
    applications.sort(key=lambda row: (row['date'], row['document'], row['loan_number']), reverse=True)
    rows.sort(key=lambda row: (row['date'], row['document'], row['loan_number']), reverse=True)
    return {'client_name': identity['client_name'], 'client_number': identity['client_number'],
            'applications': applications, 'rows': rows}
