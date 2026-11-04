"""Capacity shared by cash assignments and offsets of imported remainders."""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.rounding import money, sum_money

CATEGORIES = {"Ajuste de aplicación", "Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"}


def is_remainder(item):
    return (item.get("docstatus") == 1 and item.get("category") in CATEGORIES
            and bool(item.get("accounting_source_key")))


def cash_capacity(item):
    """Signed claim remaining for deposits, including already assigned cash."""
    amount = money(item.get("amount_usd"))
    offsets = money(item.get("compensated_usd"))
    if offsets < 0 or offsets > abs(amount):
        frappe.throw(_("El saldo compensado de {0} no es válido. Revise el historial.").format(item.get("name")))
    return (abs(amount) - offsets) * (-1 if amount < 0 else 1)


def used_elsewhere(item, as_of_date=None, *, reserve=False):
    """Read complete journals; a user's hidden deposits still consume capacity."""
    if not is_remainder(item):
        return money(0)
    if item.get("category") == "Ajuste de aplicación":
        from credinomina_reconciliation.application_adjustment_links import total
        return total(item) if not as_of_date or getdate(item.posting_date) <= getdate(as_of_date) else money(0)
    from credinomina_reconciliation.complementary_balances import _distribution_index
    paid = defaultdict(lambda: money(0))
    sign = -1 if money(item.amount_usd) < 0 else 1
    for row in _distribution_index({item.name}, authoritative=True).get(item.name, []):
        if not as_of_date or getdate(row["date"]) <= getdate(as_of_date):
            paid[row["deposit"]] += money(row["amount_usd"]) * sign
    if any(amount < 0 for amount in paid.values()):
        frappe.throw(_("Revise la distribución de la partida {0} antes de compensar.").format(item.name))
    if reserve:
        planned = defaultdict(lambda: money(0))
        for row in frappe.get_all("CN Remittance Target", filters={"complementary_item": item.name,
                "parenttype": "CN Remittance Allocation", "docstatus": ["<", 2]},
                fields=["parent", "amount_usd"], limit_page_length=0):
            planned[row.parent] += abs(money(row.amount_usd))
        return sum_money(max(paid[name], planned[name]) for name in paid.keys() | planned.keys())
    return sum_money(paid.values())
