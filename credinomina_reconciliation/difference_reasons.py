"""Optional explanations for unresolved rows; these labels never move money."""

import frappe

from credinomina_reconciliation.rounding import decimal_value


DOCTYPE = "CN Difference Reason"
DEFAULTS = (
    ("Liquidación pendiente de cobro", "La aplicación o liquidación está registrada, pero falta acreditar el cobro."),
    ("Saldo a favor", "Posible excedente por verificar; esta etiqueta no documenta ni crea un saldo a favor."),
    ("Cuota no deducida", "La cuota no figura entre las deducciones recibidas."),
    ("Parcialmente deducida", "La deducción informada es menor que el importe esperado."),
    ("Diferencia de importe", "Los importes de las fuentes no coinciden; falta revisar el soporte."),
    ("Otro por aclarar", "Diferencia pendiente de identificar con su soporte."),
)


def seed_reasons():
    for name, description in DEFAULTS:
        if not frappe.db.exists(DOCTYPE, name):
            frappe.get_doc({"doctype": DOCTYPE, "reason_name": name,
                            "description": description}).insert(ignore_permissions=True)


def only_detail_reasons_changed(document, previous):
    """Allow classification of a submitted deposit without reopening closed cash."""
    if not previous or previous.docstatus != 1 or document.docstatus != 1:
        return False

    numeric = {"Currency", "Float", "Percent", "Int", "Check"}

    def same_value(field, current, saved):
        if field.fieldtype in numeric:
            return decimal_value(current) == decimal_value(saved)
        return str(current or "") == str(saved or "")

    changed = False
    for field in document.meta.fields:
        name = field.fieldname
        if name in {"difference_reason", "difference_notes"}:
            changed |= not same_value(field, document.get(name), previous.get(name))
            continue
        if field.fieldtype != "Table":
            if not same_value(field, document.get(name), previous.get(name)):
                return False
            continue
        current_rows, saved_rows = document.get(name) or [], previous.get(name) or []
        if len(current_rows) != len(saved_rows):
            return False
        child_fields = frappe.get_meta(field.options).fields
        for current, saved in zip(current_rows, saved_rows):
            if current.name != saved.name or current.idx != saved.idx:
                return False
            for child_field in child_fields:
                if name == "detail_rows" and child_field.fieldname in {"difference_reason", "difference_notes"}:
                    changed |= not same_value(child_field, current.get(child_field.fieldname), saved.get(child_field.fieldname))
                elif not same_value(child_field, current.get(child_field.fieldname), saved.get(child_field.fieldname)):
                    return False
    return changed
