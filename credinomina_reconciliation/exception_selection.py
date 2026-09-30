"""Permission-scoped picker for exception links. Never applies payments."""

import json

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.reconciliation import converted_amount


PAGE_SIZE = 30
RELATION_FIELDS = (
    "employer", "period", "collection_row_id", "source_import", "source_row",
    "client_name", "client_number", "loan_number", "related_case_type",
    "related_case_id", "related_case_summary",
)


def _require_access():
    if not any(frappe.has_permission("CN Reconciliation Exception", p)
               for p in ("create", "write")):
        frappe.throw(_("No tiene permiso para relacionar excepciones."), frappe.PermissionError)


def _links(row):
    raw = row.get("application_allocation_detail") or "[]"
    try:
        links = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
        return None
    names = {link.get("collection_row_id") for link in links if link.get("collection_row_id")}
    if not names and row.get("collection_row_id"):
        names.add(row["collection_row_id"])
    return names


def _candidates(employer, kind, period=None, search=None, row_id=None):
    if not employer or kind not in ("Cobranza", "Aplicación"):
        frappe.throw(_("Seleccione empresa y tipo de caso."))
    frappe.get_doc("CN Employer", employer).check_permission("read")
    # Child tables have no independent permissions. Scope every child query to
    # visible parent documents, including the period(s) of grouped applications.
    periods = {p.name: p for p in frappe.get_list(
        "CN Reconciliation Period", filters={"employer": employer},
        fields=["name", "status", "payroll_month"], limit_page_length=0,
    )} if frappe.has_permission("CN Reconciliation Period", "read") else {}
    if period and (period not in periods or periods[period].status == "Cerrado"):
        frappe.throw(_("Seleccione un período abierto de la empresa al que tenga acceso."))
    collection_filters = {"parent": ["in", list(periods)], "parenttype": "CN Reconciliation Period"}
    collections = {r.name: r for r in frappe.get_all(
        "CN Collection Row", filters=collection_filters,
        fields=["name", "parent", "client_name", "client_number", "loan_number", "source_row",
                "expected_usd", "applied_usd", "application_status", "installment_number"],
        order_by="parent asc, idx asc", limit_page_length=0,
    )} if periods else {}
    if kind == "Cobranza":
        for row in collections.values():
            if row_id and row.name != row_id:
                continue
            if periods[row.parent].status == "Cerrado" or (period and row.parent != period):
                continue
            yield _case(row, employer, kind, row.parent,
                        collection=row.name, amount=row.expected_usd,
                        date=periods[row.parent].payroll_month, status=row.application_status)
        return
    if not frappe.has_permission("CN Source Import", "read"):
        return
    imports = {p.name: p for p in frappe.get_list(
        "CN Source Import", filters={"employer": employer},
        fields=["name", "historical_period"], limit_page_length=0,
    )}
    if not imports:
        return
    filters = {"parent": ["in", list(imports)], "parenttype": "CN Source Import",
               "event_type": "Aplicacion", "effective": 1, "match_status": ["!=", "Ignorado"]}
    if row_id:
        filters["name"] = row_id
    search_filters = None
    if search:
        search_filters = {f: ["like", f"%{search}%"] for f in (
            "client_name", "client_number", "loan_number", "reference", "accounting_entry", "receipt",
        )}
    # Read in batches so large imports do not need to be materialized just to
    # display one page. The caller stops the iterator after PAGE_SIZE + 1 hits.
    start = 0
    while True:
        rows = frappe.get_all(
            "CN Source Row", filters=filters, or_filters=search_filters,
            fields=["name", "parent", "source_row", "idx", "client_name", "client_number", "loan_number",
                    "event_date", "reference", "accounting_entry", "receipt", "amount", "currency",
                    "equivalent_currency", "equivalent_amount", "fx_basis", "manual_fx_rate",
                    "collection_row_id", "collection_period", "historical_period", "application_allocation_detail",
                    "deposit_match_status", "match_status"],
            order_by="event_date desc, parent asc, idx asc, name asc", limit_start=start, limit_page_length=200,
        )
        for row in rows:
            links = _links(row)
            if links is None or any(name not in collections for name in links):
                continue  # Unknown, deleted or inaccessible linked claims.
            contexts = {collections[name].parent for name in links}
            if not contexts:
                context = row.historical_period or row.collection_period or imports[row.parent].historical_period
                contexts = {context or ""}
            if any(p and (p not in periods or periods[p].status == "Cerrado") for p in contexts):
                continue
            for context in sorted(contexts):
                if period and context != period:
                    continue
                matching = [name for name in links if collections[name].parent == context]
                yield _case(row, employer, kind, context,
                            collection=matching[0] if len(matching) == 1 else "",
                            source=row.parent, amount=converted_amount(row, "USD"),
                            date=row.event_date, status=row.deposit_match_status or row.match_status)
        if len(rows) < 200:
            return
        start += 200


def _case(row, employer, kind, period, *, collection="", source="", amount=None, date=None, status=None):
    reference = " · ".join(str(row.get(f)) for f in ("accounting_entry", "receipt", "reference") if row.get(f))
    summary = " · ".join(str(x) for x in (kind, row.get("client_name"), row.get("loan_number"), reference) if x)
    return {
        "related_case_type": kind, "related_case_id": row.name, "related_case_summary": summary,
        "employer": employer, "period": period or "", "collection_row_id": collection,
        "source_import": source, "source_row": (row.source_row or row.idx) if source else 0,
        "client_name": row.get("client_name") or "", "client_number": row.get("client_number") or "",
        "loan_number": row.get("loan_number") or "", "date": date, "amount_usd": amount,
        "reference": reference, "status": status or "Pendiente",
    }


@frappe.whitelist()
def get_related_cases(employer, kind="Cobranza", period=None, search=None, start=0):
    _require_access()
    start = max(0, cint(start))
    query = (search or "").strip()[:140]
    matches = []
    skipped = 0
    for case in _candidates(employer, kind, period, query):
        if kind == "Cobranza" and query and query.casefold() not in " ".join(
            str(case.get(f) or "") for f in ("client_name", "client_number", "loan_number")
        ).casefold():
            continue
        if skipped < start:
            skipped += 1
            continue
        matches.append(case)
        if len(matches) > PAGE_SIZE:
            break
    return {"rows": matches[:PAGE_SIZE], "has_more": len(matches) > PAGE_SIZE, "start": start}


@frappe.whitelist()
def resolve_related_case(employer, kind, row_id, period=None):
    _require_access()
    cases = list(_candidates(employer, kind, period, row_id=row_id))
    if len(cases) != 1:
        frappe.throw(_("El caso ya no está disponible o abarca varios períodos. Seleccione el período y vuelva a buscar."))
    return {field: cases[0][field] for field in RELATION_FIELDS}


def validate_selected_case(doc, previous):
    # Do not interfere with existing automatic reconciliation updates.
    # The picker itself cannot replace a keyed automatic exception's origin.
    if not doc.get("related_case_id"):
        if previous and previous.get("related_case_id"):
            frappe.throw(_("Para cambiar el vínculo use Seleccionar caso relacionado."))
        return
    if doc.get("exception_key"):
        frappe.throw(_("El caso de una excepción automática no se puede reemplazar."))
    values = resolve_related_case(doc.employer, doc.related_case_type, doc.related_case_id, doc.period)
    for field in ("period", "collection_row_id", "source_import", "source_row", "client_number", "loan_number"):
        if (doc.get(field) or "") != (values[field] or ""):
            frappe.throw(_("La relación cambió. Use Seleccionar caso relacionado para actualizarla antes de guardar."))
    doc.client_name = values["client_name"]
    doc.related_case_summary = values["related_case_summary"]
