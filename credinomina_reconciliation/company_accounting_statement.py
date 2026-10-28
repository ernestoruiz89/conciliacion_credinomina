"""Independent accounting turnover and management columns for the company statement.

Accounting amounts always come from the original ledger evidence. Management is
a dated projection of its uses; it is never added to the accounting balance.
"""
import frappe
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.accounting_control import build_rows, MONEY_FIELDS
from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
from credinomina_reconciliation.application_context import import_company_scope
from credinomina_reconciliation.company_statement import FIELDS as MANAGEMENT_FIELDS
from credinomina_reconciliation.deposit_accounting_sources import attach_sources
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float

CORE = 'Contabilidad del core'
MANAGEMENT = 'Gestión'
CORE_METRICS = (
    ('core_debit_usd', 'Débitos core US$'),
    ('core_credit_usd', 'Créditos core US$'),
    ('core_balance_usd', 'Saldo contable core US$'),
    ('core_debit_nio', 'Débitos core C$'),
    ('core_credit_nio', 'Créditos core C$'),
    ('core_balance_nio', 'Saldo contable core C$'),
)
MANAGEMENT_METRICS = (
    ('company_credit_usd', 'Saldo a favor empresa (−) US$'),
    ('client_credit_usd', 'Saldo a favor clientes (−) US$'),
    ('company_receivable_usd', 'CxC empresa por gestionar US$'),
    ('employee_receivable_usd', 'CxC empleados por gestionar US$'),
    ('application_pending_usd', 'Aplicaciones sin depósito US$'),
    ('core_pending_usd', 'Core por conciliar (+débito / −crédito) US$'),
    ('deposit_pending_usd', 'Depósitos sin asignar (−) US$'),
)
POSITION_METRICS = (
    ('management_pending_usd', 'Pendientes US$'),
    ('other_credit_usd', 'Otros créditos pendientes (−) US$'),
    ('management_net_usd', 'Saldo US$'),
)
POSITION_FIELDS = tuple(field for field, _ in POSITION_METRICS)
DOCUMENTED_CREDITS = {'company_credit_usd', 'client_credit_usd'}
CORE_FIELDS = tuple(field for field, _ in CORE_METRICS)
FIELDS = (*CORE_FIELDS, *MANAGEMENT_FIELDS)


def selected_scope(filters):
    component = filters.get('component')
    if component in CORE_FIELDS:
        return CORE
    if component in (*MANAGEMENT_FIELDS, *POSITION_FIELDS) or str(filters.get('accounting_queue') or '0') in {'1', 'True'}:
        return MANAGEMENT
    return filters.get('statement_scope') or 'Todos'


def validate_dates(filters):
    start, end, cutoff = (filters.get(field) for field in ('from_date', 'to_date', 'cutoff_date'))
    if start and end and getdate(start) > getdate(end):
        frappe.throw('La fecha Desde no puede ser posterior a Hasta.')
    if cutoff:
        if getdate(cutoff) > getdate(nowdate()):
            frappe.throw('La fecha de corte no puede ser futura.')
        if any(value and getdate(value) > getdate(cutoff) for value in (start, end)):
            frappe.throw('Desde/Hasta no pueden ser posteriores a la fecha de corte.')


def load_core_detail(filters):
    """Read original lines, including settled/ignored evidence and child origins.

    Do not restrict parents by their bank date or primary ledger line: a grouped
    receipt can hold postings on other dates or accounts. Mirror resolution happens
    before filtering. Internal cancellation is not a reversal of the core posting.
    """
    for doctype in ('CN Accounting Import', 'CN Complementary Item', 'CN Remittance Allocation'):
        if not frappe.has_permission(doctype, 'read'):
            frappe.throw('Necesita permiso de lectura de importaciones, partidas y depósitos.', frappe.PermissionError)
    validate_dates(filters)
    company = {'employer': filters['employer']} if filters.get('employer') else {}
    imports = {row['name']: row for row in records('CN Accounting Import', **import_company_scope(filters.get('employer')), fields=[
        'name', 'employer', 'currency', 'manual_fx_rate', 'creation', 'status',
        'source_file', 'file_hash', 'bulk_source_file', 'bulk_source_hash'])}
    sources = list(child_records('CN Source Row', imports, 'CN Accounting Import', 'rows', fields=[
        'name', 'parent', 'idx', 'event_date', 'source_account', 'source_currency', 'source_debit',
        'source_credit', 'source_description', 'description', 'source_fx_rate', 'manual_fx_rate', 'fx_rate',
        'accounting_source_key', 'source_key', 'complementary_item', 'remittance_allocation', 'source_row',
        'client_name', 'client_number', 'loan_number', 'portfolio_client_name', 'portfolio_employer',
        'voucher', 'tmov', 'tdoc', 'accounting_reference', 'event_type', 'accounting_classification', 'difference_reason']))
    # An imported application without original ledger evidence is operational
    # information, not a fabricated debit in the core.
    sources = [row for row in sources if row.get('accounting_source_key') or row.get('source_account')
               or money(row.get('source_debit')) or money(row.get('source_credit'))]
    for source in sources:
        source['portfolio_employer'] = imports[source['parent']].get('employer') or source.get('portfolio_employer')
    items = list(records('CN Complementary Item', filters={**company, 'accounting_source_key': ['is', 'set']},
        fields=list(dict.fromkeys(['name', 'employer', 'docstatus', 'posting_date', 'category',
            'client_name', 'client_number', 'loan_number', 'description', 'voucher', 'accounting_status',
            'credit_management_status', 'reason_type', 'subcategory', 'review_status', 'origin_deposit',
            *[field for field in EVIDENCE_FIELDS if field != 'source_loan_number']]))))
    deposits = list(records('CN Remittance Allocation', filters=company, fields=list(dict.fromkeys([
        'name', 'employer', 'docstatus', 'bank_account', 'deposit_date', 'cash_identity_status',
        'consolidated_deposit', *EVIDENCE_FIELDS]))))
    attach_sources(deposits)
    visible = {row['name'] for row in deposits}
    for deposit in deposits:
        if deposit.get('cash_identity_status') == 'Origen vinculado' and deposit.get('consolidated_deposit') not in visible:
            # Preserve readable original evidence if the canonical cash document
            # is outside the caller's permissions. No hidden amounts are read.
            deposit['cash_identity_status'] = ''
    ledger, _duplicates = build_rows(sources, imports, items, deposits=deposits, include_financial=False)
    return core_detail(ledger, filters)


def core_detail(ledger, filters=None):
    filters = filters or {}
    result = []
    for line in ledger:
        when = line.get('event_date')
        if not when:
            frappe.throw('Hay una línea del core sin fecha contable; revise el origen antes de calcular el saldo.')
        if any(filters.get(field) and getdate(when) > getdate(filters[field]) for field in ('to_date', 'cutoff_date')):
            continue
        if filters.get('from_date') and getdate(when) < getdate(filters['from_date']):
            continue
        if any(filters.get(field) and line.get(field) != filters[field]
               for field in ('employer', 'source_account', 'source_currency')):
            continue
        document, doctype = next((line[key], kind) for key, kind in (
            ('accounting_import', 'CN Accounting Import'), ('complementary_item', 'CN Complementary Item'),
            ('remittance_allocation', 'CN Remittance Allocation')) if line.get(key))
        values = {field: 0.0 for field in FIELDS}
        for old, new in zip(MONEY_FIELDS, ('core_debit_nio', 'core_credit_nio', 'core_balance_nio',
                                         'core_debit_usd', 'core_credit_usd', 'core_balance_usd')):
            # USD originals have no original C$ amount, not an unknown conversion.
            values[new] = 0.0 if old.endswith('_nio') and line.get('source_currency') == 'USD' else line.get(old)
        result.append(dict(line, **values, statement_scope=CORE, source_document=document,
            source_doctype=doctype, position_type=line.get('movement_type'), status='Origen contable',
            origin_type='Core', accounting_pending=False, _component='core_balance_usd',
            reference=line.get('voucher'), source_rows=str(line.get('source_row') or ''),
            observation='; '.join(filter(None, [line.get('warning'), line.get('description')])),
            accounting_status=line.get('accounting_status') or 'Importada del core', management_amount_usd=0.0))
    return result


def management_detail(rows):
    """Preserve the signed pending position: credits are already in the cash origin."""
    result = []
    for source in rows:
        row = dict(source, statement_scope=MANAGEMENT, nio_currency='NIO',
                   **{field: 0.0 for field in CORE_FIELDS})
        amount = row.pop('balance_usd', None)
        row['management_amount_usd'] = None if amount is None else money_float(amount)
        result.append(row)
    return result


def matches(row, filters):
    component = filters.get('component')
    amount = row.get('management_amount_usd')
    if component in POSITION_FIELDS:
        if row.get('statement_scope') != MANAGEMENT:
            return False
        # An unknown amount must remain available for review, not silently vanish.
        if component == 'management_pending_usd' and (row.get('_component') in DOCUMENTED_CREDITS | {'deposit_pending_usd'}
                or (amount is not None and money(amount) <= 0)):
            return False
        if component == 'other_credit_usd' and (row.get('_component') in DOCUMENTED_CREDITS
                or (amount is None and row.get('_component') not in {'core_pending_usd', 'deposit_pending_usd'})
                or (amount is not None and money(amount) >= 0)):
            return False
    for field in ('status', 'origin_type', 'category', 'subcategory', 'reason_type',
                  'accounting_status', 'management_status', 'treatment_status'):
        if filters.get(field) and row.get(field) != filters[field]:
            return False
    query = str(filters.get('search') or '').strip().casefold()
    return not query or query in ' '.join(str(row.get(field) or '') for field in (
        'client_name', 'client_number', 'loan_number', 'source_document', 'reference', 'observation',
        'source_file', 'source_rows', 'source_row_id', 'evidence_key')).casefold()


def summarize(detail):
    groups = {}
    for row in detail:
        key = row.get('employer') or ''
        group = groups.setdefault(key, dict(employer=key or None, usd_currency='USD', nio_currency='NIO',
            core_movement_count=0, pending_count=0, _accounting_documents=set(),
            management_pending_usd=money(0), other_credit_usd=money(0), pending_core_difference_usd=money(0),
            **{field: money(0) for field in FIELDS}))
        if row['statement_scope'] == CORE:
            group['core_movement_count'] += 1
        else:
            group['pending_count'] += int(bool(row.get('_component', True)))
            if 'pending_core_difference_usd' in row:
                value = row['pending_core_difference_usd']
                group['pending_core_difference_usd'] = (None if value is None or group['pending_core_difference_usd'] is None
                    else group['pending_core_difference_usd'] + money(value))
            if row.get('accounting_pending'):
                group['_accounting_documents'].add(row['source_document'])
            # Split individual positions before summing: a core debit and credit
            # must not hide each other inside the headline pending amount.
            for field in MANAGEMENT_FIELDS:
                value = row.get(field, 0)
                if value is None:
                    if field not in DOCUMENTED_CREDITS | {'deposit_pending_usd'}:
                        group['management_pending_usd'] = None
                    if field in {'core_pending_usd', 'deposit_pending_usd'}:
                        group['other_credit_usd'] = None
                elif money(value) > 0 and group['management_pending_usd'] is not None:
                    group['management_pending_usd'] += money(value)
                elif field not in DOCUMENTED_CREDITS and money(value) < 0 and group['other_credit_usd'] is not None:
                    group['other_credit_usd'] += money(value)
        for field in FIELDS:
            value = row.get(field, 0)
            group[field] = None if group[field] is None or value is None else group[field] + money(value)
    result = []
    for key in sorted(groups):
        group = groups[key]
        group['accounting_pending_count'] = len(group.pop('_accounting_documents'))
        for field in (*FIELDS, 'management_pending_usd', 'other_credit_usd', 'pending_core_difference_usd'):
            if group[field] is not None:
                group[field] = money_float(group[field])
        group['management_net_usd'] = (None if any(group[field] is None for field in MANAGEMENT_FIELDS) else money_float(
            sum((money(group[field]) for field in MANAGEMENT_FIELDS), money(0))))
        group['reconciliation_difference_usd'] = (None if group['management_net_usd'] is None or group['core_balance_usd'] is None
            else money_float(money(group['management_net_usd']) - money(group['core_balance_usd'])))
        result.append(group)
    return result
