"""Validate NIO deposit conversion without inserting a bank movement."""

import frappe


TEST_SITE = "cn-reconciliation-test.local"


def run():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    meta = frappe.get_meta("CN Remittance Allocation")
    if meta.has_field("fx_evidence"):
        raise AssertionError("El campo fx_evidence sigue visible en la remesa.")
    if any(meta.has_field(field) for field in (
        "target_section", "period", "row_key", "historical_application",
        "complementary_item",
    )):
        raise AssertionError("Persisten campos del destino individual antiguo.")
    if not meta.get_field("deposit_date").reqd:
        raise AssertionError("La fecha del depósito no es obligatoria.")
    if not frappe.db.exists(
        "Patch Log",
        {"patch": "credinomina_reconciliation.patches.v1_0.move_remittance_fx_evidence_to_notes"},
    ):
        raise AssertionError("No se ejecutó el parche de evidencia cambiaria anterior.")
    deposit = frappe.get_doc({
        "doctype": "CN Remittance Allocation",
        "employer": "Simulación Convenio Alfa",
        "deposit_reference": "CN-TEST-CURRENCY-NO-INSERT",
        "deposit_date": "2026-12-05",
        "deposit_currency": "NIO",
        "deposit_amount": 3650,
        "fx_rate": 36.5,
        "amount_usd": 999,
        "notes": "Tasa según comprobante bancario del ensayo; validación sin guardar.",
    })
    deposit.validate()
    if round(deposit.amount_usd, 4) != 100:
        raise AssertionError({"converted_usd": deposit.amount_usd})
    deposit.notes = "Validación sin guardar."
    try:
        deposit.validate()
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("Un depósito C$ sin fuente de tasa documentada fue aceptado.")
    deposit.notes = "Tasa según comprobante bancario del ensayo."
    deposit.fx_rate = 73
    deposit.amount_usd = 999
    deposit.validate()
    if round(deposit.amount_usd, 4) != 50:
        raise AssertionError("El equivalente no siguió el cambio de tasa.")
    deposit.deposit_currency = "USD"
    deposit.deposit_amount = 46.53
    deposit.amount_usd = 999
    deposit.validate()
    if round(deposit.amount_usd, 4) != 46.53:
        raise AssertionError("El equivalente USD no siguió el importe del depósito.")
    deposit.deposit_date = None
    try:
        deposit.validate()
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("Se aceptó una remesa sin fecha de depósito.")
    return {
        "deposit_nio": 3650, "rate": 36.5, "converted_usd": 100,
        "revised_rate_usd": 50, "deposit_usd": 46.53,
    }


def run_submitted_amount_test():
    """Recalculate a submitted NIO deposit, then roll the test transaction back."""
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    frappe.db.savepoint("cn_amount_usd_smoke")
    try:
        document = frappe.get_doc({
            "doctype": "CN Remittance Allocation",
            "employer": "Simulación Convenio Alfa",
            "deposit_reference": f"CN-TEST-AMOUNT-{frappe.generate_hash(length=10)}",
            "deposit_date": "2026-12-05",
            "deposit_currency": "NIO",
            "deposit_amount": 3650,
            "fx_rate": 36.5,
            "notes": "Tasa según comprobante bancario del ensayo; sin persistencia.",
        }).insert()
        document.submit()
        document.reload()
        if round(document.amount_usd, 4) != 100:
            raise AssertionError({"before_rate_change": document.amount_usd})
        document.fx_rate = 73
        document.save()
        document.reload()
        if round(document.amount_usd, 4) != 50:
            raise AssertionError({"after_rate_change": document.amount_usd})
        return {"submitted_usd": 100, "updated_usd": 50, "rolled_back": True}
    finally:
        frappe.db.rollback(save_point="cn_amount_usd_smoke")
