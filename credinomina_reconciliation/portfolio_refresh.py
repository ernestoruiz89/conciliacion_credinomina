"""Refresh stored portfolio evidence atomically with a cut's import.

Only portfolio-derived fields are written. Source identities, reconciliation
links, allocations and money are preserved, including on closed/submitted data.
"""
from html import escape

import frappe
from frappe.utils import now_datetime

from credinomina_reconciliation.credit_portfolio import PORTFOLIO_FIELDS, index_portfolio_rows, refresh_accounting_portfolio
from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER
from credinomina_reconciliation.remittance_portfolio import FIELDS, update_detail_portfolio
from credinomina_reconciliation.report_records import _pages, child_records, PAGE_SIZE


def _imports_for_cuts(names):
    names = sorted(names)
    parents = {row.name for row in _pages(frappe.get_all, "CN Accounting Import",
        filters={"portfolio_snapshot": ["in", names], "docstatus": ["!=", 2]}, fields=["name"])}
    parents.update(row.parent for row in _pages(frappe.get_all, "CN Source Row", filters={
        "portfolio_snapshot_used": ["in", names], "parenttype": "CN Accounting Import",
        "parentfield": "rows", "docstatus": ["!=", 2],
    }, fields=["parent"]))
    return parents


def _related_deposits(snapshot, previous_report_date):
    # Deposits can use this cut as fallback even when none of their current
    # detail rows found a credit in it. Include periods using its next cut.
    cuts = {snapshot.name}
    for date in {str(value)[:10] for value in (snapshot.report_date, previous_report_date) if value}:
        following = frappe.get_all("CN Credit Portfolio Snapshot", filters={
            "name": ["!=", snapshot.name], "disabled": 0, "docstatus": ["!=", 2],
            "status": ["in", ["Importado", "Importado con alertas"]], "report_date": [">", date],
        }, fields=["name"], order_by="report_date asc, name desc", limit_page_length=1)
        cuts.update(row.name for row in following)
    imports = sorted(_imports_for_cuts(cuts))
    periods = set()
    for offset in range(0, len(imports), PAGE_SIZE):
        for row in _pages(frappe.get_all, "CN Accounting Import", filters={
            "name": ["in", imports[offset:offset + PAGE_SIZE]], "docstatus": ["!=", 2],
        }, fields=["historical_period"]):
            periods.add(row.historical_period)
    for row in child_records("CN Source Row", imports, "CN Accounting Import", "rows",
                             fields=["historical_period", "collection_period"]):
        periods.update((row.historical_period, row.collection_period))
    periods = sorted(periods - {None, ""})
    names = {row.parent for row in _pages(frappe.get_all, "CN Remittance Detail", filters={
        "portfolio_snapshot_used": snapshot.name, "parenttype": "CN Remittance Allocation",
        "parentfield": "detail_rows", "docstatus": ["!=", 2],
    }, fields=["parent"])}
    for offset in range(0, len(periods), PAGE_SIZE):
        names.update(row.parent for row in _pages(frappe.get_all, "CN Remittance Period", filters={
            "period": ["in", periods[offset:offset + PAGE_SIZE]],
            "parenttype": "CN Remittance Allocation", "parentfield": "detail_periods",
            "docstatus": ["!=", 2],
        }, fields=["parent"]))
    return names


def _locked_document(doctype, name):
    doc = frappe.get_doc(doctype, name, for_update=True)
    doc.check_permission("read")
    doc.check_permission("write")
    doc.check_if_locked()
    return doc


def _persist_evidence(document, rows, updates, fields, snapshot):
    changed = 0
    for row, values in zip(rows, updates):
        differences = {field: values.get(field) for field in fields
                       if str(row.get(field) or "") != str(values.get(field) or "")}
        if not differences:
            continue
        frappe.db.set_value(row.get("doctype"), row.get("name"), differences, update_modified=False)
        changed += 1
    if changed:
        # Invalidate stale open forms without running financial save hooks.
        document.modified = now_datetime()
        document.modified_by = frappe.session.user
        frappe.db.set_value(document.doctype, document.name,
            {"modified": document.modified, "modified_by": document.modified_by}, update_modified=False)
        document.add_comment("Comment", "<p>" + escape(frappe._(
            "Datos de cartera actualizados desde {0}: {1} filas. SHA-256: {2}."
        ).format(snapshot.name, changed, snapshot.file_hash or "")) + "</p>")
        frappe.clear_document_cache(document.doctype, document.name)
        document.notify_update()
    return changed


def refresh_portfolio_dependencies(snapshot, previous_report_date=None):
    """No commits: a failure rolls back the cut and all dependent updates."""
    result = dict(updated_accounting_imports=0, updated_source_rows=0,
                  updated_deposits=0, updated_deposit_rows=0)
    index = index_portfolio_rows(snapshot.rows)
    for name in sorted(_imports_for_cuts({snapshot.name})):
        doc = _locked_document("CN Accounting Import", name)
        if doc.docstatus == 2:
            continue
        rows = [row for row in doc.rows if row.portfolio_snapshot_used == snapshot.name or (
            row.event_type == "Aplicacion" and doc.portfolio_snapshot == snapshot.name)]
        if not rows:
            continue
        values = [dict(row.as_dict(), event_type="Aplicacion") for row in rows]
        refresh_accounting_portfolio(values, snapshot, index=index)
        for value in values:
            if (doc.employer and doc.employer != UNIDENTIFIED_EMPLOYER and value.get("portfolio_employer")
                    and value["portfolio_employer"] != doc.employer):
                value["portfolio_validation_status"] = "Empresa del movimiento distinta a la del corte"
        count = _persist_evidence(doc, rows, values, PORTFOLIO_FIELDS, snapshot)
        result["updated_source_rows"] += count
        result["updated_accounting_imports"] += bool(count)
    for name in sorted(_related_deposits(snapshot, previous_report_date)):
        doc = _locked_document("CN Remittance Allocation", name)
        if doc.docstatus == 2:
            continue
        before = [row.as_dict().copy() for row in doc.detail_rows]
        update_detail_portfolio(doc, force=True)
        count = _persist_evidence(doc, before, doc.detail_rows, FIELDS, snapshot)
        result["updated_deposit_rows"] += count
        result["updated_deposits"] += bool(count)
    return result
