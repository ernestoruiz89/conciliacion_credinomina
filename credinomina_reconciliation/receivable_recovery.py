"""Recover a transferred receivable through existing deposits or paired offsets.

An automatically created complementary receipt supplies the positive destination
for new cash. Its origin stays linked to the negative adjustment. Only actual
deposit distributions or confirmed offsets reduce the receivable.
"""
import re
import json
from itertools import islice
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.complementary_subcategories import RECEIVABLE
from credinomina_reconciliation.rounding import money, money_float, decimal_value

CATEGORY = 'Cobro de CxC'
_WRITE_TOKEN = object()
FROZEN = ('receivable_origin', 'receivable_operation', 'employer', 'posting_date', 'amount',
          'currency', 'fx_rate', 'reference', 'receivable_method', 'receivable_counterpart')


def is_receivable(item):
    return (item.get('docstatus') == 1 and item.get('category') == 'Ajuste de conciliación'
        and item.get('subcategory_effect') == RECEIVABLE and money(item.get('amount_usd')) < 0
        and not item.get('accounting_source_key'))


def guard_receipt(document, previous=None):
    if previous and previous.get('receivable_origin'):
        for field in FROZEN:
            before, after = previous.get(field), document.get(field)
            same = decimal_value(before) == decimal_value(after) if field in {'amount', 'fx_rate'} else str(before or '') == str(after or '')
            if not same:
                frappe.throw(_('El cobro de CxC conserva su origen, empresa e importe; revierta su distribución o compensación para corregirlo.'))
    elif (document.get('receivable_origin') or document.get('receivable_operation') or document.get('category') == CATEGORY):
        if document.flags.get('receivable_write_token') is not _WRITE_TOKEN:
            frappe.throw(_('Use Aplicar cobro / Compensar CxC desde la partida original.'))
    if document.get('receivable_origin'):
        if document.get('category') not in {CATEGORY, 'Compensación entre partidas'}:
            frappe.throw(_('El cobro de CxC no puede reclasificarse como otro concepto.'))
        if document.get('accounting_source_key') or document.get('related_application') or document.get('registered_deposit'):
            frappe.throw(_('El cobro de CxC es un vínculo de conciliación, sin una segunda aplicación ni asiento original.'))
        document.accounting_status = 'No requiere registro'


def guard_origin(document):
    if frappe.db.exists('CN Complementary Item', {'receivable_origin': document.name, 'docstatus': ['!=', 2]}):
        frappe.throw(_('La partida tiene cobros o compensaciones de CxC vinculados. Conserve el origen y revierta primero las operaciones relacionadas.'))


def validate_deposit_distributions(items, replacement):
    """An original shortfall cannot disappear while its recovery stays applied."""
    names = {item.get('name') for item in items if is_receivable(item)}
    names.update(item.get('receivable_origin') for item in items if item.get('receivable_origin'))
    if not names:
        return
    from credinomina_reconciliation.complementary_balances import load_balances
    originals = frappe.get_all('CN Complementary Item', filters={'name': ['in', sorted(names)]},
        fields=['name', 'employer', 'docstatus', 'category', 'amount_usd', 'subcategory_effect', 'accounting_source_key'])
    receipts = frappe.get_all('CN Complementary Item', filters={'receivable_origin': ['in', sorted(names)],
        'docstatus': ['!=', 2]}, fields=['name', 'receivable_origin', 'employer', 'category', 'docstatus', 'amount_usd', 'compensated_usd'])
    balances = load_balances([*originals, *receipts])
    for item in [*originals, *receipts]:
        distributions = balances[item['name']]['distributions']
        distributions[:] = [entry for entry in distributions if entry['deposit'] not in replacement]
        for deposit, entries in replacement.items():
            distributions.extend(dict(deposit=deposit, employer=entry.get('empresa') or item.get('employer'),
                amount_usd=entry.get('importe_usd')) for entry in entries
                if entry.get('tipo') == 'Partida complementaria' and entry.get('partida') == item['name'])
    for item in originals:
        debt, recovered = {}, {}
        for entry in balances[item['name']]['distributions']:
            company = entry.get('employer') or item.get('employer')
            debt[company] = debt.get(company, money(0)) - money(entry.get('amount_usd'))
        for receipt in receipts:
            if receipt.get('receivable_origin') != item['name'] or receipt.get('docstatus') != 1:
                continue
            amount = (money(receipt.get('compensated_usd')) if receipt.get('category') == 'Compensación entre partidas'
                else sum((money(entry.get('amount_usd')) for entry in balances[receipt['name']]['distributions']), money(0)))
            company = receipt.get('employer')
            recovered[company] = recovered.get(company, money(0)) + amount
        if any(amount > debt.get(company, money(0)) for company, amount in recovered.items()):
            frappe.throw(_('La distribución dejaría un cobro o compensación de CxC sin su faltante original ({0}). Revierta primero el cobro o compensación relacionado.').format(item['name']))


def position(item):
    from credinomina_reconciliation.complementary_balances import load_balances
    from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables, load_settlements
    receipts = load_settlements([item])
    balances = load_balances([item, *receipts])
    rows = build_receivables([item], balances, settlements=receipts, settlement_balances=balances)
    return rows, receipts, balances


def lock_recovery_origins(documents):
    """Use the cash-pool lock BEFORE item locks, including generic offsets.

    A receipt can be compensated again after an append-only reversal. Locking
    that receipt alone does not serialize it with a new recovery of its origin.
    The pool generation also rejects a repeatable-read snapshot taken before
    another recovery committed.
    """
    receipts = [doc for doc in documents if doc.get('receivable_origin')]
    if not receipts:
        return
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    companies = {company for receipt in receipts for company in reconciliation_companies(receipt.employer)}
    lock_cash_pool(companies)
    for name in sorted({receipt.receivable_origin for receipt in receipts}):
        original = frappe.get_doc('CN Complementary Item', name, for_update=True)
        original.check_permission('read')
        original.check_permission('write')
        if not is_receivable(original):
            frappe.throw(_('La CxC original ya no está disponible; recargue la partida.'))


def validate_compensation_recovery(documents, amount):
    for receipt in documents:
        if not receipt.get('receivable_origin'):
            continue
        # A recovery is a positive receivable destination. It cannot consume a
        # second recovery or credit belonging to an unidentified company.
        other = next(doc for doc in documents if doc.name != receipt.name)
        if other.employer != receipt.employer or other.get('receivable_origin'):
            frappe.throw(_('La partida para compensar la CxC debe pertenecer a la misma empresa.'))
        original = frappe.get_doc('CN Complementary Item', receipt.receivable_origin)
        original.check_permission('read')
        preview = preview_recovery(original.name)
        row = next((row for row in preview['companies'] if row.get('employer') == receipt.employer), None)
        own_reservation = (money(receipt.amount_usd) if receipt.docstatus == 0 and receipt.category == CATEGORY else money(0))
        if not row or amount > money(row['available_usd']) + own_reservation:
            frappe.throw(_('La compensación supera la CxC disponible; existen otros cobros o destinos reservados.'))


@frappe.whitelist()
def preview_recovery(item_name):
    item = frappe.get_doc('CN Complementary Item', item_name)
    item.check_permission('read')
    item.check_permission('write')
    if not is_receivable(item):
        frappe.throw(_('Seleccione un ajuste confirmado con subcategoría CxC a la empresa.'))
    rows, receipts, balances = position(item)
    # Planned receipt destinations reserve capacity, but do not settle the debt.
    companies = []
    for row in rows:
        reserved = money(0)
        for receipt in receipts:
            if receipt.get('employer') != row.get('employer'):
                continue
            if receipt.get('category') != 'Compensación entre partidas':
                reserved += max(money(receipt.get('amount_usd')) - money(balances[receipt['name']]['used_usd']), money(0))
        companies.append(dict(row, available_usd=None if row['receivable_usd'] is None else money_float(max(money(row['receivable_usd']) - reserved, money(0)))))
    return {'request_key': uuid4().hex, 'companies': companies, 'receipts': receipts}


def _check_cash_destination(deposit, employer, amount):
    from credinomina_reconciliation.paying_employers import allowed_employers
    deposit.check_permission('read')
    deposit.check_permission('write')
    if deposit.docstatus != 1 or employer not in allowed_employers(deposit.employer):
        frappe.throw(_('Seleccione un depósito confirmado que pueda pagar a esta empresa.'))
    deposit._assert_open_related_periods()
    available = cash_available(deposit)
    if amount > available:
        frappe.throw(_('El depósito no tiene suficiente importe sin distribuir.'))
    targets = {row.get('complementary_item') for row in deposit.get('targets') or []} - {None, ''}
    if targets:
        confirmed = set(frappe.get_all('CN Complementary Item', filters={
            'name': ['in', sorted(targets)], 'docstatus': 1}, pluck='name', limit_page_length=0))
        invalid = sorted(targets - confirmed)
        if invalid:
            frappe.throw(_('El depósito {0} conserva destinos de partidas canceladas o sin confirmar ({1}). Abra el depósito y retire o corrija esos destinos antes de aplicar un nuevo cobro. El historial de la partida y su cancelación se conserva.').format(
                deposit.name, ', '.join(invalid)))


def cash_available(deposit):
    reserved = sum((max(money(row.get('amount_usd')), money(0)) for row in deposit.get('targets') or []
                    if row.get('result') != 'Aplicada'), money(0))
    return max(money(deposit.amount_usd) - money(deposit.allocated_usd)
               - money(deposit.justified_surplus_usd) - reserved, money(0))


def _candidate_value(document, employer, method):
    if method == 'Depósito':
        _check_cash_destination(document, employer, money('0.01'))
        return cash_available(document)
    from credinomina_reconciliation.complementary_compensation import _eligible, _direction, balance, CREDIT_CATEGORIES
    document.check_permission('read')
    document.check_permission('write')
    if (document.employer != employer or document.get('receivable_origin')
            or document.category in CREDIT_CATEGORIES or _direction(document) != -1):
        frappe.throw(_('Seleccione una contrapartida de crédito de la misma empresa.'))
    _eligible(document)
    available = money(balance(document)['pending_usd'])
    if available <= 0:
        frappe.throw(_('La contrapartida no tiene saldo disponible.'))
    return available


def _recovery_scope(item_name, employer, method):
    if method not in {'Depósito', 'Compensación'}:
        frappe.throw(_('Seleccione el medio de liquidación.'))
    preview = preview_recovery(item_name)
    company = next((row for row in preview['companies'] if row.get('employer') == employer), None)
    if company and company.get('receivable_usd') is None:
        frappe.throw(_('La CxC tiene cobros o compensaciones inconsistentes. Revise sus vínculos antes de registrar otra liquidación.'))
    if not company or money(company['available_usd']) <= 0:
        frappe.throw(_('La empresa no tiene CxC disponible en esta partida.'))
    return company


@frappe.whitelist()
def get_recovery_destination(item_name, employer, method, destination):
    company = _recovery_scope(item_name, employer, method)
    doctype = 'CN Remittance Allocation' if method == 'Depósito' else 'CN Complementary Item'
    document = frappe.get_doc(doctype, destination)
    available = _candidate_value(document, employer, method)
    return dict(name=document.name, available_usd=money_float(available),
                receivable_usd=company['available_usd'],
                suggested_usd=money_float(min(available, money(company['available_usd']))))


def _prefilter_recovery_candidates(rows, method):
    """Batch cheap availability/closed-period checks before loading documents.

    These are exclusions, not authorization or settlement. The candidate and
    confirmation paths still run every permission, lock and financial check.
    """
    from collections import defaultdict
    from credinomina_reconciliation.report_records import child_records
    from credinomina_reconciliation.complementary_compensation import _direction, CREDIT_CATEGORIES
    pending, periods, items, journals = [], set(), set(), {}
    for row in rows:
        if method == 'Depósito':
            if cash_available(row) <= 0:
                continue
            try:
                journal = json.loads(row.get('allocation_detail') or '[]')
                if not isinstance(journal, list) or any(not isinstance(entry, dict) for entry in journal):
                    continue
            except (ValueError, TypeError):
                continue
            journals[row.name] = journal
            periods.update(entry['periodo'] for entry in journal if entry.get('periodo'))
            items.update(entry['partida'] for entry in journal if entry.get('partida') and not entry.get('periodo'))
        else:
            if (row.get('receivable_origin') or row.get('registration_exception') or row.get('related_application')
                    or row.get('registered_deposit') or row.category in set(CREDIT_CATEGORIES) | {'Ajuste de aplicación', 'Diferencia por tolerancia'}
                    or (row.docstatus == 1 and row.category != 'Compensación entre partidas')
                    or row.get('review_action') in {'Ajuste de aplicación', 'Partida de depósito', 'Reversión identificada'}):
                continue
            # Direction is derived from original evidence for core imports.
            debit, credit = money(row.get('source_debit')), money(row.get('source_credit'))
            if row.get('accounting_source_key') and not (credit > 0 and debit == 0):
                continue
            if _direction(row) != -1 or getdate(row.posting_date) > getdate(nowdate()):
                continue
            if row.get('period'):
                periods.add(row.period)
        pending.append(row)
    if not pending:
        return []
    names = [row.name for row in pending]
    targets = defaultdict(list)
    linked = set()
    if method == 'Depósito':
        for target in child_records('CN Remittance Target', names, 'CN Remittance Allocation', 'targets',
                fields=['parent', 'amount_usd', 'result', 'complementary_item']):
            targets[target.parent].append(target)
            if target.get('complementary_item'):
                items.add(target.complementary_item)
    else:
        # This query returns only exclusion identifiers, never hidden deposit data.
        linked = set(frappe.get_all('CN Remittance Target', filters={
            'complementary_item': ['in', names], 'docstatus': ['<', 2]}, pluck='complementary_item', limit_page_length=0))
    item_rows = {row.name: row for row in frappe.get_all('CN Complementary Item',
        filters={'name': ['in', sorted(items)]}, fields=['name', 'period', 'docstatus'], limit_page_length=0)} if items else {}
    periods.update(row.period for row in item_rows.values() if row.period)
    closed = set(frappe.get_all('CN Reconciliation Period', filters={
        'name': ['in', sorted(periods)], 'status': 'Cerrado'}, pluck='name', limit_page_length=0)) if periods else set()
    offsets = defaultdict(lambda: money(0))
    if method != 'Depósito':
        for entry in child_records('CN Complementary Offset', names, 'CN Complementary Item', 'compensations',
                filters={'compensation_date': ['<=', nowdate()]}, fields=['parent', 'amount_usd']):
            offsets[entry.parent] += money(entry.amount_usd)
    result = []
    for row in pending:
        if method == 'Depósito':
            if any((entry.get('periodo') or item_rows.get(entry.get('partida'), {}).get('period')) in closed
                   for entry in journals[row.name]):
                continue
            if any(target.get('complementary_item') and item_rows.get(target.complementary_item, {}).get('docstatus') != 1
                   for target in targets[row.name]):
                continue
            if cash_available(frappe._dict(row, targets=targets[row.name])) <= 0:
                continue
        elif row.name in linked or row.get('period') in closed or abs(money(row.amount_usd)) - offsets[row.name] <= 0:
            continue
        result.append(row)
    return result


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_recovery_destinations(doctype, txt, searchfield, start, page_len, filters):
    from frappe.utils import cint
    from credinomina_reconciliation.report_records import records
    from credinomina_reconciliation.paying_employers import load_payer_map
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    employer, method = filters.get('employer'), filters.get('method')
    _recovery_scope(filters.get('item_name'), employer, method)
    expected = 'CN Remittance Allocation' if method == 'Depósito' else 'CN Complementary Item'
    if doctype != expected:
        frappe.throw(_('Tipo de destino no válido.'))
    if method == 'Depósito':
        payers = [employer, *[payer for payer, beneficiaries in load_payer_map().items() if employer in beneficiaries]]
        scope = {'docstatus': 1, 'employer': ['in', payers]}
        search_fields = ('name', 'deposit_reference', 'bank_account')
        date_field = 'deposit_date'
    else:
        scope = {'docstatus': ['!=', 2], 'employer': employer, 'name': ['!=', filters.get('item_name')]}
        search_fields = ('name', 'reference', 'voucher', 'description')
        date_field = 'posting_date'
    offset, limit, found, result = max(cint(start), 0), min(max(cint(page_len), 1), 50), 0, []
    fields = (['amount_usd', 'allocated_usd', 'justified_surplus_usd', 'allocation_detail'] if method == 'Depósito' else
              ['category', 'review_action', 'amount_usd', 'registration_exception', 'related_application',
               'registered_deposit', 'receivable_origin', 'period', 'accounting_source_key', 'source_debit', 'source_credit'])
    rows = iter(records(doctype, fields=['name', 'employer', 'docstatus', date_field, *fields], filters=scope,
                or_filters={field: ['like', '%' + (txt or '')[:200] + '%'] for field in search_fields},
                order_by=f'{date_field} asc, name asc'))
    def candidates():
        while batch := list(islice(rows, 250)):
            yield from _prefilter_recovery_candidates(batch, method)
    for row in candidates():
        document = frappe.get_doc(doctype, row.name)
        message_count = len(frappe.local.message_log or [])
        try:
            available = _candidate_value(document, employer, method)
        except (frappe.ValidationError, frappe.PermissionError):
            # Rejected candidates are omitted, not shown as errors in a search dropdown.
            frappe.local.message_log = (frappe.local.message_log or [])[:message_count]
            continue
        if found >= offset:
            result.append([row.name, str(document.get(date_field)), document.employer,
                           _('Disponible US$ {0}').format(f'{available:,.2f}'),
                           document.get('deposit_reference') or document.get('reference') or '',
                           document.get('bank_account') or document.get('voucher') or ''])
        found += 1
        if len(result) >= limit:
            break
    return result


@frappe.whitelist(methods=['POST'])
def apply_recovery(item_name, employer, method, destination, amount_usd, recovery_date, reason, request_key):
    if not re.fullmatch(r'[a-f0-9]{32}', request_key or ''):
        frappe.throw(_('Abra de nuevo Aplicar cobro / Compensar CxC.'))
    if method not in {'Depósito', 'Compensación'} or not destination or not employer:
        frappe.throw(_('Seleccione empresa, tipo de liquidación y destino.'))
    amount, reason = money(amount_usd), (reason or '').strip()
    if amount <= 0 or not reason or not recovery_date:
        frappe.throw(_('Indique importe positivo, fecha y motivo del cobro o compensación.'))
    item = frappe.get_doc('CN Complementary Item', item_name)
    item.check_permission('read')
    item.check_permission('write')
    item.check_permission('submit')
    if not is_receivable(item):
        frappe.throw(_('La partida no es una CxC confirmada.'))
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    lock_cash_pool(reconciliation_companies(employer))
    item = frappe.get_doc('CN Complementary Item', item_name, for_update=True)
    if not is_receivable(item):
        frappe.throw(_('La CxC original ya no está disponible; recargue la partida.'))
    date = getdate(recovery_date)
    if date < getdate(item.posting_date) or date > getdate(nowdate()):
        frappe.throw(_('La fecha debe estar entre la fecha del ajuste y hoy.'))
    existing = frappe.db.get_value('CN Complementary Item', {'receivable_operation': request_key}, 'name')
    if existing:
        receipt = frappe.get_doc('CN Complementary Item', existing)
        receipt.check_permission('read')
        if (receipt.receivable_origin != item_name or receipt.employer != employer
                or receipt.receivable_method != method or receipt.receivable_counterpart != destination
                or money(receipt.amount_usd) != amount or getdate(receipt.posting_date) != date or receipt.description != reason):
            frappe.throw(_('Esta confirmación ya se utilizó con datos diferentes.'))
        if receipt.docstatus == 2:
            frappe.throw(_('Este cobro fue cancelado. Su reintento no lo reactiva; abra una nueva liquidación.'))
        return {'name': receipt.name, 'origin': item_name}
    preview = preview_recovery(item_name)
    company = next((row for row in preview['companies'] if row.get('employer') == employer), None)
    if not company or amount > money(company['available_usd']):
        frappe.throw(_('El importe supera la CxC disponible de esta empresa; recargue su saldo.'))
    deposit = None
    if method == 'Depósito':
        deposit = frappe.get_doc('CN Remittance Allocation', destination, for_update=True)
        _check_cash_destination(deposit, employer, amount)
    receipt = frappe.get_doc(dict(doctype='CN Complementary Item', category=CATEGORY,
        naming_series='CN-COMP-.YYYY.-.#####', receivable_origin=item_name,
        receivable_operation=request_key, receivable_method=method, receivable_counterpart=destination,
        employer=employer, posting_date=date, currency='USD', amount=money_float(amount),
        reference=destination, description=reason, accounting_status='No requiere registro'))
    receipt.flags.receivable_write_token = _WRITE_TOKEN
    receipt.flags.defer_reconciliation = True
    receipt.insert()
    if method == 'Compensación':
        from credinomina_reconciliation.complementary_compensation import confirm_compensation
        confirm_compensation(receipt.name, destination, money_float(amount), date, reason, request_key)
    else:
        from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
        receipt.submit()
        receipt.flags.pop('receivable_write_token', None)
        deposit.append('targets', dict(complementary_item=receipt.name, amount_usd=money_float(amount),
            notes=_('Cobro de CxC por ajuste {0}').format(item_name)))
        deposit.save()
        reconcile_deposit(deposit)
        deposit.reload()
        if not any(row.complementary_item == receipt.name and row.result == 'Aplicada' for row in deposit.targets):
            frappe.throw(_('El depósito no pudo aplicar el cobro. Revise su detalle y destinos; la operación no se guardó.'))
    return {'name': receipt.name, 'origin': item_name}
