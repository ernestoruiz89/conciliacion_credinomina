"""Explicit classification; never infer a receivable from the amount's sign alone."""
import frappe
from frappe import _

from credinomina_reconciliation.rounding import money

DOCTYPE = 'CN Complementary Subcategory'
ADJUSTMENT = 'Ajuste de conciliación'
RECEIVABLE = 'CxC a la empresa'
EMPLOYEE_RECEIVABLE = 'CxC al Empleado'
RECEIVABLE_EFFECTS = (RECEIVABLE, EMPLOYEE_RECEIVABLE)
UNCLASSIFIED = 'Por clasificar'
OTHER = 'Sin CxC adicional'
DEFAULTS = ((RECEIVABLE, RECEIVABLE), (EMPLOYEE_RECEIVABLE, EMPLOYEE_RECEIVABLE),
            (UNCLASSIFIED, UNCLASSIFIED), ('Otro ajuste sin CxC', OTHER))


def seed_subcategories():
    for name, effect in DEFAULTS:
        if not frappe.db.exists(DOCTYPE, name):
            frappe.get_doc(dict(doctype=DOCTYPE, subcategory_name=name, effect=effect)).insert(ignore_permissions=True)


def validate_subcategory(doc, previous=None):
    if previous and previous.get('docstatus') == 1 and previous.get('subcategory_effect') in RECEIVABLE_EFFECTS:
        if doc.get('subcategory') != previous.get('subcategory') or doc.get('category') != previous.get('category'):
            frappe.throw(_('No retire la clasificación de CxC de una partida confirmada para cerrar su pendiente.'))
    if doc.get('category') != ADJUSTMENT:
        doc.subcategory = None
        doc.subcategory_effect = None
        return
    if not doc.get('subcategory'):
        frappe.throw(_('Seleccione una Subcategoría para el Ajuste de conciliación.'))
    effect = frappe.db.get_value(DOCTYPE, doc.subcategory, 'effect')
    if effect not in {*RECEIVABLE_EFFECTS, UNCLASSIFIED, OTHER}:
        frappe.throw(_('La subcategoría no existe o no tiene un tratamiento válido.'))
    doc.subcategory_effect = effect
    if effect in RECEIVABLE_EFFECTS and (money(doc.get('amount_usd')) >= 0 or doc.get('accounting_source_key')):
        frappe.throw(_('La CxC se usa para un ajuste manual negativo que cubre un faltante del depósito; no duplique un movimiento del core.'))
    if effect == EMPLOYEE_RECEIVABLE:
        validate_employee(doc, previous)


def validate_employee(doc, previous=None):
    """Keep an individual debt attached to its verified debtor after confirmation."""
    from credinomina_reconciliation.parsers import clean_text, normalize_credit_number
    from credinomina_reconciliation.rounding import decimal_value
    if doc.get('generic_distribution'):
        frappe.throw(_('La CxC al Empleado debe identificar un cliente; no admite distribución genérica.'))
    if previous and previous.get('docstatus') == 1 and previous.get('subcategory_effect') == EMPLOYEE_RECEIVABLE:
        for field in ('credit_client', 'client_number', 'loan_number', 'employer', 'period',
                      'posting_date', 'amount', 'currency', 'fx_rate'):
            same = (decimal_value(doc.get(field)) == decimal_value(previous.get(field)) if field in {'amount', 'fx_rate'}
                    else str(doc.get(field) or '') == str(previous.get(field) or ''))
            if not same:
                frappe.throw(_('La CxC al Empleado confirmada conserva su cliente, crédito e importe. Cancele primero la partida para corregir su origen.'))
        return
    if not doc.get('credit_client') and doc.get('client_number'):
        doc.credit_client = frappe.db.get_value('CN Client', {'client_number': doc.client_number}, 'name')
    if not doc.get('credit_client') or not doc.get('employer'):
        frappe.throw(_('Seleccione el cliente y la empresa de la CxC al Empleado.'))
    client = frappe.get_doc('CN Client', doc.credit_client)
    client.check_permission('read')
    if not client.client_number or (doc.get('client_number') and clean_text(doc.client_number) != clean_text(client.client_number)):
        frappe.throw(_('El número de cliente no coincide con el cliente seleccionado.'))
    from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
    from credinomina_reconciliation.paying_employers import choose_detail_client
    evidence = dict(client_number=client.client_number, employer=doc.employer, loan_number=doc.get('loan_number'))
    clients = [client.as_dict()]
    loans = load_detail_loan_clients([evidence], clients, {doc.employer})
    beneficiary, reason = choose_detail_client(evidence, clients, doc.employer, {doc.employer}, loans)
    if not beneficiary or beneficiary['name'] != client.name:
        frappe.throw(_('No se pudo verificar el cliente y su empresa para la CxC: {0}').format(reason))
    doc.client_number, doc.client_name = client.client_number, client.client_name
    doc.loan_number = normalize_credit_number(doc.get('loan_number'))


def legacy_subcategory(item):
    # Only an explicit, exact description is backfilled as CxC. All other old
    # adjustments remain visible for review, including fully distributed ones.
    description = ' '.join((item.get('description') or '').casefold().split())
    if description == 'cxc a la empresa' and money(item.get('amount_usd')) < 0 and not item.get('accounting_source_key'):
        return RECEIVABLE, RECEIVABLE
    return UNCLASSIFIED, UNCLASSIFIED
