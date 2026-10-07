"""Informational credit status from the latest selected period's portfolio."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.deposit_identity import credit_key
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.credit_lookup import get_credit_rows
from credinomina_reconciliation.paying_employers import allowed_employers
from credinomina_reconciliation.remittance_periods import selected_periods

IMPORTED = ["Importado", "Importado con alertas"]
FIELDS = ("portfolio_credit_status", "portfolio_report_date", "portfolio_snapshot_used")


def period_date(period):
    return str(period.get("historical_application_date") or period.get("historical_end_date")
               or period.get("cutoff_date") or period.get("payroll_month") or "")


def load_cuts(document, allowed):
    periods = selected_periods(document)
    if not periods or not all(frappe.has_permission(doctype, "read") for doctype in (
            "CN Reconciliation Period", "CN Accounting Import", "CN Credit Portfolio Snapshot")):
        return []
    periods = frappe.get_list("CN Reconciliation Period", filters={
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
    imports = frappe.get_list("CN Accounting Import", filters={
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
    cuts = frappe.get_list("CN Credit Portfolio Snapshot", filters={
        "disabled": 0, "name": ["in", sorted(names)], "status": ["in", IMPORTED]},
        fields=["name", "report_date"], order_by="report_date desc, name desc", limit_page_length=1)
    if not cuts or not cuts[0].report_date:
        return []
    previous = frappe.get_list("CN Credit Portfolio Snapshot", filters={
        "disabled": 0, "status": ["in", IMPORTED], "report_date": ["<", cuts[0].report_date]},
        fields=["name", "report_date"], order_by="report_date desc, name desc", limit_page_length=1)
    return cuts + previous


def apply_credit_states(details, cuts, portfolio_rows, allowed):
    index = defaultdict(list)
    for row in portfolio_rows:
        if row.get("employer") in allowed:
            index[(row.get("parent"), credit_key(row.get("credit_number")))].append(row)
    for detail in details:
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


def update_detail_portfolio(document):
    details = document.get("detail_rows") or []
    if not details:
        return
    previous = document.get_doc_before_save()

    def identity(doc):
        return [(row.get("name"), row.get("loan_number"), row.get("employer"), row.get("client"))
                for row in doc.get("detail_rows") or []]

    if (previous and document.employer == previous.employer
            and set(selected_periods(document)) == set(selected_periods(previous))
            and identity(document) == identity(previous)
            and all(row.get("portfolio_credit_status") for row in previous.get("detail_rows") or [])):
        for row, old in zip(details, previous.detail_rows):
            row.update({field: old.get(field) for field in FIELDS})
        return
    allowed = allowed_employers(document.employer)
    cuts = load_cuts(document, allowed)
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
