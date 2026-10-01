"""Accounting imports derive their identity from their movement dates."""

import re

import frappe
from frappe import _
from frappe.model.naming import getseries, make_autoname, validate_name
from frappe.utils import getdate


DOCTYPE = "CN Accounting Import"


def accounting_month(rows):
    dates = [getdate(row.get("event_date")) for row in rows if row.get("event_date")]
    # A file can span several months. Its first movement determines the name.
    return min(dates).replace(day=1) if dates else None


def accounting_prefix(employer, month):
    if not employer or not month:
        return None
    code = str(frappe.db.get_value("CN Employer", employer, "employer_code") or "").strip()
    if not code:
        frappe.throw(_("La empresa {0} debe tener código para nombrar la importación contable.").format(employer))
    prefix = f"CONTA-{code}-{month.month}-{month.year}-"
    if len(prefix) + 3 > 140:
        frappe.throw(_("El código de empresa es demasiado largo para nombrar la importación contable."))
    validate_name(DOCTYPE, prefix + "001")
    return prefix


def matches_accounting_prefix(name, prefix):
    return bool(prefix and re.fullmatch(re.escape(prefix) + r"\d{3,}", name or ""))


def new_accounting_name(prefix):
    if not prefix:
        return make_autoname("CONTA-BORRADOR-.YYYY.-.#####")
    # getseries serializes simultaneous imports for the same company/month.
    while True:
        candidate = prefix + getseries(prefix, 3)
        if not frappe.db.exists(DOCTYPE, candidate):
            return candidate


def rename_accounting_import(document):
    month = accounting_month(document.get("rows") or [])
    prefix = accounting_prefix(document.get("employer"), month)
    if not prefix or matches_accounting_prefix(document.name, prefix):
        return document.name
    old_name = document.name
    new_name = new_accounting_name(prefix)
    renamed = frappe.rename_doc(
        DOCTYPE, old_name, new_name, force=True, merge=False,
        show_alert=False, rebuild_search=False,
    )
    document.name = renamed
    document.localname = old_name
    for row in document.get_all_children():
        row.parent = renamed
    return renamed
