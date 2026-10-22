"""Small, exact period aggregates; parent names must already be permission-filtered."""
from collections import defaultdict

import frappe


def application_years(employer):
    """Resolve visible application years without loading amounts or cash JSON."""
    from frappe.utils import getdate
    from credinomina_reconciliation.application_context import (
        IMPORTED_STATUSES, application_employer, import_company_scope,
    )
    from credinomina_reconciliation.report_records import records, child_records
    if not frappe.has_permission("CN Accounting Import", "read"):
        return set()
    periods = {row.name: row for row in records("CN Reconciliation Period",
        filters={"employer": employer}, fields=["name", "employer"])}
    parents = {row.name: row for row in records("CN Accounting Import",
        filters={"status": ["in", list(IMPORTED_STATUSES)]}, **import_company_scope(employer),
        fields=["name", "employer", "historical_period"])}
    sources = list(child_records("CN Source Row", parents, "CN Accounting Import", "rows",
        filters={"event_type": "Aplicacion", "effective": 1, "event_date": ["is", "set"]},
        fields=["parent", "event_date", "historical_period", "collection_row_id", "portfolio_employer"]))
    # Only blank-company legacy imports can require collection evidence after
    # checking their direct period. Ordinary imports need no collection rows.
    collection_names = {row.collection_row_id for row in sources if row.collection_row_id
        and not parents[row.parent].employer
        and (row.historical_period or parents[row.parent].historical_period) not in periods}
    collections = {}
    names = sorted(collection_names)
    for offset in range(0, len(names), 500):
        if periods:
            collections.update({row.name: row for row in frappe.get_all("CN Collection Row", filters={
                "name": ["in", names[offset:offset + 500]], "parent": ["in", list(periods)],
                "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows",
            }, fields=["name", "parent"], limit_page_length=0)})
    return {getdate(row.event_date).year for row in sources if row.event_date
        and application_employer(row, parents[row.parent], periods, collections) == employer}


def collection_summaries(period_names):
    result = {}
    for start in range(0, len(period_names), 500):
        rows = frappe.db.sql("""
            SELECT parent,
                SUM(CASE WHEN COALESCE(expected_usd, 0) = 0 AND COALESCE(expected_nio, 0) != 0
                    THEN 1 ELSE 0 END) AS quality_missing_basis_count,
                SUM(CASE WHEN COALESCE(expected_usd, 0) = 0 AND COALESCE(expected_nio, 0) != 0 THEN 0
                    ELSE ABS(ROUND(COALESCE(expected_usd, 0), 2)
                    - ROUND(COALESCE(applied_usd, 0), 2)) END) AS worker_gap_usd,
                0 AS pending_detail_usd,
                0 AS employer_gap_usd,
                SUM(CASE WHEN application_status = 'Diferencia aplicacion vs deposito'
                    THEN 1 ELSE 0 END) AS application_difference_count,
                SUM(CASE WHEN application_status IN
                    ('Diferencia aplicacion vs deposito', 'Diferencia cambiaria en revision')
                    THEN 1 ELSE 0 END) AS difference_count
            FROM `tabCN Collection Row`
            WHERE parent IN %(parents)s AND parenttype = 'CN Reconciliation Period'
            GROUP BY parent
        """, {"parents": tuple(period_names[start:start + 500])}, as_dict=True)
        result.update({row.parent: row for row in rows})
    return result


def historical_difference_counts(period_names):
    result = defaultdict(int)
    if not frappe.has_permission("CN Accounting Import", "read"):
        return result
    for start in range(0, len(period_names), 500):
        rows = frappe.db.sql("""
            SELECT historical_period, parent, COUNT(*) AS total FROM `tabCN Source Row`
            WHERE historical_period IN %(periods)s AND event_type = 'Aplicacion' AND effective = 1
                AND parenttype = 'CN Accounting Import'
                AND deposit_match_status IN ('Diferencia de importe', 'Falta tipo de cambio', 'Ambiguo')
            GROUP BY historical_period, parent
        """, {"periods": tuple(period_names[start:start + 500])}, as_dict=True)
        allowed = readable_imports({row.parent for row in rows})
        for row in rows:
            if row.parent in allowed:
                result[row.historical_period] += int(row.total or 0)
    return result


def readable_imports(names):
    names = sorted(names)
    allowed = set()
    if not names or not frappe.has_permission("CN Accounting Import", "read"):
        return allowed
    for start in range(0, len(names), 500):
        allowed.update(frappe.get_list(
            "CN Accounting Import", filters={"name": ["in", names[start:start + 500]]},
            pluck="name", limit_page_length=0,
        ))
    return allowed
