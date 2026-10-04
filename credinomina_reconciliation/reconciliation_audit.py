"""Append reconciliation changes to Frappe's existing Version history.

No financial document is introduced. Versions are inserted in the same database
transaction as the reconciliation: a failed operation leaves neither half behind.
This module never updates or deletes earlier evidence.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json

import frappe
from frappe import _
from frappe.utils import cint
from credinomina_reconciliation.rounding import money_float

FIELDS = ("amount_usd", "allocated_usd", "unallocated_usd", "justified_surplus_usd",
          "unclassified_usd", "result", "detail_status", "allocation_detail")
_context = ContextVar("cn_reconciliation_audit", default=None)


@contextmanager
def audit_reason(action, reason=""):
    token = _context.set({"action": action, "reason": str(reason or "").strip()[:2000]})
    try:
        yield
    finally:
        _context.reset(token)


def snapshot(document):
    return {field: money_float(document.get(field)) if field.endswith("_usd")
            else document.get(field) or ("[]" if field == "allocation_detail" else "")
            for field in FIELDS}


def _normalized(field, value):
    if field == "allocation_detail":
        try:
            return json.loads(value or "[]")
        except (ValueError, TypeError):
            return value  # Preserve invalid old evidence rather than erase it.
    return value


def record_transition(name, before, after):
    changed = [[field, before.get(field), after.get(field)] for field in FIELDS
               if _normalized(field, before.get(field)) != _normalized(field, after.get(field))]
    if not changed:
        return None
    context = _context.get() or {"action": "Actualización de conciliación", "reason": ""}
    command = (getattr(frappe.local, "form_dict", None) or {}).get("cmd", "")
    periods = set()
    for state in (before, after):
        entries = _normalized("allocation_detail", state.get("allocation_detail"))
        if isinstance(entries, list):
            periods.update(entry.get("periodo") for entry in entries if isinstance(entry, dict))
    data = {"changed": changed, "added": [], "removed": [], "row_changed": [],
            "cn_reconciliation": {**context, "command": command, "deposit": name,
                                  "periods": sorted(periods - {None, ""}),
                                  "before": before, "after": after}}
    version = frappe.get_doc({"doctype": "Version", "ref_doctype": "CN Remittance Allocation",
                              "docname": name, "data": json.dumps(data, ensure_ascii=False)})
    version.insert(ignore_permissions=True)
    return version.name


@frappe.whitelist()
def get_history(deposit_name, start=0):
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
    deposit.check_permission("read")
    start = max(cint(start), 0)
    records = frappe.get_all("Version", filters={"ref_doctype": deposit.doctype, "docname": deposit.name,
        "data": ["like", '%"cn_reconciliation":%']}, fields=["name", "owner", "creation", "data"],
        order_by="creation desc, name desc", limit_start=start, limit_page_length=21)
    output = []
    for row in records[:20]:
        data = json.loads(row.data)
        output.append({"name": row.name, "user": row.owner, "date": str(row.creation), **data["cn_reconciliation"]})
    return {"rows": output, "has_more": len(records) > 20, "next_start": start + 20,
            "notice": _("Historial registrado desde esta mejora. Las distribuciones anteriores no se reconstruyen retroactivamente.")}
