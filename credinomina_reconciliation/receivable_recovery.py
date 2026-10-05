"""Recover a transferred receivable through existing deposits or paired offsets.

An automatically created complementary receipt supplies the positive destination
for new cash. Its origin stays linked to the negative adjustment. Only actual
deposit distributions or confirmed offsets reduce the receivable.
"""
import re
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
        companies.append(dict(row, available_usd=money_float(max(money(row['receivable_usd']) - reserved, money(0)))))
    return {'request_key': uuid4().hex, 'companies': companies, 'receipts': receipts}


def _check_cash_destination(deposit, employer, amount):
    from credinomina_reconciliation.paying_employers import allowed_employers
    deposit.check_permission('read')
    deposit.check_permission('write')
    if deposit.docstatus != 1 or employer not in allowed_employers(deposit.employer):
        frappe.throw(_('Seleccione un depósito confirmado que pueda pagar a esta empresa.'))
    available = money(deposit.amount_usd) - money(deposit.allocated_usd) - money(deposit.justified_surplus_usd)
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
