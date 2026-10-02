"""Period names use the short name (or code) and the collection month."""

import frappe
from frappe import _
from frappe.model.naming import getseries, validate_name
from frappe.utils import getdate

from credinomina_reconciliation.employer_naming import employer_document_prefix


DOCTYPE = "CN Reconciliation Period"


def period_name_prefix(employer, payroll_month):
    if not employer or not payroll_month:
        frappe.throw(_("Indique la empresa y el mes de cobranza para nombrar el período."))
    code = employer_document_prefix(employer)
    if not code:
        frappe.throw(_("La empresa {0} debe tener nombre corto o código antes de crear el período.").format(employer))
    month = getdate(payroll_month)
    prefix = f"{code}-{month.month}-{month.year}-"
    if len(prefix) + 2 > 140:
        frappe.throw(_("El nombre corto o código de empresa es demasiado largo para nombrar el período."))
    validate_name(DOCTYPE, prefix + "01")
    return prefix


def new_period_name(employer, payroll_month):
    prefix = period_name_prefix(employer, payroll_month)
    # getseries locks the counter inside the current transaction. Pass the
    # literal prefix so dots or naming tokens in the short name/code stay literal.
    while True:
        name = prefix + getseries(prefix, 2)
        if not frappe.db.exists(DOCTYPE, name):
            return name


def rename_period_for_context_change(document):
    previous = document.get_doc_before_save()
    if not previous:
        return
    old_month = getattr(previous, "payroll_month", None)
    new_month = getattr(document, "payroll_month", None)
    employer = getattr(document, "employer", None)
    employer_changed = employer != getattr(previous, "employer", None)
    month_changed = (
        bool(old_month) != bool(new_month)
        or (old_month and new_month and
            getdate(old_month).replace(day=1) != getdate(new_month).replace(day=1))
    )
    if not employer_changed and not month_changed:
        return
    old_name = document.name
    new_name = new_period_name(employer, new_month)
    # Frappe moves Link fields, child parents, attachments and version history.
    # Do not commit here: renaming must roll back if the save fails.
    renamed = frappe.rename_doc(
        DOCTYPE, old_name, new_name, force=True, merge=False,
        show_alert=False, rebuild_search=False,
    )
    document.name = renamed
    document.localname = old_name  # Update the open form's route after saving.
    for child in document.get_all_children():
        child.parent = renamed
