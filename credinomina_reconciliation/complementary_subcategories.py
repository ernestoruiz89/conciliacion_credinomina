"""Explicit classification; never infer a receivable from the amount's sign alone."""
import frappe
from frappe import _

from credinomina_reconciliation.rounding import money

DOCTYPE = 'CN Complementary Subcategory'
ADJUSTMENT = 'Ajuste de conciliación'
RECEIVABLE = 'CxC a la empresa'
UNCLASSIFIED = 'Por clasificar'
OTHER = 'Sin CxC adicional'
DEFAULTS = ((RECEIVABLE, RECEIVABLE), (UNCLASSIFIED, UNCLASSIFIED), ('Otro ajuste sin CxC', OTHER))


def seed_subcategories():
    for name, effect in DEFAULTS:
        if not frappe.db.exists(DOCTYPE, name):
            frappe.get_doc(dict(doctype=DOCTYPE, subcategory_name=name, effect=effect)).insert(ignore_permissions=True)


def validate_subcategory(doc, previous=None):
    if previous and previous.get('docstatus') == 1 and previous.get('subcategory_effect') == RECEIVABLE:
        if doc.get('subcategory') != previous.get('subcategory') or doc.get('category') != previous.get('category'):
            frappe.throw(_('No retire la clasificación de CxC de una partida confirmada para cerrar su pendiente.'))
    if doc.get('category') != ADJUSTMENT:
        doc.subcategory = None
        doc.subcategory_effect = None
        return
    if not doc.get('subcategory'):
        frappe.throw(_('Seleccione una Subcategoría para el Ajuste de conciliación.'))
    effect = frappe.db.get_value(DOCTYPE, doc.subcategory, 'effect')
    if effect not in {RECEIVABLE, UNCLASSIFIED, OTHER}:
        frappe.throw(_('La subcategoría no existe o no tiene un tratamiento válido.'))
    doc.subcategory_effect = effect
    if effect == RECEIVABLE and (money(doc.get('amount_usd')) >= 0 or doc.get('accounting_source_key')):
        frappe.throw(_('CxC a la empresa se usa para un ajuste manual negativo que cubre un faltante del depósito; no duplique un movimiento del core.'))


def legacy_subcategory(item):
    # Only an explicit, exact description is backfilled as CxC. All other old
    # adjustments remain visible for review, including fully distributed ones.
    description = ' '.join((item.get('description') or '').casefold().split())
    if description == 'cxc a la empresa' and money(item.get('amount_usd')) < 0 and not item.get('accounting_source_key'):
        return RECEIVABLE, RECEIVABLE
    return UNCLASSIFIED, UNCLASSIFIED
