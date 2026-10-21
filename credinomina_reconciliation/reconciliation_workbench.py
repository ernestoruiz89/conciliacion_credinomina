"""Client workbench and read-only checks using the existing destination rules."""
import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import now_datetime

from credinomina_reconciliation import accounting_related_records as related
from credinomina_reconciliation.complementary_balances import load_balances
from credinomina_reconciliation.parsers import canonical_identifier, clean_text
from credinomina_reconciliation.rounding import decimal_value, money, money_float


def _scope(employer=None, client=None, context=None):
    # Form-encoded requests can turn an optional null into an empty string.
    if isinstance(context, str):
        try:
            context = frappe.parse_json(context) if context.strip() else None
        except ValueError:
            frappe.throw(_('El origen de la consulta no es válido.'))
    if context is None:
        context = {}
    if not isinstance(context, dict):
        frappe.throw(_('El origen de la consulta no es válido.'))
    if context.get('import_name'):
        document = frappe.get_doc('CN Accounting Import', context['import_name'])
        document.check_permission('read')
        identity = related._identity(document, context.get('source_row_name'))
        company = document.employer
    elif context.get('remittance_name'):
        document = frappe.get_doc('CN Remittance Allocation', context['remittance_name'])
        document.check_permission('read')
        detail = next((row for row in document.detail_rows
                       if row.name == context.get('detail_row_name')), None)
        if detail is None:
            frappe.throw(_('La fila no pertenece a este depósito.'))
        identity = related._identity_values(detail, [detail])
        company = detail.employer or document.employer
    else:
        if not employer or not client:
            frappe.throw(_('Seleccione empresa y cliente para consultar la mesa.'))
        document = frappe.get_doc('CN Client', client)
        document.check_permission('read')
        if document.employer != employer:
            frappe.throw(_('El cliente seleccionado pertenece a otra empresa.'))
        person = frappe._dict(document.as_dict())
        person.client = document.name
        identity = related._identity_values(person, [person])
        # Only aliases belonging to the readable selected client broaden the search.
        for alias in document.get('aliases') or []:
            value = clean_text(alias.alias_name)
            if value:
                identity['name_values'].add(value)
                identity['names'].add(related.name_key(value))
        company = employer
    if employer and company != employer:
        frappe.throw(_('La empresa cambió. Vuelva a seleccionar el cliente.'))
    if not company or not any(identity[field] for field in ('numbers', 'clients', 'national_ids', 'names')):
        frappe.throw(_('Identifique la empresa y el cliente antes de consultar sus pendientes.'))
    frappe.get_doc('CN Employer', company).check_permission('read')
    return company, identity


def _decorate(row):
    row = dict(row)
    prefix, doctype, route = {
        'Aplicación': ('application', 'CN Accounting Import', 'cn-accounting-import'),
        'Depósito': ('deposit', 'CN Remittance Allocation', 'cn-remittance-allocation'),
        'Partida complementaria': ('item', 'CN Complementary Item', 'cn-complementary-item'),
    }[row['type']]
    row.update(id=json.dumps([prefix, row['document'], row.get('row_name') or ''], ensure_ascii=False),
               doctype=doctype, route=route)
    return row


@frappe.whitelist()
def get_data(employer=None, client=None, context=None):
    company, identity = _scope(employer, client, context)
    data = related._related_pending(company, identity)
    for key in ('applications', 'rows'):
        data[key] = sorted((_decorate(row) for row in data[key]),
                           key=lambda row: (not bool(row['date']), row['date'], row.get('period') or '', row['document'], row['id']))
    return dict(data, employer=company, consulted_on=str(now_datetime()))


def _amount_result(application_pending, source_available, amount=None):
    """Keep unknown amounts unknown and never round an invalid request into a payment."""
    pending = money(application_pending) if application_pending is not None else None
    available = money(source_available) if source_available is not None else None
    limit = min(pending, available) if pending is not None and available is not None else None
    errors = []
    proposed = None
    if amount in (None, ''):
        proposed = max(limit, money(0)) if limit is not None else None
    else:
        try:
            exact = decimal_value(amount)
            if not exact.is_finite() or exact <= 0 or exact != money(exact):
                raise ValueError
            proposed = money(exact)
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            errors.append(_('Indique un importe positivo con hasta dos decimales.'))
    if limit is None:
        errors.append(_('No se puede determinar el importe disponible para este vínculo.'))
    elif limit <= 0:
        errors.append(_('La selección no tiene saldo disponible para un nuevo vínculo.'))
    if proposed is not None and pending is not None and proposed > pending:
        errors.append(_('El importe supera el pendiente disponible de la aplicación.'))
    if proposed is not None and available is not None and proposed > available:
        errors.append(_('El importe supera el disponible del depósito o de la partida.'))
    valid = not errors
    return dict(application_pending_usd=money_float(pending) if pending is not None else None,
                source_available_usd=money_float(available) if available is not None else None,
                proposed_usd=money_float(proposed) if proposed is not None else None,
                application_remaining_usd=money_float(pending - proposed) if valid else None,
                source_remaining_usd=money_float(available - proposed) if valid else None,
                errors=errors)


def _detail_available(deposit, detail, credits):
    manual = [row for row in deposit.targets if row.get('detail_row') == detail.name]
    matched = frappe.parse_json(detail.get('matched_targets') or '[]')
    if not isinstance(matched, list) or any(not isinstance(row, dict) for row in matched):
        frappe.throw(_('El detalle guardado es inválido. Revise la distribución del depósito.'))
    assignments = manual or [row for row in matched if not row.get('instruction_id')]
    used = sum((money(row.get('amount_usd')) for row in assignments), money(0))
    reserved = sum((money(row.amount_usd) for row in credits if row.credit_detail_row == detail.name), money(0))
    return max(money(detail.amount_usd) - used - reserved, money(0))


def _deposit_check(source, application, row, errors, warnings):
    from credinomina_reconciliation.remittance_selection import get_pending_targets
    from credinomina_reconciliation.remittance_periods import selected_periods
    from credinomina_reconciliation.client_credit import load_credits
    deposit = frappe.get_doc('CN Remittance Allocation', source['document'])
    deposit.check_permission('write')
    if deposit.docstatus != 1:
        errors.append(_('Confirme el depósito antes de aplicar su dinero.'))
        return None, None
    if not row.historical_period:
        errors.append(_('Asigne y concilie el período de la aplicación antes de seleccionarla como destino del depósito.'))
        return None, None
    period = frappe.get_doc('CN Reconciliation Period', row.historical_period)
    period.check_permission('read')
    if period.status == 'Cerrado':
        errors.append(_('El período de la aplicación está cerrado. Reábralo antes de cambiar su conciliación.'))
        return None, None
    if any(target.historical_application == row.name for target in deposit.targets):
        errors.append(_('La aplicación ya está en los destinos del depósito. Revise ese destino y use Conciliar.'))
        return None, None
    periods = set(selected_periods(deposit))
    if deposit.apply_fifo and deposit.detail_rows and not periods:
        errors.append(_('Seleccione al menos un período en Períodos a conciliar para aplicar FIFO.'))
    if source.get('row_name') and periods and row.historical_period not in periods and not deposit.allow_manual_other_periods:
        errors.append(_('El período de la aplicación está fuera de los períodos del detalle. Revise esa selección o habilite otros períodos en el depósito.'))
    result = get_pending_targets(deposit.name, detail_row_name=source.get('row_name') or None,
                                 selected_ids=[json.dumps(['H', row.name])])
    candidate = next((item for item in result['rows'] if item.get('historical_application') == row.name), None)
    if not candidate:
        errors.append(_('La aplicación no está disponible para este depósito: revise período, identidad, reservas y asignaciones existentes.'))
        return None, None
    available = money(Decimal(result['available_cents']) / 100)
    if source.get('row_name'):
        detail = next((detail for detail in deposit.detail_rows if detail.name == source['row_name']), None)
        if detail is None:
            errors.append(_('La fila del depósito cambió. Actualice la mesa.'))
            return None, None
        from credinomina_reconciliation.remittance_detail import _candidate_matches, _manual_application_identity_matches
        claim = {key: row.get(key) for key in ('client_name', 'client_number', 'employee_number',
            'national_id', 'loan_number', 'installment_number')}
        claim.update(kind='H', group=period.employer, client=row.client or row.portfolio_client or candidate.get('client'),
                     references=[clean_text(row.reference)])
        if candidate.get('client'):
            client_doc = frappe.get_doc('CN Client', candidate['client'])
            client_doc.check_permission('read')
            claim['client_names'] = [client_doc.client_name, *[alias.alias_name for alias in client_doc.get('aliases') or []]]
            for key in ('client_number', 'national_id', 'employee_number'):
                claim[key] = claim.get(key) or client_doc.get(key)
        detail_identity = detail.as_dict()
        detail_identity['client'] = detail.client or (result.get('detail_client') or {}).get('name')
        if not _candidate_matches(detail_identity, claim) and not _manual_application_identity_matches(detail_identity, claim, deposit.employer):
            errors.append(_('La aplicación no coincide con los identificadores, crédito, cuota o referencia de la fila del depósito. Revise esos datos antes de vincular.'))
        detail_available = _detail_available(deposit, detail, load_credits([deposit.name]))
        source['detail_available_usd'] = money_float(detail_available)
        available = min(available, detail_available)
    else:
        warnings.append(_('El cliente está identificado en el origen del depósito. Revise su detalle antes de asignar el importe.'))
    # Reserve instructions saved by every user, even before they are reconciled.
    from credinomina_reconciliation.application_adjustment_links import confirmed
    from credinomina_reconciliation.application_adjustments import cash_coverage, net_amount
    current = frappe._dict(row.as_dict())
    current.application_adjustment_usd = sum((money(item.application_adjustment_usd)
        for item in confirmed([row.name], employer=period.employer)), money(0))
    current.net_applied_usd = net_amount(current)
    pending = min(money(Decimal(candidate['pending_cents']) / 100), money(cash_coverage(current)['adjustable_usd']))
    return pending, available


def _item_check(source, application, row, parent, errors, warnings):
    from credinomina_reconciliation.accounting_review import application_candidates
    from credinomina_reconciliation.application_adjustments import application_link_error, adjustment_block_reason
    item = frappe.get_doc('CN Complementary Item', source['document'])
    item.check_permission('write')
    if item.docstatus == 1:
        item.check_permission('submit')
    else:
        from credinomina_reconciliation.application_adjustment_links import total
        if total(item) > 0:
            errors.append(_('La partida en borrador ya tiene una distribución entre aplicaciones. Revísela en la partida antes de proponer otro vínculo.'))
            return None, None
    reason = application_link_error(item, row, parent)
    reason = reason or adjustment_block_reason(row, item.name,
        frappe.parse_json(item.get('adjustment_periods') or '[]'),
        frappe.parse_json(item.get('adjustment_collection_rows') or '[]'))
    if reason:
        errors.append(reason)
        return None, None
    balance = load_balances([item])[item.name]
    if balance.get('amount_issue') or balance.get('financial_status') == 'Revisar distribución':
        errors.append(_('La partida tiene una inconsistencia de importe o distribución. Revísela antes de vincular.'))
        return None, None
    candidate = next((value for value in application_candidates(item.name, parent.name) if value['name'] == row.name), None)
    if not candidate:
        errors.append(_('La partida no puede vincularse a esta aplicación con su tratamiento actual, o la aplicación ya tiene su saldo cubierto o reservado.'))
        return None, None
    if item.docstatus == 0:
        warnings.append(_('La partida está en borrador. El ajuste tendrá efecto al confirmarla.'))
    return money(candidate['adjustable_usd']), max(money(balance['pending_usd']), money(0))


@frappe.whitelist()
def preview(application_id, source_id, amount=None, employer=None, client=None, context=None):
    # Resolve identities and permissions again; never trust amounts or rows from the browser.
    data = get_data(employer, client, context)
    application = next((row for row in data['applications'] if row['id'] == application_id), None)
    source = next((row for row in data['rows'] if row['id'] == source_id), None)
    if application is None or source is None:
        return dict(can_continue=False, errors=[_('La selección cambió o ya no está pendiente. Actualice la mesa.')],
                    warnings=[], checked_on=str(now_datetime()))
    parent = frappe.get_doc('CN Accounting Import', application['document'])
    parent.check_permission('read')
    row = next((row for row in parent.rows if row.name == application['row_name']), None)
    if row is None:
        frappe.throw(_('La aplicación ya no pertenece a esta importación. Actualice la mesa.'))
    errors, warnings = [], []
    if application.get('loan_number') and source.get('loan_number') and canonical_identifier(application['loan_number']) != canonical_identifier(source['loan_number']):
        warnings.append(_('Los números de crédito son distintos. Confirme que corresponde usar este movimiento para la aplicación seleccionada.'))
    if application.get('reference') and source.get('reference') and clean_text(application['reference']) != clean_text(source['reference']):
        warnings.append(_('Las referencias son diferentes. Revise el respaldo del vínculo.'))
    if application['date'] and source['date'] and application['date'][:7] != source['date'][:7]:
        warnings.append(_('Los movimientos pertenecen a meses distintos. Revise las fechas y el período que se cubrirá.'))
    pending = available = None
    # Validation errors from existing selectors are expected preflight findings.
    # Permission errors and unexpected failures must still abort the request.
    log = getattr(frappe.local, 'message_log', None)
    log_length = len(log) if log is not None else 0
    try:
        if source['type'] == 'Depósito':
            pending, available = _deposit_check(source, application, row, errors, warnings)
        else:
            pending, available = _item_check(source, application, row, parent, errors, warnings)
    except frappe.PermissionError:
        raise
    except frappe.ValidationError as exc:
        errors.append(str(exc))
        messages = getattr(frappe.local, 'message_log', None)
        if messages is not None:
            del messages[log_length:]
    amounts = _amount_result(pending, available, amount)
    errors.extend(amounts.pop('errors'))
    if errors:
        amounts['application_remaining_usd'] = None
        amounts['source_remaining_usd'] = None
    elif (source.get('detail_available_usd') is not None
            and money(source['detail_available_usd']) > money(amounts['proposed_usd'])):
        warnings.append(_('Esta selección cubre solo parte de la fila del depósito. Complete su distribución para que la fila pueda quedar conciliada.'))
    return dict(amounts, can_continue=not errors, errors=list(dict.fromkeys(errors)), warnings=warnings,
                checked_on=str(now_datetime()), application=application, source=source,
                next_step=(_('Abra el depósito y pulse Mesa de conciliación → Continuar selección de la mesa. Revise el destino propuesto, agréguelo, guarde y use Conciliar.')
                           if source['type'] == 'Depósito' else
                           _('Abra la partida y pulse Mesa de conciliación → Continuar selección de la mesa. Revise la aplicación y el importe propuestos antes de guardar o confirmar.')))
