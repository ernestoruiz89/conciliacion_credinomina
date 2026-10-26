"""Filterable reconciliation state, refreshed atomically with financial writes."""
import json

import frappe

from credinomina_reconciliation.complementary_balances import (
    CASH_CATEGORIES, FIELDS, _distribution_index, financial_balance, reconciliation_state,
)

DOCTYPE = "CN Complementary Item"
FIELD = "reconciliation_status"
STATES = ("Pendiente", "Parcial", "Conciliado", "Revisar distribución", "Vinculado a depósito",
          "Revertida", "Parcialmente revertida", "Cancelada", "Saldo no disponible")


def state(item, distributions=()):
    if item.get("docstatus") == 2:
        return "Cancelada"
    if item.get("status") == "Revertido":
        return "Revertida"
    if item.get("origin_deposit"):
        return "Vinculado a depósito"
    return reconciliation_state(financial_balance(item, distributions))


def _reset():
    frappe.flags.cn_complementary_states = None
    frappe.flags.cn_complementary_states_scheduled = False
    frappe.flags.cn_complementary_states_cleanup = False


def mark(names=(), deposits=()):
    """Accumulate changes, including direct SQL writers, until just before commit."""
    names = {names} if isinstance(names, str) else set(names)
    deposits = {deposits} if isinstance(deposits, str) else set(deposits)
    names.discard(None); names.discard("")
    deposits.discard(None); deposits.discard("")
    if not names and not deposits:
        return
    pending = frappe.flags.cn_complementary_states
    if pending is None:
        pending = dict(names=set(), deposits=set())
        frappe.flags.cn_complementary_states = pending
    pending["names"].update(names)
    pending["deposits"].update(deposits)
    if not frappe.flags.cn_complementary_states_cleanup:
        frappe.db.after_commit.add(_reset)
        frappe.db.after_rollback.add(_reset)
        frappe.flags.cn_complementary_states_cleanup = True
    if not frappe.flags.cn_complementary_states_scheduled:
        frappe.db.before_commit.add(flush)
        frappe.flags.cn_complementary_states_scheduled = True


def item_changed(doc, method=None):
    mark([doc.name])


def deposit_changed(doc, method=None, previous=None):
    if previous is None and callable(getattr(doc, "get_doc_before_save", None)):
        previous = doc.get_doc_before_save()
    names = set()
    for document in (doc, previous):
        if not document:
            continue
        content = document.get("allocation_detail") or "[]"
        try:
            entries = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            entries = []
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("tipo") == "Partida complementaria" and entry.get("partida"):
                names.add(entry["partida"])
    mark(names, deposits=[doc.get("name")])


def flush():
    pending = frappe.flags.cn_complementary_states
    frappe.flags.cn_complementary_states = None
    frappe.flags.cn_complementary_states_scheduled = False
    if not pending or not frappe.db.has_column(DOCTYPE, FIELD):
        return
    names = pending["names"]
    deposits = sorted(pending["deposits"])
    for offset in range(0, len(deposits), 500):
        names.update(frappe.get_all(DOCTYPE, filters={"registered_deposit": ["in", deposits[offset:offset + 500]]},
                                    pluck="name", limit_page_length=0))
    ordered = sorted(names)
    for offset in range(0, len(ordered), 500):
        items = frappe.get_all(DOCTYPE, filters={"name": ["in", ordered[offset:offset + 500]]},
            fields=[*FIELDS, FIELD], limit_page_length=0)
        refresh(items)


def refresh(items):
    """Update only the derived status; preserve amounts, histories and timestamps."""
    items = list(items)
    wanted = {item["name"] for item in items if item.get("category") in CASH_CATEGORIES
              and item.get("docstatus") != 2 and item.get("status") != "Revertido"
              and not item.get("origin_deposit") and not item.get("registration_exception")}
    unavailable = set()
    try:
        distributions = _distribution_index(wanted, authoritative=True) if wanted else {}
    except (ValueError, TypeError, frappe.ValidationError):
        # Isolate bad evidence so one malformed journal does not make every
        # other item in a migration batch unavailable or abort a financial write.
        distributions = {}
        for name in sorted(wanted):
            try:
                distributions.update(_distribution_index({name}, authoritative=True))
            except (ValueError, TypeError, frappe.ValidationError):
                unavailable.add(name)
    changed = 0
    for item in items:
        try:
            value = "Saldo no disponible" if item["name"] in unavailable else state(item, distributions.get(item["name"], ()))
        except (ValueError, TypeError, frappe.ValidationError):
            value = "Saldo no disponible"
        if item.get(FIELD) != value:
            frappe.db.set_value(DOCTYPE, item["name"], FIELD, value, update_modified=False)
            frappe.clear_document_cache(DOCTYPE, item["name"])
            changed += 1
    return changed
