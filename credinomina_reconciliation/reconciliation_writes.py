"""Persist already calculated reconciliation results in the caller's transaction."""
from functools import partial

import frappe


def update_child_results(doctype, updates_by_name):
    """Update changed fields on existing rows, retaining their audit timestamps."""
    if not updates_by_name:
        return
    bulk_update = getattr(frappe.db, "bulk_update", None)
    if len(updates_by_name) < 4 or not bulk_update:
        for name, values in updates_by_name.items():
            frappe.db.set_value(doctype, name, values, update_modified=False)
        return

    # Very long per-client journals can already occupy a packet on their own.
    # Keep them out of multi-row statements, even for otherwise ordinary deposits.
    batch = {}
    for name, values in updates_by_name.items():
        if sum(len(value) for value in values.values() if isinstance(value, str)) > 8192:
            frappe.db.set_value(doctype, name, values, update_modified=False)
        else:
            batch[name] = values
    if not batch:
        return

    # bulk_update skips the named document/value cache invalidation of set_value.
    # Batch those exact Redis keys, including the invalidation at transaction end.
    frappe.db.value_cache.pop(doctype, None)
    keys = [frappe.get_document_cache_key(doctype, name) for name in batch]
    invalidate = partial(frappe.cache.delete_value, keys)
    invalidate()
    frappe.db.after_commit.add(invalidate)
    frappe.db.after_rollback.add(invalidate)
    bulk_update(doctype, batch, chunk_size=50, update_modified=False)
