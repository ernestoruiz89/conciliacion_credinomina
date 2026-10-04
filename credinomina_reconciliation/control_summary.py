"""Small, exact period aggregates; parent names must already be permission-filtered."""
from collections import defaultdict

import frappe
from credinomina_reconciliation.aging import VALID_DEDUCTION_STATUSES


def collection_summaries(period_names):
    result = {}
    for start in range(0, len(period_names), 500):
        rows = frappe.db.sql("""
            SELECT parent,
                SUM(CASE WHEN COALESCE(deduction_status, '') NOT IN %(valid_statuses)s
                    THEN 0 ELSE GREATEST(ROUND(COALESCE(expected_usd, 0), 2)
                        - ROUND(COALESCE(deducted_usd, 0), 2), 0) END) AS worker_gap_usd,
                SUM(CASE WHEN COALESCE(deduction_status, '') NOT IN %(valid_statuses)s
                    THEN COALESCE(expected_usd, 0) ELSE 0 END) AS pending_detail_usd,
                SUM(CASE WHEN COALESCE(deduction_status, '') NOT IN %(valid_statuses)s
                    THEN 0 ELSE GREATEST(ROUND(COALESCE(deducted_usd, 0), 2)
                        - ROUND(COALESCE(remitted_usd, 0), 2)
                        - GREATEST(ROUND(COALESCE(fx_variance_usd, 0), 2), 0)
                        - GREATEST(-ROUND(COALESCE(rounding_adjustment_usd, 0), 2), 0), 0)
                    END) AS employer_gap_usd,
                SUM(CASE WHEN application_status = 'Diferencia aplicacion vs deposito'
                    THEN 1 ELSE 0 END) AS application_difference_count,
                SUM(CASE WHEN application_status IN
                    ('Diferencia aplicacion vs deposito', 'Diferencia cambiaria en revision')
                    THEN 1 ELSE 0 END) AS difference_count
            FROM `tabCN Collection Row`
            WHERE parent IN %(parents)s AND parenttype = 'CN Reconciliation Period'
            GROUP BY parent
        """, {"parents": tuple(period_names[start:start + 500]),
              "valid_statuses": VALID_DEDUCTION_STATUSES}, as_dict=True)
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
