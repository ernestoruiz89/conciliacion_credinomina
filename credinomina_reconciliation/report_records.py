"""Complete, bounded database reads for financial reports.

Parent reads respect Frappe permissions. Child reads are permitted only for an
explicit set of readable parents, with both parenttype and parentfield fixed.
Pagination limits query size, never the reported population or its totals.
"""
import frappe

PAGE_SIZE = 500


def records(doctype, *, fields, filters=None, order_by="name asc", **kwargs):
    yield from _pages(frappe.get_list, doctype, fields=fields, filters=filters,
                      order_by=order_by, **kwargs)


def _pages(query, doctype, *, fields, filters=None, order_by="name asc", **kwargs):
    # A unique tiebreaker is essential when many records share a date or company.
    if "name" not in order_by.lower().replace("`", "").split(",")[-1].split():
        order_by += ", name asc"
    offset = 0
    while True:
        batch = query(doctype, fields=fields, filters=filters or {}, order_by=order_by,
                      limit_start=offset, limit_page_length=PAGE_SIZE, **kwargs)
        yield from batch
        if len(batch) < PAGE_SIZE:
            return
        offset += len(batch)


def child_records(doctype, readable_parents, parenttype, parentfield, *, fields,
                  filters=None, order_by="parent asc, idx asc"):
    names = sorted(set(readable_parents))
    for offset in range(0, len(names), PAGE_SIZE):
        scope = {**(filters or {}), "parent": ["in", names[offset:offset + PAGE_SIZE]],
                 "parenttype": parenttype, "parentfield": parentfield}
        yield from _pages(frappe.get_all, doctype, fields=fields, filters=scope, order_by=order_by)
