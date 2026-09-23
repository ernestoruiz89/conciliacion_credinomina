"""Validate NIO deposit conversion without inserting a bank movement."""

import frappe


TEST_SITE = "cn-reconciliation-test.local"


def run():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    deposit = frappe.get_doc({
        "doctype": "CN Remittance Allocation",
        "employer": "Simulación Convenio Alfa",
        "deposit_reference": "CN-TEST-CURRENCY-NO-INSERT",
        "deposit_date": "2026-12-05",
        "deposit_currency": "NIO",
        "deposit_amount": 3650,
        "fx_rate": 36.5,
        "fx_evidence": "Tasa documentada del ensayo",
        "notes": "Validación sin guardar.",
    })
    deposit.validate()
    if round(deposit.amount_usd, 4) != 100:
        raise AssertionError({"converted_usd": deposit.amount_usd})
    deposit.fx_evidence = ""
    try:
        deposit.validate()
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("Un depósito C$ sin tasa documentada fue aceptado.")
    return {"deposit_nio": 3650, "rate": 36.5, "converted_usd": 100}
