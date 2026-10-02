"""Repair display states/row coverage without rerunning allocations or closing periods."""
import json
from collections import defaultdict

import frappe

from credinomina_reconciliation.detail_balances import update_detail_balances
from credinomina_reconciliation.historical import historical_status
from credinomina_reconciliation.rounding import money, money_float


def _batches(doctype, fields):
    last = ""
    while True:
        rows = frappe.get_all(doctype, filters={"name": [">", last]}, fields=fields,
                              order_by="name asc", limit_page_length=200)
        if not rows:
            return
        yield rows
        last = rows[-1].name


def execute():
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _operative_period_status

    pending = defaultdict(lambda: money(0))
    for batch in _batches("CN Remittance Allocation", ["name", "docstatus", "unclassified_usd", "allocation_detail"]):
        for deposit in batch:
            # Per-deposit processing avoids loading every child row into memory.
            rows = frappe.get_all("CN Remittance Detail", filters={"parent": deposit.name},
                                  fields=["name", "amount_usd", "matched_targets"], limit_page_length=0)
            targets = frappe.get_all("CN Remittance Target", filters={"parent": deposit.name},
                                     fields=["detail_row", "amount_usd"], limit_page_length=0) if rows else []
            update_detail_balances({"detail_rows": rows, "targets": targets})
            for row in rows:
                frappe.db.set_value("CN Remittance Detail", row.name,
                                    {"linked_usd": row.linked_usd, "pending_usd": row.pending_usd}, update_modified=False)
            if deposit.docstatus != 1 or money(deposit.unclassified_usd) <= 0:
                continue
            entries = json.loads(deposit.allocation_detail or "[]")
            for period in {entry.get("periodo") for entry in entries
                           if entry.get("tipo") in {"Cobranza", "Aplicacion historica"} and entry.get("periodo")}:
                pending[period] += money(deposit.unclassified_usd)
    for batch in _batches("CN Reconciliation Period", ["name", "status", "reconciliation_mode", "applied_usd", "remitted_usd", "rounding_adjustment_usd"]):
        for period in batch:
            values = {"unassigned_deposit_usd": money_float(pending[period.name])}
            if period.status == "Con excedente":
                values["status"] = (
                    historical_status(money(period.applied_usd) + money(period.rounding_adjustment_usd), period.remitted_usd)
                    if period.reconciliation_mode == "Historica"
                    else _operative_period_status(frappe.get_doc("CN Reconciliation Period", period.name))
                )
            # Closed statuses and financial balances are intentionally untouched.
            frappe.db.set_value("CN Reconciliation Period", period.name, values, update_modified=False)
