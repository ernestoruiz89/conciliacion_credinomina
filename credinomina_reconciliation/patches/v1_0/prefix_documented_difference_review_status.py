"""Label documented company differences for review without changing allocations."""
import frappe


def execute():
    frappe.db.sql("""UPDATE `tabCN Remittance Detail`
        SET match_status = %s
        WHERE match_status = %s AND row_kind = %s AND parenttype = %s
        AND parentfield = %s AND docstatus < 2""",
        ("Revisar: Diferencia documentada", "Diferencia documentada", "Diferencia documentada",
         "CN Remittance Allocation", "detail_rows"))
