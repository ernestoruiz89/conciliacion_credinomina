"""Current reconciliation position: independent obligations, never a loan ledger."""
import frappe
from frappe.utils import cint, getdate, nowdate

from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.application_aging import application_balances, balance_status
from credinomina_reconciliation.complementary_balances import FIELDS, load_balances, cached_balance_reads, reconciliation_state
from credinomina_reconciliation.operational_statement_cash import apply_cash_breakdown, selected_cash
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float, sum_money


FINANCIAL_VIEW = 'Aplicaciones, depósitos y partidas'
FINANCIAL_TYPES = frozenset({'Aplicación', 'Depósito con saldo pendiente', 'Partida complementaria'})


def normalize_position_type(value):
    # Preserve saved report filters and links using the former caption.
    return 'Depósito con saldo pendiente' if value == 'Depósito sin asignar' else value


def selected_position_types(value):
    view = normalize_position_type(value)
    return FINANCIAL_TYPES if view == FINANCIAL_VIEW else frozenset({view}) if view else None


def _within_dates(when, filters):
    return (not filters.get('from_month') or (when and getdate(when) >= getdate(filters['from_month']))) and (
        not filters.get('to_month') or (when and getdate(when) <= getdate(filters['to_month'])))


def _matches(row, filters):
    for field, normalize in (('client_number', canonical_identifier), ('national_id', canonical_identifier),
                             ('loan_number', canonical_credit_number)):
        if filters.get(field) and normalize(row.get(field)) != normalize(filters[field]):
            return False
    for field in ('employer', 'collection_cycle', 'operational_status',
                  'category', 'subcategory', 'reason_type', 'accounting_status', 'management_status', 'treatment_status'):
        if filters.get(field) and str(row.get(field) or '') != str(filters[field]):
            return False
    if not _within_dates(row.get('event_date'), filters):
        return False
    if cint(filters.get('only_open')):
        # Financial settlement is separate from external management or journal
        # registration. This filter hides settled entries even with those tasks.
        state = str(row.get('operational_status') or '')
        return bool(row.get('is_open')) and not state.startswith(('Conforme', 'Conciliado', 'Conciliada'))
    return True


def _application_in_range(source, filters):
    parts = source.get('_statement_parts')
    if not parts or not (filters.get('from_month') or filters.get('to_month')):
        return source
    selected = [part for part in parts if _within_dates(part.get('application_date'), filters)]
    if not selected:
        return None
    if len(selected) == len(parts):
        return source
    applied = sum_money(part['applied_usd'] for part in selected)
    imports = {part['source_import'] for part in selected}
    row = dict(source, application_date=min(part['application_date'] for part in selected),
               applied_usd=money_float(applied), source_import=imports.pop() if len(imports) == 1 else None,
               source_rows=', '.join(sorted({part['source_rows'] for part in selected})),
               has_application_adjustment=any(part['has_application_adjustment'] for part in selected),
               difference_reason=' · '.join(sorted({part['difference_reason'] for part in selected if part.get('difference_reason')})),
               difference_notes='\n'.join(sorted({part['difference_notes'] for part in selected if part.get('difference_notes')})))
    row.pop('_shared_bank_cash', None)
    row.pop('_bank_basis', None)
    if not money(source.get('adjustment_usd')) and source.get('paid_usd') in (0, source['applied_usd']):
        covered = applied if source['paid_usd'] else money(0)
        cash = source.get('deposit_cash_usd', source['paid_usd'])
        row.update(paid_usd=money_float(covered), amount_usd=money_float(applied - covered),
                   deposit_cash_usd=money_float(covered) if cash == source['paid_usd'] else None)
    else:
        issue = 'El pago de esta cuota cubre aplicaciones de distintas fechas y falta su reparto individual.'
        row.update(paid_usd=None, deposit_cash_usd=None, amount_usd=None, distribution_issue=issue, observation=issue)
    return row


def build_position(collections, applications, items, balances, clients, filters, *, adjustment_receivables=(),
                   unallocated_deposits=(), period_cycles=None):
    period_cycles = period_cycles or {}
    output = []
    for source in collections:
        row = dict(source, position_type='Cobranza', event_date=source.get('payroll_month'),
            source_doctype='CN Reconciliation Period', source_document=source.get('period'),
            is_open=not source.get('operational_status', '').startswith(('Conforme', 'Conciliado')))
        # Applications and their cash are shown only in application rows below.
        for field in ('applied_usd', 'applied_nio', 'remitted_usd', 'remitted_nio',
                      'complementary_usd', 'fx_variance_usd', 'rounding_adjustment_usd'):
            row[field] = None
        output.append(row)
    for source in applications:
        source = _application_in_range(source, filters)
        if source is None:
            continue
        state = balance_status(source)
        row = dict(source, position_type='Aplicación', event_date=source.get('application_date') or source.get('payroll_month'),
            client_number=source.get('client') or source.get('client_number'),
            source_doctype='CN Accounting Import' if source.get('source_import') else 'CN Reconciliation Period',
            source_document=source.get('source_import') or source.get('period'),
            remitted_usd=source.get('deposit_cash_usd', source.get('paid_usd')), applied_pending_usd=source.get('amount_usd'),
            assigned_usd=application_assigned(source),
            rounding_adjustment_usd=source.get('adjustment_usd'),
            operational_status=state, is_open=state != 'Conciliado')
        row.pop('_statement_parts', None)
        output.append(row)
    by_number = {}
    for client in clients:
        key = (client.get('employer'), canonical_identifier(client.get('client_number')))
        if key[1]:
            by_number.setdefault(key, []).append(client)
    by_name = {client['name']: client for client in clients}
    for item in items:
        balance = balances[item['name']]
        candidates = by_number.get((item.get('employer'), canonical_identifier(item.get('client_number'))), [])
        client = by_name.get(item.get('credit_client')) or (candidates[0] if len(candidates) == 1 else {})
        # A company-wide amount is not allocated to individual clients by inference.
        company_wide = item.get('category') == 'Saldo a favor de la empresa'
        if company_wide:
            client = {}
        if item.get('docstatus') == 2:
            continue
        financial_pending = (balance.get('pending_usd') is None or bool(money(balance.get('pending_usd')))
                             or balance['financial_status'] == 'Revisar distribución')
        signed_pending = signed_complementary_pending(item, balance['pending_usd'])
        output.append(dict(
            position_type='Partida complementaria', event_date=item.get('posting_date'),
            source_doctype='CN Complementary Item', source_document=item['name'],
            employer=item.get('employer'), period=item.get('period'), collection_cycle=period_cycles.get(item.get('period')),
            client_number='' if company_wide else client.get('client_number') or item.get('client_number'),
            client_name='Sin cliente individual' if company_wide else client.get('client_name') or item.get('client_name') or item.get('source_client_name'),
            national_id=client.get('national_id'), loan_number='' if company_wide else item.get('loan_number'),
            category=item.get('category'), subcategory=item.get('subcategory'), reason_type=item.get('reason_type'),
            difference_reason=item.get('difference_reason'),
            difference_notes=item.get('difference_notes'),
            treatment_status=balance['financial_status'], complementary_usd=balance['original_usd'],
            complementary_used_usd=balance['used_usd'], complementary_pending_usd=balance['pending_usd'],
            _signed_complementary_pending_usd=signed_pending,
            complementary_pending_net_usd=(money_float(-money(signed_pending))
                if signed_pending is not None and balance['financial_status'] != 'Revisar distribución' else None),
            credit_pending_usd=balance['management_pending_usd'], credit_resolved_usd=item.get('credit_resolved_usd'),
            accounting_status=balance['accounting_status'], operational_status=reconciliation_state(balance),
            management_status=balance.get('management_status'),
            observation='; '.join(filter(None, [balance.get('amount_issue'), item.get('description')])),
            is_open=financial_pending or bool(money(balance.get('management_pending_usd'))),
            usd_currency='USD', nio_currency='NIO'))
    for item in adjustment_receivables:
        output.append(dict(position_type='CxC por ajuste', event_date=item.get('posting_date'),
            source_doctype='CN Complementary Item', source_document=item['name'], employer=item.get('employer'),
            period=item.get('period'), collection_cycle=period_cycles.get(item.get('period')), client_name=item.get('client_name'),
            client_number=item.get('client_number') or item.get('client'), national_id=item.get('national_id'),
            loan_number=item.get('loan_number'), category=item.get('category'), subcategory=item.get('subcategory'),
            reason_type=item.get('reason_type'), difference_reason=item.get('difference_reason'), difference_notes=item.get('difference_notes'),
            company_receivable_usd=0 if item.get('subcategory_effect') == 'CxC al Empleado' else item['receivable_usd'],
            employee_receivable_usd=item['receivable_usd'] if item.get('subcategory_effect') == 'CxC al Empleado' else 0,
            treatment_status=item.get('subcategory_effect'), operational_status=item['receivable_status'],
            accounting_status=item.get('accounting_status'), management_status=item['receivable_status'],
            observed_usd=item.get('observed_usd'),
            observation='; '.join(filter(None, [item.get('observation'), item.get('description')])),
            is_open=item['receivable_usd'] is None or bool(money(item['receivable_usd'])), usd_currency='USD'))
    output.extend(unallocated_deposits)
    kinds = selected_position_types(filters.get('position_type'))
    return sorted([row for row in output if (kinds is None or row['position_type'] in kinds) and _matches(row, filters)],
                  key=lambda row: (str(row.get('event_date') or ''), row['position_type'],
                                   row.get('client_name') or '', row.get('source_document') or ''))


@cached_balance_reads
def load_position(collections, filters):
    if filters.get('from_month') and filters.get('to_month') and getdate(filters['from_month']) > getdate(filters['to_month']):
        frappe.throw('La fecha Desde no puede ser posterior a Hasta.')
    kinds = selected_position_types(filters.get('position_type'))
    applications = []
    scope = {'employer': filters['employer']} if filters.get('employer') else {}
    periods = {}
    if kinds is None or 'Aplicación' in kinds:
        inputs = load_application_context(employer=filters.get('employer'), include_cash_breakdown=True)
        applications = application_balances(*inputs, nowdate(), include_settled=True, statement_detail=True)
        periods = inputs[2]
        apply_cash_breakdown(applications, periods, inputs[3])
    elif filters.get('collection_cycle') and kinds.intersection({'Partida complementaria', 'CxC por ajuste'}):
        periods = {p.name: p for p in records('CN Reconciliation Period', filters=scope,
            fields=['name', 'collection_cycle', 'reconciliation_mode'])}
    fields = list(dict.fromkeys([*FIELDS, 'client_number', 'loan_number', 'credit_client', 'subcategory', 'reason_type',
        'client_name', 'source_client_name', 'credit_resolved_usd', 'difference_notes']))
    items = list(records('CN Complementary Item', fields=fields, filters={**scope, 'docstatus': ['!=', 2]})) if kinds is None or 'Partida complementaria' in kinds else []
    clients = list(records('CN Client', filters=scope, fields=['name', 'client_number', 'client_name', 'national_id', 'employer'])) if items else []
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    from credinomina_reconciliation.unallocated_deposit_position import load as load_unallocated_deposits
    return build_position(collections, applications, items, load_balances(items), clients, filters,
        period_cycles={name: p.get('collection_cycle') or ('Mensual' if p.get('reconciliation_mode') == 'Operativa' else '')
                       for name, p in periods.items()},
        adjustment_receivables=load_receivables(employer=filters.get('employer'), include_settled=not cint(filters.get('only_open')))
                              if kinds is None or 'CxC por ajuste' in kinds else [],
        unallocated_deposits=load_unallocated_deposits(employer=filters.get('employer'), separate_company_difference=True)
            if kinds is None or 'Depósito con saldo pendiente' in kinds else [])


def row_net_balance(row):
    """Signed contribution to the net pending balance, never a running balance."""
    kind = row.get('position_type')
    if kind == 'Aplicación':
        value, sign = row.get('applied_pending_usd'), 1
    elif kind == 'Depósito con saldo pendiente':
        value, sign = row.get('deposit_pending_usd'), -1
    elif kind == 'Partida complementaria':
        if row.get('operational_status') == 'Revisar distribución':
            return None
        # Debits add to debt; credits reduce it, as in the net summary.
        value, sign = row.get('_signed_complementary_pending_usd'), 1
    else:
        return None  # Collection/control rows must not count the same debt twice.
    return money_float(money(value) * sign) if value is not None else None


def summarize_selection(data, filters=None):
    """Totals of the selected rows, only for the selected financial dimensions.

    An unknown amount suppresses that monetary total instead of silently adding
    zero. Complementary balances retain their magnitude for the audit columns;
    the compact column uses credit-positive, debit-negative pending amounts.
    """
    view = normalize_position_type((filters or {}).get('position_type'))
    kinds = selected_position_types(view) if view else {row['position_type'] for row in data}
    figures, amounts, net_rows = [], {}, []

    def count(label, value, color='orange'):
        figures.append(dict(label=label, value=value, datatype='Int', indicator=color))

    def total(rows, field, label=None, color='blue', unknown=None):
        missing = sum(unknown(row) if unknown else row.get(field) is None for row in rows)
        value = None if missing else money_float(sum_money(row.get(field) for row in rows))
        if rows:
            amounts[field] = value
        if label:
            if missing:
                count(label + ' · importes sin determinar', missing, 'red')
            else:
                figures.append(dict(label=label, value=value, datatype='Currency', currency='USD', indicator=color))

    if 'Cobranza' in kinds:
        rows = [row for row in data if row['position_type'] == 'Cobranza']
        total(rows, 'expected_usd', 'Cobranza US$', unknown=lambda row:
              row.get('expected_usd') is None or (not money(row.get('expected_usd')) and bool(money(row.get('expected_nio')))))
        total(rows, 'quality_applied_usd', 'Aplicado para control US$')
        total(rows, 'quality_difference_usd')
        count('Filas con diferencias de control', sum(row.get('is_open', False) for row in rows))
    if 'Aplicación' in kinds:
        rows = [row for row in data if row['position_type'] == 'Aplicación']
        net_rows.extend(rows)
        total(rows, 'applied_usd', 'Aplicado neto US$')
        total(rows, 'assigned_usd', 'Asignado US$', 'green')
        total(selected_cash(rows), 'remitted_usd')
        total(rows, 'applied_pending_usd', 'CxC por aplicaciones US$', 'orange')
    if 'Depósito con saldo pendiente' in kinds:
        deposits = [row for row in data if row['position_type'] == 'Depósito con saldo pendiente']
        # An indeterminate customer detail is informational when its actual
        # bank remainder is already present in this same filtered selection.
        whole = {row['source_document'] for row in deposits if not row.get('source_rows') and row.get('deposit_pending_usd') is not None}
        selected_deposits = [row for row in deposits if row.get('deposit_pending_usd') is not None or row['source_document'] not in whole]
        total(selected_deposits, 'deposit_pending_usd', 'Saldo pendiente de depósitos US$')
        net_rows.extend(selected_deposits)
        total([row for row in deposits if row.get('source_rows')], 'deposit_detail_pending_usd')
    if 'Partida complementaria' in kinds:
        rows = [row for row in data if row['position_type'] == 'Partida complementaria']
        net_rows.extend(rows)
        reviews = [row for row in rows if row.get('operational_status') == 'Revisar distribución'
                   or (row.get('complementary_pending_usd') is not None and money(row['complementary_pending_usd']) < 0)]
        review_ids = {id(row) for row in reviews}
        valid = [row for row in rows if id(row) not in review_ids]
        total(valid, 'complementary_pending_usd')
        total(rows, 'complementary_pending_net_usd', 'Complementaria pendiente US$', 'orange')
        total(rows, 'complementary_usd')
        total(rows, 'complementary_used_usd')
        if reviews:
            # Audit totals cover every visible movement, so an unresolved
            # balance cannot become a numeric total of only the valid subset.
            amounts['complementary_pending_usd'] = None
            count('Partidas con inconsistencias', len(reviews), 'red')
            observed = [dict(row, observed_usd=abs(money(row.get('complementary_pending_usd')))
                             or abs(money(row.get('complementary_usd')))) for row in reviews]
            total(observed, 'observed_usd', 'Importes observados US$', 'red')
        # None means this dimension does not apply to non-credit complementary items.
        credits = [row for row in rows if row.get('category') in {'Saldo a favor del cliente', 'Saldo a favor de la empresa'}]
        if credits or view == 'Partida complementaria':
            total(credits, 'credit_pending_usd', 'Saldos a favor por gestionar US$')
        total(credits, 'credit_resolved_usd')
        count('Partidas pendientes de registro contable', sum(
            row.get('accounting_status') in {'Pendiente de registro', 'Asiento informado'} for row in rows))
    if 'CxC por ajuste' in kinds:
        total([row for row in data if row['position_type'] == 'CxC por ajuste'],
              'company_receivable_usd', 'CxC a la empresa por ajustes US$', 'orange')
        total([row for row in data if row['position_type'] == 'CxC por ajuste'],
              'employee_receivable_usd', 'CxC al empleado por ajustes US$', 'orange')
    total([{'net_balance_usd': row_net_balance(row)} for row in net_rows], 'net_balance_usd')
    return figures, amounts


def summary(data, filters=None):
    return summarize_selection(data, filters)[0]


def application_assigned(source):
    """Confirmed coverage, without counting a reduction of applied net twice.

    paid_usd includes executed deposit/credit coverage. Signed reconciliation
    differences are distinct from application_adjustment_usd, which is already
    deducted from applied_usd. Never derive an allocation from a capped balance.
    """
    if source.get('paid_usd') is None or source.get('adjustment_usd', 0) is None:
        return None
    return money_float(money(source['paid_usd']) - money(source.get('adjustment_usd')))


def signed_complementary_pending(item, pending):
    """Sign only the remaining balance, using the original accounting evidence."""
    if pending is None or money(pending) < 0:
        return None
    if not money(pending):
        return 0.0
    net = money(item.get('source_debit')) - money(item.get('source_credit'))
    if not net:
        return None
    return money_float(money(pending) * (1 if net > 0 else -1))


def net_balance_summary(data, amounts, filters):
    """Selection-only position; never offset documents or infer missing signs."""
    kinds = selected_position_types(filters.get('position_type'))
    if kinds is not None and not FINANCIAL_TYPES <= kinds:
        return []
    items = [row for row in data if row['position_type'] == 'Partida complementaria']
    unknown = sum(row.get('_signed_complementary_pending_usd') is None
                  or row.get('operational_status') == 'Revisar distribución' for row in items)
    figures = []
    if unknown:
        figures.append(dict(label='Complementarias sin saldo o signo contable determinable', value=unknown,
                            datatype='Int', indicator='red'))
    else:
        signed = -sum_money(row['_signed_complementary_pending_usd'] for row in items)
        figures.append(dict(label='Complementarias pendientes netas US$', value=money_float(signed),
                            datatype='Currency', currency='USD', indicator='blue'))
    missing = unknown + sum(amounts.get(field, 0) is None for field in ('applied_pending_usd', 'deposit_pending_usd'))
    if missing:
        figures.append(dict(label='Saldo neto no determinable · importes por revisar', value=missing,
                            datatype='Int', indicator='red'))
    else:
        balance = money(amounts.get('applied_pending_usd')) - money(amounts.get('deposit_pending_usd')) - signed
        figures.append(dict(label='Saldo neto de conciliación US$', value=money_float(balance),
                            datatype='Currency', currency='USD', indicator='blue'))
    return figures
