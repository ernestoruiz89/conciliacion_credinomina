"""Receipt-month dashboard smoke, only on the disposable site; always roll back."""
import json

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_control_data


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = frappe.generate_hash(length=10)
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": f"Cash Overview {marker}",
            "employer_code": f"CD{marker}", "payroll_frequency": "Mensual",
        }).insert()
        bank = frappe.get_doc({
            "doctype": "CN Bank Account", "account_name": f"Banco prueba {marker}",
            "bank_name": "Banco prueba", "account_number": marker, "currency": "NIO",
        }).insert()
        periods = [frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": month, "reconciliation_mode": "Historica",
        }).insert() for month in ("2025-04-01", "2025-05-01")]
        fee_name = f"CASH-FEE-{marker}"
        frappe.get_doc({"doctype": "CN Complementary Item", "name": fee_name,
                        "docstatus": 1, "period": periods[1].name,
                        "category": "Cobranza administrativa", "amount_usd": 100}).db_insert()
        entries = [
            {"tipo": "Aplicacion historica", "periodo": periods[0].name, "importe_usd": 300},
            {"tipo": "Cobranza", "periodo": periods[1].name, "importe_usd": 500},
            {"tipo": "Partida complementaria", "partida": fee_name, "importe_usd": 100},
        ]
        names = []
        # Deliberate read fixtures: do not run reconciliation hooks or change
        # existing source rows just to test dashboard aggregation.
        for suffix, date, status in (("JUNE", "2025-06-20", 1), ("JAN", "2026-01-05", 1),
                                     ("DRAFT", "2025-06-21", 0), ("CANCEL", "2025-06-22", 2)):
            name = f"CASH-{suffix}-{marker}"
            frappe.get_doc({
                "doctype": "CN Remittance Allocation", "name": name, "docstatus": status,
                "employer": employer.name, "deposit_date": date, "deposit_reference": name,
                "bank_account": bank.name,
                "deposit_currency": "NIO", "deposit_amount": 36624.30, "amount_usd": 1000,
                "allocated_usd": 900, "unallocated_usd": 100, "justified_surplus_usd": 100,
                "unclassified_usd": 0, "result": "Parcial con saldo a favor",
                "allocation_detail": json.dumps(entries),
            }).db_insert()
            if status == 1:
                names.append(name)
        data = get_control_data(2025, employer.name)
        assert {p["month"] for p in data["periods"]} == {"2025-04", "2025-05"}
        june, = data["cash_deposits"]
        assert june["name"] == names[0] and june["month"] == "2025-06"
        assert june["bank_account"] == bank.account_name
        assert june["total_usd"] == 1000 and june["credits_usd"] == 800
        assert june["other_usd"] == 100 and june["credit_balance_usd"] == 100
        assert june["shared"] and len(june["destinations"]) == 3
        assert not june["settled"] and not june["needs_review"]
        next_year = get_control_data(2026, employer.name)
        assert next_year["periods"] == []
        january, = next_year["cash_deposits"]
        assert january["name"] == names[1]
        assert january["payroll_months"] == ["2025-04", "2025-05"]
        all_years = get_control_data("Todos", employer.name)
        assert {d["name"] for d in all_years["cash_deposits"]} == set(names)
        assert sum(d["total_usd"] for d in all_years["cash_deposits"]) == 2000
        return {"receipt_month_independent": True, "cross_year": True,
                "full_distribution": [800, 100, 100], "draft_cancelled_excluded": True,
                "rolled_back": True}
    finally:
        frappe.db.rollback()
