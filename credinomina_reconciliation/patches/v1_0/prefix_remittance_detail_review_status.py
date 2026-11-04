"""Make existing unresolved detail labels filterable without reconciling cash."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.remittance_detail_status import review_status
from credinomina_reconciliation.report_records import _pages


def execute():
    groups = defaultdict(list)
    for row in _pages(frappe.get_all, "CN Remittance Detail", filters={
            "parenttype": "CN Remittance Allocation", "parentfield": "detail_rows", "docstatus": ["<", 2]},
            fields=["name", "match_status"]):
        previous = row.match_status or ""
        status = review_status(previous)
        if previous != status:
            groups[(previous, status)].append(row.name)
    for (previous, status), names in groups.items():
        for start in range(0, len(names), 500):
            frappe.db.sql("""UPDATE `tabCN Remittance Detail` SET match_status=%(status)s
                WHERE name IN %(names)s AND parenttype='CN Remittance Allocation'
                AND parentfield='detail_rows' AND docstatus<2 AND coalesce(match_status,'')=%(previous)s""",
                dict(status=status, previous=previous, names=tuple(names[start:start + 500])))
