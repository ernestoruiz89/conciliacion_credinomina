"""Informational credit status from the latest selected period's portfolio."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.deposit_identity import credit_key
from credinomina_reconciliation.parsers import canonical_identifier, clean_text
from credinomina_reconciliation.credit_lookup import get_credit_rows
from credinomina_reconciliation.paying_employers import allowed_employers
from credinomina_reconciliation.remittance_credit_selection import portfolio_row_matches_client
from credinomina_reconciliation.remittance_periods import selected_periods
from credinomina_reconciliation.report_records import INCOMPLETE_EVIDENCE

IMPORTED = ["Importado", "Importado con alertas"]
FIELDS = ("portfolio_credit_status", "portfolio_report_date", "portfolio_snapshot_used")


def period_date(period):
    return str(period.get("historical_application_date") or period.get("historical_end_date")
               or period.get("cutoff_date") or period.get("payroll_month") or "")


def load_cuts(document, allowed, *, complete=False):
    periods = selected_periods(document)
    if not periods:
        return []
    if not all(frappe.has_permission(doctype, "read") for doctype in (
            "CN Reconciliation Period", "CN Accounting Import", "CN Credit Portfolio Snapshot")):
        if complete:
            frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)
        return []

    def readable(doctype, **kwargs):
        result = frappe.get_list(doctype, **kwargs)
        if complete:
            # Compare the same selection (including ordering/limit) so a hidden
            # dependency cannot clear a stored state or substitute an older cut.
            expected = frappe.get_all(doctype, **{**kwargs, "fields": ["name"]})
            if {row.name for row in result} != {row.name for row in expected}:
                frappe.throw(frappe._(INCOMPLETE_EVIDENCE), frappe.PermissionError)
        return result

    periods = readable("CN Reconciliation Period", filters={
        "name": ["in", periods], "employer": ["in", sorted(allowed)]}, fields=[
        "name", "employer", "historical_application_date", "historical_end_date", "cutoff_date", "payroll_month"],
        limit_page_length=0)
    if not periods:
        return []
    latest = max(periods, key=lambda row: (period_date(row), row.name))
    sources = frappe.get_all("CN Source Row", filters={
        "parenttype": "CN Accounting Import", "parentfield": "rows", "docstatus": ["!=", 2],
        "event_type": "Aplicacion"}, or_filters={
        "historical_period": latest.name, "collection_period": latest.name},
        fields=["parent", "portfolio_snapshot_used"], limit_page_length=0)
    imports = readable("CN Accounting Import", filters={
        "employer": latest.employer, "docstatus": ["!=", 2],
        "status": ["in", ["Importado", "Importado con excepciones"]]},
        or_filters={"historical_period": latest.name, "name": ["in", list({row.parent for row in sources})]},
        fields=["name", "portfolio_snapshot"], limit_page_length=0)
    by_name = {row.name: row for row in imports}
    names = {row.portfolio_snapshot for row in imports if row.portfolio_snapshot}
    # An automatic selection is recorded per source row, not on its parent.
    names.update(row.portfolio_snapshot_used for row in sources
                 if row.parent in by_name and not by_name[row.parent].portfolio_snapshot
                 and row.portfolio_snapshot_used)
    if not names:
        return []
    cuts = readable("CN Credit Portfolio Snapshot", filters={
        "disabled": 0, "name": ["in", sorted(names)], "status": ["in", IMPORTED]},
        fields=["name", "report_date"], order_by="report_date desc, name desc", limit_page_length=1)
    if not cuts or not cuts[0].report_date:
        return []
    previous = readable("CN Credit Portfolio Snapshot", filters={
        "disabled": 0, "status": ["in", IMPORTED], "report_date": ["<", cuts[0].report_date]},
        fields=["name", "report_date"], order_by="report_date desc, name desc", limit_page_length=1)
    return cuts + previous


def fill_unique_detail_credits(document, clients):
    """Fill missing imported credits only when one row in the applicable cut fits."""
    details = [row for row in document.get("detail_rows") or []
               if not clean_text(row.get("loan_number")) and row.get("client") and row.get("employer")]
    if not details:
        return
    allowed = allowed_employers(document.employer)
    cuts = load_cuts(document, allowed)
    if not cuts:
        return
    detail_employers = sorted({row.employer for row in details if row.employer in allowed})
    if not detail_employers:
        return
    detail_clients = {row.client for row in details}
    clients_by_name = {client["name"]: client for client in clients
                       if client["name"] in detail_clients}
    by_core = defaultdict(set)
    for client in clients:
        core = canonical_identifier(client.get("client_number"))
        if core:
            by_core[(client.get("employer"), core)].add(client["name"])
    rows = frappe.get_all("CN Credit Portfolio Row", filters={
        "parent": ["in", [cut.name for cut in cuts]],
        "parenttype": "CN Credit Portfolio Snapshot", "parentfield": "rows",
        "employer": ["in", detail_employers],
    }, fields=["name", "parent", "credit_number", "employer", "matched_client",
               "client_number_core", "national_id"], limit_page_length=0)
    candidates = defaultdict(list)
    for row in rows:
        if not clean_text(row.get("credit_number")):
            continue
        linked = row.get("matched_client")
        if linked:
            names = [linked] if linked in clients_by_name else []
        else:
            core = canonical_identifier(row.get("client_number_core"))
            names = by_core.get((row.get("employer"), core), set()) if core else set()
            if len(names) != 1:
                continue
            names = names & detail_clients
        for name in names:
            client = clients_by_name[name]
            if portfolio_row_matches_client(row, client, client.get("employer")):
                candidates[(row.parent, name, row.employer)].append(row)
    for detail in details:
        if detail.client not in clients_by_name:
            continue
        for cut in cuts:
            matches = candidates.get((cut.name, detail.client, detail.employer), [])
            if not matches:
                continue
            # Multiple rows, even repeated credit numbers, require a manual choice.
            if len(matches) == 1:
                detail.loan_number = clean_text(matches[0].credit_number)
            break


def apply_credit_states(details, cuts, portfolio_rows, allowed):
    index = defaultdict(list)
    for row in portfolio_rows:
        if row.get("employer") in allowed:
            index[(row.get("parent"), credit_key(row.get("credit_number")))].append(row)
    for detail in details:
        if detail.get("row_kind") == "Diferencia documentada":
            detail.update(dict(zip(FIELDS, ("No aplica", None, None))))
            continue
        detail.update(dict(zip(FIELDS, ("No Identificado", None, None))))
        key = credit_key(detail.get("loan_number"))
        if not key:
            continue
        for cut in cuts:
            matches = index.get((cut.name, key), [])
            if detail.get("employer"):
                matches = [row for row in matches if row.get("employer") == detail.employer]
            if not matches:
                continue
            # Ambiguity or a contradictory client must not fall back to an older state.
            if len(matches) != 1:
                break
            row = matches[0]
            if detail.get("client") and row.get("matched_client") and detail.client != row["matched_client"]:
                break
            detail.update({"portfolio_credit_status": clean_text(row.get("credit_status")) or "No Identificado",
                           "portfolio_report_date": cut.report_date, "portfolio_snapshot_used": cut.name})
            break


def update_detail_portfolio(document, *, force=False, require_cuts=False):
    details = document.get("detail_rows") or []
    if not details:
        return
    previous = document.get_doc_before_save()

    def identity(doc):
        return [(row.get("name"), row.get("loan_number"), row.get("employer"), row.get("client"))
                for row in doc.get("detail_rows") or []]

    if (not force and previous and document.employer == previous.employer
            and set(selected_periods(document)) == set(selected_periods(previous))
            and identity(document) == identity(previous)
            and all(row.get("portfolio_credit_status") for row in previous.get("detail_rows") or [])):
        for row, old in zip(details, previous.detail_rows):
            row.update({field: old.get(field) for field in FIELDS})
        return
    allowed = allowed_employers(document.employer)
    cuts = load_cuts(document, allowed, complete=force)
    if require_cuts and not cuts:
        frappe.throw(frappe._("No hay un corte de cartera importado para los períodos seleccionados. Revise las aplicaciones y su corte de cartera."))
    loans = sorted({credit_key(row.get("loan_number")) for row in details if row.get("loan_number")})
    rows = []
    if cuts:
        for offset in range(0, len(loans), 500):
            rows.extend(get_credit_rows("CN Credit Portfolio Row", filters={
                "parent": ["in", [cut.name for cut in cuts]], "parenttype": "CN Credit Portfolio Snapshot",
                "parentfield": "rows", "employer": ["in", sorted(allowed)]},
                fields=["parent", "credit_number", "credit_status", "employer", "matched_client"],
                loan_field="credit_number", loans=loans[offset:offset + 500]))
    apply_credit_states(details, cuts, rows, allowed)
    return cuts


@frappe.whitelist(methods=["POST"])
def refresh_credit_status(remittance_name, modified):
    """Refresh only portfolio evidence, without saving or reconciling the deposit."""
    from credinomina_reconciliation.deposit_accounting_sources import assert_usable_cash
    from credinomina_reconciliation.portfolio_refresh import _locked_document, _persist_evidence

    doc = _locked_document("CN Remittance Allocation", remittance_name)
    assert_usable_cash(doc)
    if doc.docstatus == 2 or str(doc.modified) != str(modified):
        frappe.throw(frappe._("El depósito cambió o está cancelado. Recargue antes de actualizar."))
    if not doc.detail_rows:
        frappe.throw(frappe._("El depósito no tiene filas de detalle para actualizar."))
    if not selected_periods(doc):
        frappe.throw(frappe._("Seleccione al menos un período del detalle antes de actualizar los estados de crédito."))
    before = [frappe._dict(row.as_dict()) for row in doc.detail_rows]
    cuts = update_detail_portfolio(doc, force=True, require_cuts=True)
    values = [row.as_dict() for row in doc.detail_rows]
    cuts[0].file_hash = frappe.db.get_value("CN Credit Portfolio Snapshot", cuts[0].name, "file_hash")
    changed = _persist_evidence(doc, before, values, FIELDS, cuts[0])
    customers = [row for row in doc.detail_rows if row.get("row_kind") != "Diferencia documentada"]
    unidentified = sum(row.get("portfolio_credit_status") == "No Identificado" for row in customers)
    return {"deposit": doc.name, "updated": changed, "customers": len(customers),
            "unidentified": unidentified, "cut": cuts[0].name,
            "previous_cut": cuts[1].name if len(cuts) > 1 else None}
