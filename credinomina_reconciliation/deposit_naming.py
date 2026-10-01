"""Deposit identifiers are assigned from the deposit month, not today's date."""

import re

import frappe
from frappe import _
from frappe.model.naming import getseries
from frappe.utils import getdate

DOCTYPE = "CN Remittance Allocation"


def deposit_prefix(deposit_date):
    if not deposit_date:
        frappe.throw(_("Indique la fecha real del depósito antes de guardarlo."))
    date = getdate(deposit_date)
    return f"DEP-{date.month}-{date.year}-"


def new_deposit_name(deposit_date):
    prefix = deposit_prefix(deposit_date)
    while True:
        name = prefix + getseries(prefix, 4)
        if not frappe.db.exists(DOCTYPE, name):
            return name


def uses_deposit_series(name):
    return bool(re.fullmatch(r"DEP-(?:[1-9]|1[0-2])-\d{4}-\d{4,}", name or ""))
