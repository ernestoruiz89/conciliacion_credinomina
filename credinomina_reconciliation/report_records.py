"""Complete, bounded database reads for financial reports.

Parent reads respect Frappe permissions. Child reads are permitted only for an
explicit set of readable parents, with both parenttype and parentfield fixed.
Pagination limits query size, never the reported population or its totals.
"""
import frappe

PAGE_SIZE = 500

INCOMPLETE_EVIDENCE = (
    "Saldo no determinable: evidencia restringida. No tiene acceso a todos los "
    "documentos necesarios para comprobar este saldo. Solicite revisión a un supervisor."
)


def require_complete_evidence(doctype, readable_names, *, filters, fields=(), relevant=None, **kwargs):
    """Fail closed when a financial dependency is hidden by record permissions.

    This internal completeness check never returns hidden names, amounts or
    documents. A visible origin is not proof that all of its uses are visible.
    The optional predicate narrows broad journal scans to that visible origin.
    """
    names = set(readable_names)
    # Most users running a report can read the entire queried population. Scan
    # only identities first; large journals are needed solely for hidden rows.
    # Keep the second read bounded and internal: hidden evidence never escapes.
    hidden = []
    for row in _pages(frappe.get_all, doctype, fields=["name"],
                      filters=filters, order_by="name asc", **kwargs):
        if row.name in names:
            continue
        if relevant is None:
            frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)
        hidden.append(row.name)
    for offset in range(0, len(hidden), PAGE_SIZE):
        batch = hidden[offset:offset + PAGE_SIZE]
        scope = ({**filters, "name": ["in", batch]} if isinstance(filters, dict)
                 else [*(filters or []), ["name", "in", batch]])
        for row in _pages(frappe.get_all, doctype, fields=list(dict.fromkeys(["name", *fields])),
                          filters=scope, order_by="name asc", **kwargs):
            if relevant(row):
                frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)


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
