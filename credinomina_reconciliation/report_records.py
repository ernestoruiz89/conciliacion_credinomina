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


def records(doctype, *, fields, filters=None, order_by="name asc", journal_deposits=None, **kwargs):
    # The split journal is a restricted field. Its evidence is read internally
    # only after checking both the origin and every referenced destination.
    journal = "reclassification_allocations"
    if doctype != "CN Complementary Item" or journal not in fields:
        yield from _pages(frappe.get_list, doctype, fields=fields, filters=filters,
                          order_by=order_by, **kwargs)
        return
    public_fields = [field for field in fields if field != journal]
    if "name" not in public_fields:
        public_fields.append("name")
    scope = dict(filters or {})
    journal_filter = scope.pop(journal, None)
    if journal_filter and journal_filter != ["not in", ["", "[]"]]:
        raise ValueError("Unsupported restricted-journal filter")
    batch = []
    for row in _pages(frappe.get_list, doctype, fields=public_fields, filters=scope,
                      order_by=order_by, **kwargs):
        batch.append(frappe._dict(row))
        if len(batch) == PAGE_SIZE:
            yield from _hydrate_reclassification_journals(batch, journal_filter, journal_deposits)
            batch = []
    if batch:
        yield from _hydrate_reclassification_journals(batch, journal_filter, journal_deposits)


def _hydrate_reclassification_journals(batch, journal_filter, journal_deposits):
    journal = "reclassification_allocations"
    candidates = [row.name for row in batch if not row.get("category") or row.category == "Compensación entre partidas"]
    evidence = {row.get('name'): row.get(journal) for row in frappe.get_all("CN Complementary Item",
        filters={"name": ["in", candidates]}, fields=["name", journal], limit_page_length=0)} if candidates else {}
    for row in batch:
        raw = evidence.get(row.name)
        if journal_filter and (not raw or raw == "[]"):
            continue
        row[journal] = raw
        if raw and raw != "[]":
            if journal_deposits is not None:
                from credinomina_reconciliation.reclassification_distribution import DEPOSIT_KINDS, allocations
                selected = [entry for entry in allocations(row)
                            if entry["kind"] in DEPOSIT_KINDS and entry["parent_document"] in journal_deposits]
                if not selected:
                    continue
                row[journal] = selected
            _require_reclassification_targets(row)
        yield row


def _require_reclassification_targets(item):
    """Never reveal a target of a readable source without target permission."""
    from credinomina_reconciliation.reclassification_distribution import APPLICATION, allocations
    for entry in allocations(item):
        doctype = "CN Accounting Import" if entry["kind"] == APPLICATION else "CN Remittance Allocation"
        if not frappe.has_permission(doctype, "read", doc=entry["parent_document"]):
            frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)
        if entry["kind"] == APPLICATION and frappe.db.get_value("CN Source Row", entry["target"], "parent") != entry["parent_document"]:
            frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)
        if entry.get("client") and not frappe.has_permission("CN Client", "read", doc=entry["client"]):
            frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)


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
