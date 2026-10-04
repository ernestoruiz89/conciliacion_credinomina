"""One-time, explicitly estimated terms for existing rows; never recalculate cash."""
import frappe

from credinomina_reconciliation.application_deadlines import deadline_values, MIGRATED


def execute():
    last = ""
    while True:
        rows = frappe.db.sql("""
            SELECT r.name, r.event_type, r.event_date, i.employer, e.grace_days
            FROM `tabCN Source Row` r
            INNER JOIN `tabCN Accounting Import` i ON i.name=r.parent
            LEFT JOIN `tabCN Employer` e ON e.name=i.employer
            WHERE r.parenttype='CN Accounting Import' AND r.parentfield='rows'
              AND r.event_type='Aplicacion' AND r.payment_due_date IS NULL AND r.name > %s
            ORDER BY r.name LIMIT 500
        """, last, as_dict=True)
        if not rows:
            break
        for row in rows:
            values = deadline_values(row, row.employer, row.grace_days, MIGRATED)
            frappe.db.set_value("CN Source Row", row.name, values, update_modified=False)
        last = rows[-1].name
