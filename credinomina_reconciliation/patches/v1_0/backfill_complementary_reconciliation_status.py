"""Populate filterable item states without replaying financial operations."""
import frappe

from credinomina_reconciliation.complementary_balances import FIELDS
from credinomina_reconciliation.complementary_state import DOCTYPE, CACHE_FIELDS, refresh


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_complementary_item")
    last = ""
    while True:
        items = frappe.get_all(DOCTYPE, filters={"name": [">", last]}, fields=[*FIELDS, *CACHE_FIELDS],
            order_by="name asc", limit_page_length=500)
        if not items:
            return
        refresh(items)
        last = items[-1].name
