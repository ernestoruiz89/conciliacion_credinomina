"""Optional explanations for unresolved rows; these labels never move money."""

import frappe

from credinomina_reconciliation.rounding import decimal_value


DOCTYPE = "CN Difference Reason"
CREDIT_COMPONENTS = {"core_pending_usd", "deposit_pending_usd", "client_credit_usd", "company_credit_usd"}
DEFAULTS = (
    ("Liquidación pendiente de cobro", "La aplicación o liquidación está registrada, pero falta acreditar el cobro."),
    ("Saldo a favor", "Posible excedente por verificar; esta etiqueta no documenta ni crea un saldo a favor."),
    ("Cuota no deducida", "La cuota no figura entre las deducciones recibidas."),
    ("Parcialmente deducida", "La deducción informada es menor que el importe esperado."),
    ("Diferencia de importe", "Los importes de las fuentes no coinciden; falta revisar el soporte."),
    ("Otro por aclarar", "Diferencia pendiente de identificar con su soporte."),
)


def attach_treatments(rows, *, classify_credits=False):
    """Classify report rows from the readable catalog in one batched lookup.

    Grouped applications can carry several reason labels. Preserve each
    classification without inventing an allocation of the grouped amount.
    """
    from credinomina_reconciliation.report_records import records
    from credinomina_reconciliation.rounding import money, money_float

    if not any(row.get("difference_reason") or (row.get('_deposit_detail_classification') or {}).get('rows') for row in rows):
        return
    treatments = {reason["name"]: reason.get("treatment") or ""
                  for reason in records(DOCTYPE, fields=["name", "treatment"])}
    if classify_credits:
        from credinomina_reconciliation.deposit_detail_classification import expand
        rows[:] = expand(rows, treatments)
    order = ("A Favor Cliente", "A Favor Empresa")
    for row in rows:
        reason = row.get("difference_reason") or ""
        if not reason:
            continue
        names = [reason] if reason in treatments else reason.split(" · ")
        selected = {treatments.get(name) for name in names}
        row["difference_treatment"] = " · ".join(value for value in order if value in selected)
        # Classification changes the report bucket, never the signed balance.
        # Mixed/unclassified grouped reasons cannot classify the entire amount.
        field = {"A Favor Cliente": "client_credit_usd", "A Favor Empresa": "company_credit_usd"}.get(
            next(iter(selected)) if len(selected) == 1 else None)
        amount, previous = row.get("management_amount_usd"), row.get("_component")
        if (classify_credits and field and previous in CREDIT_COMPONENTS and row.get("statement_scope") == "Gestión"
                and amount is not None and money(amount) < 0 and previous != field):
            row[previous] = 0.0
            row[field] = money_float(amount)
            row["_component"] = field


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
