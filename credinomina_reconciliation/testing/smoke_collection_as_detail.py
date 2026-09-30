"""Recognize collection as company detail without requiring a deposit; roll back."""
import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import recognize_collection_as_employer_detail


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = "recognition-" + frappe.generate_hash(length=10)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2026-09-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual"})
        for index, (usd, nio) in enumerate(((10, 366.24), (20, 0), (0, 732.48)), 1):
            period.append("collection_rows", {"row_key": f"{marker}-{index}",
                "client_name": f"Cliente {index}", "client_number": f"{marker}-{index}",
                "loan_number": f"{marker}-{index}-1", "expected_usd": usd, "expected_nio": nio,
                "deduction_status": "Pendiente de detalle", "application_status": "Pendiente"})
        period.insert()
        names = [row.name for row in period.collection_rows]
        result = recognize_collection_as_employer_detail(period.name, "2026-09-30", 1)
        period.reload()
        assert result["rows"] == 3
        assert [row.name for row in period.collection_rows] == names
        assert period.deducted_usd == 30 and period.deducted_nio == 1098.72
        assert [row.deduction_currency for row in period.collection_rows] == ["Ambas", "USD", "NIO"]
        assert all(row.deduction_status == "Deduccion total" for row in period.collection_rows)
        assert all("Administrator" in row.deduction_match_note for row in period.collection_rows)
        assert period.deduction_basis == "Detalle de empresa"
        assert not period.employer_response_file and not period.deduction_recognition_deposit
        assert period.applied_usd == 0 and period.remitted_usd == 0 and period.status != "Conciliado"
        try:
            recognize_collection_as_employer_detail(period.name, "2026-09-30", 1)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("No debe reemplazar un detalle existente")
        return {"without_deposit": True, "deduction_currencies": ["Ambas", "USD", "NIO"],
                "preserves_rows": True, "does_not_invent_payments": True,
                "duplicate_blocked": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
