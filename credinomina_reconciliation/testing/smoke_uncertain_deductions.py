"""Real SQL/Python parity for invalid deduction evidence; always rolled back."""
import frappe
from credinomina_reconciliation.aging import collection_shortfall_usd, unassigned_deduction_usd
from credinomina_reconciliation.control_summary import collection_summaries


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    parent = "deduction-audit-" + frappe.generate_hash(length=12)
    rows = [
        dict(expected_usd=100, deducted_usd=70, remitted_usd=0, deduction_status="Deduccion parcial"),
        dict(expected_usd=100, deducted_usd=50, remitted_usd=0, deduction_status="Importes inconsistentes"),
        dict(expected_usd=100, deducted_usd=0, remitted_usd=0, deduction_status="Pendiente de detalle"),
    ]
    try:
        for index, row in enumerate(rows):
            frappe.db.sql("""INSERT INTO `tabCN Collection Row`
                (name, parent, parenttype, parentfield, idx, expected_usd, deducted_usd, remitted_usd, deduction_status)
                VALUES (%(name)s, %(parent)s, 'CN Reconciliation Period', 'collection_rows', %(idx)s,
                        %(expected_usd)s, %(deducted_usd)s, %(remitted_usd)s, %(deduction_status)s)""",
                dict(row, name=parent + str(index), parent=parent, idx=index + 1))
        summary = collection_summaries([parent])[parent]
        assert float(summary.worker_gap_usd) == sum(collection_shortfall_usd(row) or 0 for row in rows) == 30
        assert float(summary.employer_gap_usd) == sum(unassigned_deduction_usd(row) or 0 for row in rows) == 70
        assert float(summary.pending_detail_usd) == 200
        return {"ok": True, "checks": ["SQL and Python agree", "inconsistent detail is not debt", "missing detail remains visible"]}
    finally:
        frappe.db.rollback()
