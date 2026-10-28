"""Use reserved credit against imported applications without reopening its cash origin.

The dated, append-only credit journal is funding evidence, not another deposit.
Only destination balances are rebuilt; the original cash distribution is frozen.
"""
import json
from collections import defaultdict
from html import escape
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from credinomina_reconciliation.client_credit import CATEGORY, CREDIT_CATEGORIES, RESULT
from credinomina_reconciliation.parsers import clean_text, canonical_credit_number
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation import credit_usage


def journal_allocations(items, application_ids=None, cutoff=None):
    result = []
    for item in items:
        if item.get('docstatus') != 1 or item.get('category') not in CREDIT_CATEGORIES:
            continue
        history = item.get('credit_history') or []
        history = json.loads(history) if isinstance(history, str) else history
        history = [entry for entry in history if not cutoff or getdate(entry['fecha']) <= getdate(cutoff)]
        reversed_ids = {entry.get('reverses') for entry in history if entry.get('reverses')}
        for entry in history:
            if (not entry.get('application_id') or entry.get('reverses')
                    or entry.get('operation_id') in reversed_ids
                    or (application_ids is not None and entry['application_id'] not in application_ids)):
                continue
            result.append(dict(entry, item=item.get('name'), employer=item.get('employer'),
                               deposit=item.get('registered_deposit')))
    return result


def load_allocations(application_ids):
    names = sorted(set(application_ids))
    if not names:
        return []
    found = {}
    for offset in range(0, len(names), 100):
        for item in frappe.get_all('CN Complementary Item', filters={
            'docstatus': 1, 'category': ['in', CREDIT_CATEGORIES]},
            or_filters=[['credit_history', 'like', '%"application_id": "' + name + '"%']
                        for name in names[offset:offset + 100]],
            fields=['name', 'category', 'docstatus', 'registered_deposit', 'employer', 'credit_history'],
            limit_page_length=0):
            found[item.name] = item
    return journal_allocations(found.values(), set(names))


def funding_totals(application_ids):
    totals = defaultdict(lambda: money(0))
    for entry in load_allocations(application_ids):
        totals[entry['application_id']] += money(entry['importe_usd'])
    return totals


def with_funding(allocation, sources):
    """Read-only projection for balance builders, never persisted as bank cash."""
    credits = load_allocations(row.name for row in sources if row.get('historical_period'))
    if not credits:
        return allocation
    result = dict(allocation, allocations=list(allocation['allocations']), deposit_meta=dict(allocation['deposit_meta']))
    for entry in credits:
        key = 'credit:' + entry['operation_id']
        account = frappe._dict(reference=entry['item'], voucher=entry.get('referencia'),
            event_date=entry['fecha'], unclassified_usd=0)
        result['deposit_meta'][key] = dict(account=account, bank=account,
            employer=entry['employer'], amount_usd=entry['importe_usd'], nio_per_usd=0)
        result['allocations'].append(dict(deposit_id=key, claim_id='H:' + entry['application_id'],
            amount_usd=entry['importe_usd'], origin='Saldo a favor',
            credit_item=entry['item'], credit_operation=entry['operation_id'], origin_deposit=entry['deposit']))
    return result


def _check_item(item):
    item.check_permission('read')
    item.check_permission('write')
    item.check_permission('submit')
    if item.docstatus != 1 or item.category not in CREDIT_CATEGORIES or item.result != RESULT:
        frappe.throw(_('Seleccione un saldo a favor confirmado y documentado.'))


def _identity_matches(item, row, employer):
    if employer != item.employer:
        return False
    if item.category == CATEGORY:
        if row.get('client'):
            if row.get('client') != item.credit_client:
                return False
        elif not item.client_number or clean_text(row.get('client_number')) != clean_text(item.client_number):
            return False
        if item.loan_number and canonical_credit_number(row.get('loan_number')) != canonical_credit_number(item.loan_number):
            return False
    return True


@frappe.whitelist()
def candidates(item_name):
    from credinomina_reconciliation.remittance_selection import _historical_sources
    item = frappe.get_doc('CN Complementary Item', item_name)
    _check_item(item)
    periods = frappe.get_list('CN Reconciliation Period', filters={
        'employer': item.employer, 'status': ['!=', 'Cerrado']}, pluck='name', limit_page_length=0)
    rows = []
    for imported in _historical_sources(periods):
        for row in imported.rows:
            if (row.historical_period not in periods or row.event_type != 'Aplicacion'
                    or not row.effective or row.match_status != 'Conciliado'
                    or not _identity_matches(item, row, imported.employer)
                    or money(row.historical_balance_usd) <= 0):
                continue
            rows.append(dict(name=row.name, import_name=imported.name, period=row.historical_period,
                event_date=str(row.event_date), client_name=row.client_name, loan_number=row.loan_number,
                reference=row.voucher or row.accounting_entry or row.reference,
                pending_usd=money_float(row.historical_balance_usd)))
    return dict(modified=str(item.modified), pending_usd=credit_usage.totals(item)['credit_pending_usd'], rows=rows)


def _locked_context(item_name, modified, application_id):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    initial = frappe.get_doc('CN Complementary Item', item_name)
    _check_item(initial)
    lock_cash_pool(reconciliation_companies(initial.employer))
    item = frappe.get_doc('CN Complementary Item', item_name, for_update=True)
    _check_item(item)
    if str(item.modified) != str(modified):
        frappe.throw(_('El saldo a favor cambió. Recargue antes de aplicarlo.'))
    origin = frappe.get_doc('CN Remittance Allocation', item.registered_deposit)
    origin.check_permission('read')
    if origin.docstatus != 1 or origin.get('cash_identity_status') in {'Revisar identidad', 'Origen vinculado'}:
        frappe.throw(_('El depósito de origen no tiene efectivo confirmado disponible.'))
    parent = frappe.db.get_value('CN Source Row', {'name': application_id, 'parenttype': 'CN Accounting Import'}, 'parent')
    if not parent:
        frappe.throw(_('La aplicación contable ya no existe.'))
    imported = frappe.get_doc('CN Accounting Import', parent, for_update=True)
    imported.check_permission('read')
    imported.check_permission('write')
    row = next((row for row in imported.rows if row.name == application_id), None)
    if (not row or row.event_type != 'Aplicacion' or not row.effective or row.match_status != 'Conciliado'
            or not row.historical_period or imported.status not in {'Importado', 'Importado con excepciones'}
            or not _identity_matches(item, row, imported.employer)):
        frappe.throw(_('Seleccione una aplicación importada vinculada a un período, de la misma empresa, cliente y crédito.'))
    period = frappe.get_doc('CN Reconciliation Period', row.historical_period, for_update=True)
    period.check_permission('read')
    period.check_permission('write')
    if period.employer != item.employer:
        frappe.throw(_('El período de destino debe pertenecer a la empresa del saldo a favor.'))
    if period.status == 'Cerrado':
        frappe.throw(_('El período de destino está cerrado. El período de origen del saldo no necesita reabrirse.'))
    return item, row, period


def _save_journal(item, entry):
    from credinomina_reconciliation.complementary_state import mark
    mark([item.name])
    history = json.loads(item.credit_history or '[]')
    if credit_usage.resolved_amount(item, history) != money(item.credit_resolved_usd):
        frappe.throw(_('El saldo gestionado no coincide con su historial. Revise la partida.'))
    history.append(entry)
    values = credit_usage.totals(item, history=history)
    pending, status = values['credit_pending_usd'], values['credit_management_status']
    frappe.db.set_value(item.doctype, item.name, dict(credit_history=json.dumps(history, ensure_ascii=False), credit_usage_issue='', **values))
    item.clear_cache()
    return dict(name=item.name, pending_usd=money_float(pending), status=status)


def validate_dated_history(original, history, offsets=()):
    credit_usage.validate_history(original, history, offsets)


def rebuild_destination(period):
    from credinomina_reconciliation.complementary_cancellation import _load_imports, _related_cash
    from credinomina_reconciliation.deposit_reconciliation import stored_cash_context
    from credinomina_reconciliation.reconciliation_scope import document_state
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    imports = _load_imports([period], [], [], {period.employer})
    rows = []
    collection_ids = {collection.name for collection in period.collection_rows}
    period._reconciliation_original_state = document_state(period)
    for imported in imports:
        imported.check_permission('write')
        imported._reconciliation_original_state = document_state(imported)
        for row in imported.rows:
            row._source_import, row._source_employer = imported.name, imported.employer
            if row.historical_period == period.name or any(link.get('collection_row_id') in collection_ids
                                                          for link in engine._application_allocations(row)):
                rows.append(row)
    complements = []
    complementary_target, complementary_totals = {}, defaultdict(float)
    if period.reconciliation_mode != 'Historica':
        from credinomina_reconciliation.paying_employers import reconciliation_companies
        complements = frappe.get_all('CN Complementary Item', filters={'docstatus': 1,
            'employer': ['in', reconciliation_companies(period.employer)], 'origin_deposit': ['is', 'not set'],
            'category': ['not in', [*CREDIT_CATEGORIES, engine.TOLERANCE_CATEGORY, engine.APPLICATION_ADJUSTMENT,
                                   'Compensación entre partidas', 'Vinculado a depósito']]},
            fields=['name', 'reference', 'amount_usd', 'employer', 'period', 'client_number', 'loan_number',
                    'installment_number', 'generic_distribution', 'receivable_origin', 'category',
                    'subcategory_effect', 'docstatus', 'accounting_source_key'], limit_page_length=0)
        evidence_periods = engine._load_open_periods(['in', reconciliation_companies(period.employer)])
        mapping = engine._allocate_complementary_items(complements, evidence_periods)
        for (target, _reference), items in mapping.items():
            for item in items:
                complementary_target[item.name] = target
                complementary_totals[target] += money_float(item.amount_usd)
    deposits = _related_cash([], [period], rows, complements)
    extra = {entry.get('periodo') for deposit in deposits for entry in json.loads(deposit.allocation_detail or '[]')}
    evidence = [period, *(frappe.get_doc('CN Reconciliation Period', name) for name in extra - {None, '', period.name})]
    allocation = stored_cash_context(deposits, evidence)
    # No deposit allocation is recalculated or saved, including closed origins.
    allocation.update(complementary_totals=complementary_totals, complementary_target=complementary_target,
                      registered_ids={}, balance_registered_ids=allocation['registered_ids'])
    engine._rebuild_historical_balances([period], rows, allocation)
    if period.reconciliation_mode != 'Historica':
        engine._rebuild_period_balances([period], rows, [], allocation, {}, {}, complements,
            source_status_collections=[row for doc in evidence for row in doc.collection_rows],
            status_source_ids={row.name for row in rows})
    from credinomina_reconciliation.application_adjustments import mark_mixed_settlements
    mark_mixed_settlements(rows, [row for doc in evidence for row in doc.collection_rows])
    active = {row.parent for row in rows}
    for imported in imports:
        if imported.name not in active:
            continue
        imported.recalculate_summary()
        imported.status = 'Importado con excepciones' if imported.exception_count else 'Importado'
        token = engine._source_reconcile_verified.set(True)
        try:
            engine._save_reconciled_document(imported)
        finally:
            engine._source_reconcile_verified.reset(token)


@frappe.whitelist(methods=['POST'])
def apply(item_name, modified, application_id, amount_usd, event_date, notes=''):
    from credinomina_reconciliation.application_adjustments import cash_coverage
    item, row, period = _locked_context(item_name, modified, application_id)
    amount = money(amount_usd)
    if amount <= 0 or amount > money(credit_usage.totals(item)['credit_pending_usd']):
        frappe.throw(_('Indique un importe positivo que no supere el saldo a favor disponible.'))
    if (not event_date or getdate(event_date) < max(getdate(item.get('credit_origin_date') or item.posting_date), getdate(row.event_date))
            or getdate(event_date) > getdate()):
        frappe.throw(_('La fecha debe ser igual o posterior al saldo y a la aplicación, y no puede ser futura.'))
    available = money(cash_coverage(row, lock=True)['adjustable_usd'])
    if amount > available:
        frappe.throw(_('El importe supera el pendiente de la aplicación; revise sus depósitos y destinos reservados.'))
    entry = dict(operation_id=uuid4().hex, tratamiento='Aplicación contable', importe_usd=money_float(amount),
        fecha=str(getdate(event_date)), application_id=row.name, period=period.name, import_name=row.parent,
        referencia=row.voucher or row.accounting_entry or row.reference, observaciones=clean_text(notes),
        usuario=frappe.session.user, registrado_el=str(now_datetime()))
    result = _save_journal(item, entry)
    rebuild_destination(period)
    item.add_comment('Info', _('Saldo aplicado: US$ {0}; aplicación {1}; período {2}. Se conserva el depósito original.').format(
        amount, escape(row.name), escape(period.name)))
    return dict(result, period=period.name, application_id=row.name,
                application_pending_usd=frappe.db.get_value('CN Source Row', row.name, 'historical_balance_usd'))


def reverse(item_name, modified, entry_id, event_date, reason):
    initial = frappe.get_doc('CN Complementary Item', item_name)
    _check_item(initial)
    original = next((entry for entry in journal_allocations([initial]) if entry['operation_id'] == entry_id), None)
    if not original:
        frappe.throw(_('La aplicación del saldo ya fue revertida o no existe.'))
    item, row, period = _locked_context(item_name, modified, original['application_id'])
    if not clean_text(reason) or not event_date or getdate(event_date) < getdate(original['fecha']) or getdate(event_date) > getdate():
        frappe.throw(_('Indique motivo y una fecha de reversión válida, posterior o igual a la aplicación del saldo.'))
    entry = dict(operation_id=uuid4().hex, reverses=entry_id, tratamiento='Reversión de aplicación contable',
        importe_usd=-original['importe_usd'], fecha=str(getdate(event_date)), application_id=row.name,
        period=period.name, import_name=row.parent, referencia=original.get('referencia'),
        observaciones=clean_text(reason), usuario=frappe.session.user, registrado_el=str(now_datetime()))
    result = _save_journal(item, entry)
    rebuild_destination(period)
    item.add_comment('Info', _('Aplicación de saldo revertida: {0}. Motivo: {1}').format(escape(entry_id), escape(reason)))
    return result
