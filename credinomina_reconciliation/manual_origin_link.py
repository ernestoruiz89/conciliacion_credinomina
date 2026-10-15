"""Explicit, atomic evidence linking to an existing receipt, without redistributing cash."""
import hashlib
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import now_datetime

from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
from credinomina_reconciliation.accounting_evidence import same_ledger_evidence
from credinomina_reconciliation.accounting_types import INTERNAL, TYPES, accounting_code
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money

DEPOSIT = 'CN Remittance Allocation'
ITEM = 'CN Complementary Item'
MAX_SELECTION = 2000
ITEM_FIELDS = list(dict.fromkeys(['name', 'modified', 'docstatus', 'employer', 'category',
    'review_action', 'origin_deposit', 'related_application', 'related_import', 'registered_deposit',
    'registration_exception', 'receivable_origin', 'period', 'loan_number', 'application_adjustment_usd',
    'compensated_usd', *[field for field in EVIDENCE_FIELDS if field != 'source_loan_number']]))
DEPOSIT_FIELDS = ['name', 'modified', 'docstatus', 'employer', 'bank_account', 'deposit_reference',
    'deposit_date', 'deposit_currency', 'deposit_amount', 'deposit_voucher', 'accounting_source_key',
    'cash_identity_status', 'consolidated_deposit', 'source_description']


def _names(selection, kind):
    values = frappe.parse_json(selection) if isinstance(selection, str) else selection
    if (kind not in {'internal', 'deposit'} or not isinstance(values, list) or not values
            or len(values) > (1 if kind == 'deposit' else MAX_SELECTION)
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)):
        frappe.throw(_('Seleccione partidas válidas o un único depósito del core.'))
    return sorted(values)


def _receipt(name, *, lock=False):
    doc = frappe.get_doc(DEPOSIT, name, for_update=lock)
    doc.check_permission('write')
    if doc.docstatus == 2 or doc.get('consolidated_deposit') or doc.get('cash_identity_status') in {'Origen vinculado', 'Revisar identidad'}:
        frappe.throw(_('Seleccione un depósito vigente cuya identidad bancaria esté verificada.'))
    return doc


@frappe.whitelist()
def candidates(deposit_name, kind='internal', reference='', voucher=''):
    deposit = _receipt(deposit_name)
    if kind not in {'internal', 'deposit'}:
        frappe.throw(_('Tipo de origen no válido.'))
    filters = {'docstatus': 0, 'employer': deposit.employer, 'accounting_source_key': ['is', 'set']}
    if kind == 'internal':
        filters.update(accounting_classification=INTERNAL, origin_deposit=['is', 'not set'],
                       category='Por clasificar', review_action='Pendiente de revisión')
        ref_field, voucher_field, fields, doctype = 'accounting_reference', 'source_voucher', ITEM_FIELDS, ITEM
    else:
        filters.update(name=['!=', deposit.name], consolidated_deposit=['is', 'not set'])
        ref_field, voucher_field, fields, doctype = 'deposit_reference', 'deposit_voucher', DEPOSIT_FIELDS, DEPOSIT
    if clean_text(reference):
        filters[ref_field] = clean_text(reference)
    if clean_text(voucher):
        filters[voucher_field] = clean_text(voucher)
    rows = frappe.get_list(doctype, filters=filters, fields=fields,
                           order_by='name asc', limit_page_length=MAX_SELECTION + 1)
    if len(rows) > MAX_SELECTION:
        frappe.throw(_('Hay más de {0} resultados. Filtre por referencia o asiento antes de seleccionar.').format(MAX_SELECTION))
    return rows


def _load(deposit_name, selection, kind, *, lock=False):
    names = _names(selection, kind)
    if deposit_name in names and kind == 'deposit':
        frappe.throw(_('El origen debe ser otro documento.'))
    # Match the importer's lock order, then lock receipts in deterministic order.
    receipts = {name: _receipt(name, lock=lock) if name == deposit_name else
                frappe.get_doc(DEPOSIT, name, for_update=lock)
                for name in sorted([deposit_name, *(names if kind == 'deposit' else [])])}
    origins = ([receipts[name] for name in names] if kind == 'deposit' else
               [frappe.get_doc(ITEM, name, for_update=lock) for name in names])
    for origin in origins:
        origin.check_permission('write')
    return receipts[deposit_name], origins


def _internal_issues(item, deposit):
    issues = []
    if (item.get('docstatus') != 0 or item.get('category') != 'Por clasificar'
            or item.get('review_action') != 'Pendiente de revisión'
            or item.get('accounting_classification') != INTERNAL
            or TYPES.get((accounting_code(item.get('tmov')), accounting_code(item.get('tdoc')))) != INTERNAL):
        issues.append(_('Solo se vinculan movimientos internos en borrador pendientes de revisión.'))
    if any(item.get(field) for field in ('related_application', 'related_import', 'registered_deposit',
            'registration_exception', 'receivable_origin', 'period', 'compensations')) or any(
            money(item.get(field)) for field in ('application_adjustment_usd', 'compensated_usd')):
        issues.append(_('La partida tiene otro tratamiento o vínculo financiero; revíselo primero.'))
    if item.get('origin_deposit') and item.get('origin_deposit') != deposit.name:
        issues.append(_('La partida ya respalda otro depósito.'))
    if money(item.get('source_debit')) or money(item.get('source_credit')) <= 0:
        issues.append(_('El origen debe tener crédito positivo y débito cero; revise ajustes o reversiones.'))
    if item.get('source_currency') == 'NIO' and decimal_value(item.get('source_fx_rate')) <= 0:
        issues.append(_('Falta el tipo de cambio contable original.'))
    for original, target, label in [('employer', 'employer', 'empresa'),
            ('accounting_reference', 'deposit_reference', 'referencia'),
            ('source_date', 'deposit_date', 'fecha del depósito'),
            ('source_currency', 'deposit_currency', 'moneda')]:
        if not clean_text(item.get(original)) or clean_text(item.get(original)) != clean_text(deposit.get(target)):
            issues.append(_('No coincide {0}.').format(label))
    # Each internal movement keeps its own ledger voucher. It need not match
    # the bank receipt's voucher or the vouchers of the other selected lines.
    if not all(item.get(field) for field in ('accounting_source_key', 'source_file', 'source_file_hash',
                                            'source_row', 'source_account', 'source_voucher')):
        issues.append(_('Falta evidencia original de archivo, fila, cuenta o asiento.'))
    return issues


def inspect_selection(deposit, origins, kind):
    """Compare original currency totals; accounting FX never rewrites cash FX."""
    issues, values = [], []
    totals = defaultdict(lambda: money(0))
    existing = deposit.get('accounting_sources') or []
    if not deposit.get('employer') or deposit.get('employer') == 'NO IDENTIFICADA' or not deposit.get('bank_account') or deposit.get('bank_account') == 'NO IDENTIFICADA':
        issues.append(_('Identifique la empresa y la cuenta bancaria del depósito.'))
    if kind == 'internal':
        for item in origins:
            issues.extend(f'{item.name}: {issue}' for issue in _internal_issues(item, deposit))
            values.append({**{field: item.get(field) for field in EVIDENCE_FIELDS},
                           'source_loan_number': item.get('loan_number'), 'origin_complementary_item': item.name})
        for item in origins:
            totals[item.get('source_currency') or 'Sin identificar'] += money(item.get('source_credit'))
    else:
        origin = origins[0]
        linked_elsewhere = origin.get('consolidated_deposit') and origin.get('consolidated_deposit') != deposit.name
        if (origin.docstatus != 0 or origin.get('targets') or origin.get('detail_rows') or origin.get('detail_file')
                or linked_elsewhere or (origin.get('cash_identity_status') == 'Origen vinculado'
                                        and origin.get('consolidated_deposit') != deposit.name)):
            issues.append(_('El depósito del core debe estar en borrador, sin detalle ni destinos. Revise su conciliación antes de vincularlo.'))
        if not origin.get('accounting_source_key'):
            issues.append(_('El depósito seleccionado no tiene origen contable.'))
        if any(source.get('origin_complementary_item') for source in origin.get('accounting_sources') or []):
            issues.append(_('El depósito ya respalda movimientos internos. Conserve ese depósito y revise sus vínculos.'))
        for field, label in [('employer', 'empresa'), ('deposit_reference', 'referencia'),
                ('deposit_date', 'fecha del depósito'), ('deposit_currency', 'moneda')]:
            if not clean_text(origin.get(field)) or clean_text(origin.get(field)) != clean_text(deposit.get(field)):
                issues.append(_('No coincide {0}.').format(label))
        if origin.get('bank_account') not in {deposit.get('bank_account'), 'NO IDENTIFICADA'}:
            issues.append(_('No coincide la cuenta bancaria.'))
        if origin.get('deposit_voucher') and deposit.get('deposit_voucher') and origin.deposit_voucher != deposit.deposit_voucher:
            issues.append(_('No coincide el asiento.'))
        values = [{field: source.get(field) for field in EVIDENCE_FIELDS}
                  for source in origin.get('accounting_sources') or [origin]]
        if any(not all(row.get(field) for field in ('source_file', 'source_file_hash', 'source_row', 'source_account', 'source_date'))
               or row.get('accounting_classification') != 'Depósito'
               or money(row.get('source_debit')) or money(row.get('source_credit')) <= 0 for row in values):
            issues.append(_('Revise la evidencia original del depósito del core: debe contener créditos de depósito identificados, sin débitos.'))
        if all(row.get('source_currency') == deposit.deposit_currency for row in values):
            ledger_total = sum_money(row.get('source_credit') for row in values)
            if ledger_total != money(deposit.deposit_amount):
                issues.append(_('Las líneas contables suman {0} {1:.2f}; revise la diferencia con el efectivo.').format(deposit.deposit_currency, ledger_total))
        totals[origin.get('deposit_currency') or 'Sin identificar'] = money(origin.get('deposit_amount'))
    total = totals[deposit.deposit_currency] if set(totals) == {deposit.deposit_currency} else None
    difference = total - money(deposit.get('deposit_amount')) if total is not None else None
    if difference is not None and difference:
        issues.append(_('El importe seleccionado no coincide con el depósito. Diferencia {0} {1:.2f}.').format(deposit.deposit_currency, difference))
    keys = [row.get('accounting_source_key') for row in values]
    if not all(keys) or len(set(keys)) != len(keys):
        issues.append(_('La selección contiene identidades contables vacías o repetidas.'))
    by_key = {row.get('accounting_source_key'): row for row in existing}
    already = bool(values) and all(row.get('accounting_source_key') in by_key and same_ledger_evidence(
        row, by_key[row.get('accounting_source_key')]) for row in values)
    if (existing or deposit.get('accounting_source_key')) and not already:
        issues.append(_('El depósito ya tiene origen contable; revise sus líneas antes de agregar otro respaldo completo.'))
    if already:
        already = (all(item.get('origin_deposit') == deposit.name for item in origins) if kind == 'internal'
                   else origins[0].get('consolidated_deposit') == deposit.name)
        if not already:
            issues.append(_('Las líneas ya existen pero su vínculo de origen es inconsistente.'))
    payload = [deposit.name, str(deposit.modified), kind, [(item.name, str(item.modified)) for item in origins], values]
    token = hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()
    return dict(count=len(values), currency=deposit.deposit_currency,
                totals_by_currency={currency: money_float(amount) for currency, amount in totals.items()},
                total=money_float(total) if total is not None else None,
                deposit_amount=money_float(deposit.deposit_amount),
                difference=money_float(difference) if difference is not None else None,
                issues=list(dict.fromkeys(issues)), already_linked=already, token=token), values


def _check_ownership(deposit, origins, values, kind):
    """Unrestricted existence checks prevent duplicates without exposing other records."""
    keys = [row['accounting_source_key'] for row in values if row.get('accounting_source_key')]
    if not keys:
        return
    allowed = {deposit.name, *(item.name for item in origins if kind == 'deposit')}
    linked = frappe.get_all('CN Deposit Accounting Source', filters={'accounting_source_key': ['in', keys]},
                            fields=['parent'], limit_page_length=0)
    headers = frappe.get_all(DEPOSIT, filters={'accounting_source_key': ['in', keys]}, pluck='name')
    if (any(row.parent not in allowed for row in linked) or any(name not in allowed for name in headers)):
        frappe.throw(_('Una línea ya está registrada en otro depósito. Revise su origen; no se duplicó ningún movimiento.'))
    if kind == 'internal' and frappe.db.exists('CN Remittance Target', {'complementary_item': ['in', [item.name for item in origins]]}):
        frappe.throw(_('Una partida ya figura como destino de un depósito. Revise ese vínculo primero.'))
    if kind == 'deposit' and frappe.db.exists(ITEM, {'accounting_source_key': ['in', keys]}):
        frappe.throw(_('Una línea también existe como partida complementaria; revise esa duplicidad antes de vincular.'))


@frappe.whitelist()
def preview(deposit_name, selection, kind='internal'):
    deposit, origins = _load(deposit_name, selection, kind)
    result, values = inspect_selection(deposit, origins, kind)
    _check_ownership(deposit, origins, values, kind)
    return result


def _insert_sources(deposit, values, timestamp, *, set_primary=True):
    from credinomina_reconciliation.file_references import attach_existing_file
    for value in values:
        child = deposit.append('accounting_sources', value)
        child.update(dict(docstatus=deposit.docstatus, owner=frappe.session.user, creation=timestamp,
                          modified=timestamp, modified_by=frappe.session.user))
        child.db_insert()
    for file in sorted({value.get('source_file') for value in values if value.get('source_file')}):
        attach_existing_file(file, DEPOSIT, deposit.name, 'source_file')
    # The core receipt retains its unique header key. The canonical receipt
    # exposes those same immutable lines through the child table instead.
    if set_primary and not deposit.get('accounting_source_key'):
        frappe.db.set_value(DEPOSIT, deposit.name, {field: values[0].get(field) for field in EVIDENCE_FIELDS}, update_modified=False)


@frappe.whitelist(methods=['POST'])
def link(deposit_name, selection, token, reason, kind='internal'):
    from credinomina_reconciliation.accounting_batch_store import creation_guard
    reason = clean_text(reason)
    if not reason:
        frappe.throw(_('Documente cómo verificó que se trata del mismo depósito.'))
    with creation_guard():
        deposit, origins = _load(deposit_name, selection, kind, lock=True)
        result, values = inspect_selection(deposit, origins, kind)
        _check_ownership(deposit, origins, values, kind)
        if result['already_linked'] and not result['issues']:
            return dict(result, status='Vinculado', name=deposit.name)
        if token != result['token']:
            frappe.throw(_('Los datos cambiaron desde la revisión. Vuelva a comprobar la selección.'))
        status = 'Pendiente de revisión' if result['issues'] else 'Vinculado'
        selected_totals = '; '.join(f'{currency} {amount:.2f}' for currency, amount in result['totals_by_currency'].items())
        difference = _('No comparable: monedas distintas') if result['difference'] is None else f"{result['difference']:.2f}"
        notes = '\n'.join([reason, _('Selección: {0}').format(', '.join(item.name for item in origins)),
            _('Total seleccionado: {0}; depósito: {1} {2:.2f}; diferencia: {3}.').format(
                selected_totals, result['currency'], result['deposit_amount'], difference), *result['issues']])
        timestamp = now_datetime()
        # Narrow evidence writes preserve confirmed distributions, closed periods,
        # operational dates, cash amounts and their original exchange rate.
        if not result['issues']:
            _insert_sources(deposit, values, timestamp, set_primary=kind == 'internal')
            for origin in origins:
                if kind == 'internal':
                    updates = dict(origin_deposit=deposit.name, review_status='Origen contable vinculado', review_notes=reason)
                else:
                    updates = dict(consolidated_deposit=deposit.name, cash_identity_status='Origen vinculado', cash_identity_reason=reason)
                frappe.db.set_value(origin.doctype, origin.name, dict(updates, modified=timestamp, modified_by=frappe.session.user))
            if kind == 'deposit':
                frappe.db.set_value(DEPOSIT, deposit.name, 'linked_core_deposit', origins[0].name, update_modified=False)
                origins[0].add_comment('Comment', _('Origen contable vinculado a {0}. {1}').format(deposit.name, reason))
        frappe.db.set_value(DEPOSIT, deposit.name, dict(origin_link_status=status, origin_link_notes=notes,
                            modified=timestamp, modified_by=frappe.session.user))
        deposit.add_comment('Comment', _('Vinculación de origen: {0}.\n{1}').format(status, notes))
        return dict(result, status=status, name=deposit.name)


def guard_item(doc, previous=None):
    if doc.get('origin_deposit') or (previous and previous.get('origin_deposit')):
        frappe.throw(_('Esta partida es origen contable de un depósito. Se conserva como evidencia y no admite cambios ni efectos financieros.'))


def guard_removal(doc):
    linked = False
    if doc.get('doctype') == ITEM:
        linked = doc.get('origin_deposit') or (doc.get('name') and frappe.db.get_value(ITEM, doc.name, 'origin_deposit'))
    elif doc.get('doctype') == DEPOSIT:
        linked = any(row.get('origin_complementary_item') for row in doc.get('accounting_sources') or [])
        if not linked and doc.get('name'):
            linked = frappe.db.get_value('CN Deposit Accounting Source', {'parent': doc.name,
                'parenttype': DEPOSIT, 'parentfield': 'accounting_sources',
                'origin_complementary_item': ['is', 'set']}, 'name')
    if linked:
        frappe.throw(_('Conserve el depósito y sus movimientos de origen vinculados. Use Desconciliar para revisar la distribución del efectivo.'))
