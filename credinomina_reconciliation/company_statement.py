"""Current company position, one ledger per component; read-only informational net."""
import frappe
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.core_item_position import load_core_items
from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float, sum_money


METRICS = (
    ('application_pending_usd', 'Aplicado sin depósito US$'),
    ('core_pending_usd', 'Partidas del core pendientes US$'),
    ('company_credit_usd', 'Saldo a favor empresa US$'),
    ('client_credit_usd', 'Saldo a favor clientes US$'),
    ('deposit_pending_usd', 'Depósito sin conciliar US$'),
    ('company_receivable_usd', 'Saldo por cobrar a la empresa US$'),
)
FIELDS = tuple(field for field, label in METRICS)
CREDIT_CATEGORIES = {'Saldo a favor de la empresa', 'Saldo a favor del cliente'}


def _row(source, kind, doctype, document, event_date, field, amount, **extra):
    result = dict(employer=source.get('employer'), usd_currency='USD',
        event_date=event_date, position_type=kind, source_doctype=doctype,
        source_document=document, client_name=source.get('client_name') or source.get('source_client_name'),
        client_number=source.get('client_number'), loan_number=source.get('loan_number'),
        period=source.get('period'), source_rows=source.get('source_rows'),
        observation=source.get('observation') or '', **{key: 0.0 for key in FIELDS})
    result[field] = None if amount is None else money_float(amount)
    result['balance_usd'] = result[field]
    result.update(extra)
    return result


def build_detail(applications, core_items, credits, deposits, filters=None, *, adjustment_receivables=()):
    """Compute each remaining amount once; filters act after all-time settlement."""
    filters = filters or {}
    result = []
    for source in applications:
        amount = source.get('amount_usd')
        if amount is not None and not money(amount):
            continue
        doctype = 'CN Accounting Import' if source.get('source_import') else 'CN Reconciliation Period'
        result.append(_row(source, 'Aplicación', doctype,
            source.get('source_import') or source.get('period'),
            source.get('application_date') or source.get('payroll_month'),
            'application_pending_usd', amount,
            status='Sin conversión US$' if amount is None else 'Pendiente'))
    for item in core_items:
        if item.get('docstatus') == 2:
            continue
        # A documented credit is already represented by its outstanding management
        # balance below. Verified core evidence and used application offsets have
        # zero remaining financial balance, not a second deduction from receivables.
        if (item.get('category') in CREDIT_CATEGORIES and item.get('docstatus') == 1
                and item.get('result') == 'Saldo a favor documentado'):
            continue
        amount = item.get('_signed_pending_usd')
        if amount is not None and not money(amount):
            continue
        result.append(_row(item, 'Partida del core', 'CN Complementary Item', item['name'],
            item.get('source_date') or item.get('posting_date'), 'core_pending_usd', amount,
            status=item['_balance']['financial_status'], category=item.get('category'),
            reference=item.get('source_voucher') or item.get('voucher'),
            source_rows=str(item.get('source_row') or ''),
            observation=item.get('source_description') or item.get('description') or ''))
    for item in credits:
        if (item.get('docstatus') != 1 or item.get('result') != 'Saldo a favor documentado'
                or item.get('category') not in CREDIT_CATEGORIES):
            continue
        amount = (item.get('credit_pending_usd') if item.get('credit_management_status')
                  else item.get('amount_usd'))
        if amount is not None and not money(amount):
            continue
        company = item['category'] == 'Saldo a favor de la empresa'
        result.append(_row(item, item['category'], 'CN Complementary Item', item['name'],
            item.get('posting_date'), 'company_credit_usd' if company else 'client_credit_usd',
            None if amount is None else -money(amount), status=item.get('credit_management_status') or 'Pendiente',
            category=item['category'], reference=item.get('reference'),
            client_name='' if company else item.get('client_name'),
            client_number='' if company else item.get('client_number'),
            loan_number='' if company else item.get('loan_number'),
            observation=item.get('description') or ''))
    for deposit in deposits:
        confirmed = deposit.get('docstatus') == 1
        if deposit.get('docstatus') == 2 or not (confirmed or deposit.get('accounting_source_key')):
            continue
        # Imported core deposits are real ledger movements even before confirmation.
        # Manual drafts are not treated as received funds. Unconfirmed targets do not
        # consume cash. Subtract ALL documented surplus, even if already refunded:
        # otherwise a settled credit reappears as unassigned cash.
        amount = deposit.get('amount_usd')
        if amount is not None:
            amount = money(amount) - (money(deposit.get('allocated_usd'))
                + money(deposit.get('justified_surplus_usd')) if confirmed else money(0))
            if not amount:
                continue
        result.append(_row(deposit, 'Depósito', 'CN Remittance Allocation', deposit['name'],
            deposit.get('deposit_date'), 'deposit_pending_usd', None if amount is None else -amount,
            status=('Revisar distribución' if amount is not None and amount < 0 else
                    deposit.get('result') or 'Pendiente') if confirmed else 'Importado del core; sin confirmar',
            reference=deposit.get('deposit_reference'), observation=deposit.get('notes') or ''))
    for item in adjustment_receivables:
        result.append(_row(item, 'CxC por ajuste de depósito', 'CN Complementary Item', item['name'],
            item.get('posting_date'), 'company_receivable_usd', item['receivable_usd'],
            status=item['receivable_status'], category=item.get('category'),
            reference=item.get('reference'), related_deposits=item.get('related_deposits'),
            observation=item.get('description') or '', accounting_status=item.get('accounting_status')))
    def matches(row):
        if filters.get('employer') and row.get('employer') != filters['employer']:
            return False
        when = row.get('event_date')
        for field, check in [('from_date', lambda day, edge: day >= edge),
                             ('to_date', lambda day, edge: day <= edge)]:
            if filters.get(field) and (not when or not check(getdate(when), getdate(filters[field]))):
                return False
        return True
    return sorted((row for row in result if matches(row)), key=lambda row: (
        row.get('employer') or '', str(row.get('event_date') or ''), row['position_type'],
        row.get('source_document') or '', row.get('source_rows') or ''))


def summarize(detail):
    groups = {}
    for row in detail:
        employer = row.get('employer') or ''
        group = groups.setdefault(employer, dict(employer=employer or None, usd_currency='USD',
            **{field: money(0) for field in FIELDS}))
        for field in FIELDS:
            group[field] = (None if group[field] is None or row[field] is None
                            else group[field] + money(row[field]))
    result = []
    for employer in sorted(groups):
        row = groups[employer]
        row['balance_usd'] = (None if any(row[field] is None for field in FIELDS)
                              else sum_money(row[field] for field in FIELDS))
        for field in (*FIELDS, 'balance_usd'):
            if row[field] is not None:
                row[field] = money_float(row[field])
        result.append(row)
    return result


def load_detail(filters):
    required = ('CN Accounting Import', 'CN Reconciliation Period', 'CN Remittance Allocation',
                'CN Complementary Item')
    if any(not frappe.has_permission(doctype, 'read') for doctype in required):
        frappe.throw('Necesita permiso de lectura de aplicaciones, períodos, depósitos y partidas complementarias.',
                     frappe.PermissionError)
    if filters.get('from_date') and filters.get('to_date') and getdate(filters['from_date']) > getdate(filters['to_date']):
        frappe.throw('La fecha Desde no puede ser posterior a Hasta.')
    applications = application_balances(*load_application_context(), nowdate(), include_settled=True)
    scope = {'employer': filters['employer']} if filters.get('employer') else {}
    core = load_core_items(employer=filters.get('employer'))
    credits = list(records('CN Complementary Item', filters={**scope, 'docstatus': 1,
        'category': ['in', sorted(CREDIT_CATEGORIES)], 'result': 'Saldo a favor documentado'},
        fields=['name', 'employer', 'docstatus', 'result', 'category', 'posting_date', 'amount_usd',
                'credit_pending_usd', 'credit_management_status', 'client_name', 'client_number',
                'loan_number', 'period', 'reference', 'description']))
    deposits = list(records('CN Remittance Allocation', filters={**scope, 'docstatus': ['!=', 2]},
        fields=['name', 'employer', 'docstatus', 'accounting_source_key', 'deposit_date', 'deposit_reference',
                'amount_usd', 'allocated_usd', 'justified_surplus_usd', 'result', 'notes']))
    return build_detail(applications, core, credits, deposits, filters,
                        adjustment_receivables=load_receivables(employer=filters.get('employer')))
