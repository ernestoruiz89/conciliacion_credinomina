"""Clarify the label without recalculating allocations or changing balances."""
import frappe


def execute():
    frappe.db.sql("""UPDATE `tabCN Remittance Detail`
        SET match_status = CASE WHEN pending_usd = 0 THEN %s
                                WHEN pending_usd > 0 THEN %s ELSE %s END
        WHERE match_status = %s AND parenttype = %s AND parentfield = %s AND docstatus = 1""",
        ("Conciliada", "Cobertura parcial; diferencia de empresa", "Revisar", "Conciliada con diferencia documentada",
         "CN Remittance Allocation", "detail_rows"))
