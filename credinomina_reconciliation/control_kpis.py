"""On-demand dashboard figures; no reconciliation or cross-client netting."""
from datetime import timedelta

import frappe
from frappe.utils import getdate

from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.rounding import money, money_float, sum_money


def year_filter(field, year):
    return {field: ["between", [f"{year}-01-01", f"{year}-12-31"]]} if year else {}


def overdue_filters(year, employer, today):
    end = getdate(today) - timedelta(days=1)
    filters = {"status": ["in", ["Abierta", "En revision"]], "docstatus": ["!=", 2]}
    if employer:
        filters["employer"] = employer
    if year:
        start = getdate(f"{year}-01-01")
        end = min(end, getdate(f"{year}-12-31"))
        if start > end:
            return None
        filters["commitment_date"] = ["between", [str(start), str(end)]]
    else:
        filters["commitment_date"] = ["<=", str(end)]
    return filters


def _children(doctype, names, fields, **filters):
    # Only query children of permission-filtered parents, in bounded batches.
    names = sorted(names)
    rows = []
    for offset in range(0, len(names), 500):
        rows.extend(frappe.get_all(doctype,
            filters={"parent": ["in", names[offset:offset + 500]], **filters},
            fields=fields, limit_page_length=0))
    return rows


def load_application_figures(year, employer, today):
    if not frappe.has_permission("CN Accounting Import", "read"):
        return None
    scope = {'employer': employer} if employer else {}
    periods = {row.name: row for row in frappe.get_list("CN Reconciliation Period", filters=scope,
        fields=["name", "employer", "payroll_month", "collection_cycle", "reconciliation_mode"],
        limit_page_length=0)}
    imports = {row.name: row for row in frappe.get_list("CN Accounting Import",
        filters={**scope, "status": ["in", ["Importado", "Importado con excepciones"]]},
        fields=["name", "employer", "historical_backfill", "historical_period"], limit_page_length=0)}
    sources = _children("CN Source Row", imports, [
        "name", "parent", "event_type", "event_date", "effective", "match_status",
        "payment_due_date", "payment_term_origin",
        "currency", "amount", "equivalent_currency", "equivalent_amount", "fx_basis", "manual_fx_rate",
        "processing_route", "historical_period", "portfolio_employer", "collection_row_id",
        "application_allocation_detail", "historical_remitted_usd", "historical_detail", "application_adjustment_usd",
    ], parenttype="CN Accounting Import", parentfield="rows", event_type="Aplicacion", effective=1)
    collections = {row.name: row for row in _children("CN Collection Row", periods, [
        "name", "parent", "remittance_detail", "rounding_adjustment_usd", "fx_variance_usd",
    ], parenttype="CN Reconciliation Period", parentfield="collection_rows")}
    employer_names = {row.get("employer") for row in [*periods.values(), *imports.values()]}
    employer_names.update(row.get("portfolio_employer") for row in sources)
    employers = {}
    names = sorted(employer_names - {None, ""})
    for offset in range(0, len(names), 500):
        employers.update({row.name: row for row in frappe.get_all("CN Employer",
            filters={"name": ["in", names[offset:offset + 500]]},
            fields=["name", "grace_days"], limit_page_length=0)})
    # Keep whole operative claims until cash has been subtracted ONCE. Filtering
    # source dates first can spend the same deposit again in a different year.
    rows = application_balances(sources, imports, periods, collections, employers, today, include_settled=True)
    return summarize_applications(rows, year, employer, today)


def summarize_applications(rows, year=None, employer=None, as_of=None):
    selected = []
    for row in rows:
        if employer and row.get("employer") != employer:
            continue
        month = row.get("payroll_month") or row.get("application_date")
        if month and as_of and getdate(month).replace(day=1) > getdate(as_of):
            continue
        if year and (not month or getdate(month).year != year):
            continue
        selected.append(row)
    return {
        "net_applied_usd": money_float(sum_money(row.get("applied_usd") for row in selected)),
        "pending_usd": money_float(sum_money(row.get("amount_usd") for row in selected)),
        "overdue_usd": money_float(sum_money(row.get("amount_usd") for row in selected if (row.get("age_days") or 0) > 0)),
        "without_date_usd": money_float(sum_money(row.get("without_date") for row in selected)),
        "missing_fx_count": sum("amount_usd" not in row for row in selected),
        "unlinked_usd": money_float(sum_money(row.get("applied_usd") for row in selected if not row.get("period"))),
    }


def summarize_deposits(rows):
    rows = list({row["name"]: row for row in rows}.values())
    remaining = [max(money(row.get("amount_usd")) - money(row.get("allocated_usd"))
                     - money(row.get("justified_surplus_usd")), 0) for row in rows]
    return {
        "received_usd": money_float(sum_money(row.get("amount_usd") for row in rows)),
        "deposit_count": len(rows),
        "unassigned_usd": money_float(sum_money(remaining)),
        "unassigned_count": sum(value > 0 for value in remaining),
        "overallocated_count": sum(money(row.get("amount_usd")) < money(row.get("allocated_usd"))
                                   + money(row.get("justified_surplus_usd")) for row in rows),
    }


def summarize_credits(rows):
    client, company = money(0), money(0)
    for row in {row["name"]: row for row in rows}.values():
        pending = row.get("credit_pending_usd") if row.get("credit_management_status") else row.get("amount_usd")
        if row.get("category") == "Saldo a favor del cliente":
            # Older/uninitialized follow-up fields must not turn a credit into zero.
            client += max(money(pending), 0)
        else:
            company += max(money(pending), 0)
    return {"client_pending_usd": float(client), "company_documented_usd": float(company),
            "credit_pending_usd": float(client + company)}


def get_figures(year, employer, today):
    company = {"employer": employer} if employer else {}
    applications = load_application_figures(year, employer, today)
    deposits = None
    if frappe.has_permission("CN Remittance Allocation", "read"):
        deposits = summarize_deposits(frappe.get_list("CN Remittance Allocation",
            filters={**company, **year_filter("deposit_date", year), "docstatus": 1},
            fields=["name", "amount_usd", "allocated_usd", "justified_surplus_usd"], limit_page_length=0))
    credits = None
    if frappe.has_permission("CN Complementary Item", "read"):
        credits = summarize_credits(frappe.get_list("CN Complementary Item",
            filters={**company, **year_filter("posting_date", year), "docstatus": 1,
                     "category": ["in", ["Saldo a favor del cliente", "Saldo a favor de la empresa"]],
                     "result": "Saldo a favor documentado"},
            fields=["name", "category", "amount_usd", "credit_pending_usd", "credit_management_status"],
            limit_page_length=0))
    exceptions = None
    if frappe.has_permission("CN Reconciliation Exception", "read"):
        filters = overdue_filters(year, employer, today)
        # get_list preserves record-level permissions; db.count would not.
        exceptions = {"count": len(frappe.get_list("CN Reconciliation Exception", filters=filters,
            pluck="name", limit_page_length=0)) if filters is not None else 0, "filters": filters}
    receivables = None
    if frappe.has_permission('CN Complementary Item', 'read') and frappe.has_permission('CN Remittance Allocation', 'read'):
        from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
        from credinomina_reconciliation.aging import age_balance
        rows = load_receivables(employer, year)
        receivables = dict(pending_usd=money_float(sum_money(row['receivable_usd'] for row in rows)),
            overdue_usd=money_float(sum_money(row['receivable_usd'] for row in rows
                if (age_balance(row['receivable_usd'], row.get('credit_commitment_date'), today)['age_days'] or 0) > 0)),
            without_date_usd=money_float(sum_money(row['receivable_usd'] for row in rows if not row.get('credit_commitment_date'))))
    return {"applications": applications, "deposits": deposits, "credits": credits, "receivables": receivables,
            "exceptions": exceptions, "as_of_date": str(today)}
