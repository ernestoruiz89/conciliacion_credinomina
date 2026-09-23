"""Live historical cut: two core applications and one later detailed deposit."""

from datetime import datetime

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    reconcile_all_sources,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING


TEST_SITE = "cn-reconciliation-test.local"
EMPLOYER = "Simulación Convenio Alfa"
REFERENCE = "CN-HIST-A-202606"
MONTH = "2026-06-01"


def _assert_test_site():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")


def run():
    _assert_test_site()
    frappe.set_user("Administrator")
    if frappe.db.exists("CN Reconciliation Period", {
        "employer": EMPLOYER, "payroll_month": MONTH,
    }):
        raise RuntimeError("El corte histórico de ensayo ya existe.")
    period = frappe.get_doc({
        "doctype": "CN Reconciliation Period", "employer": EMPLOYER,
        "payroll_month": MONTH, "reconciliation_mode": "Historica",
        "historical_scope": "Fecha exacta",
        "historical_application_date": "2026-07-05",
        "notes": "Corte histórico sintético sin reconstruir cobranza.",
    }).insert()
    source = frappe.get_doc({
        "doctype": "CN Source Import", "source_type": SOURCE_ACCOUNTING,
        "source_file": "/private/files/CN-HIST-A-202606-simulado.xlsx",
        "historical_backfill": 1, "historical_period": period.name,
        "status": "Importado", "notes": "Dos aplicaciones históricas sintéticas.",
    })
    for index, amount in ((1, 41.0), (2, 42.0)):
        source.append("rows", {
            "source_row": index + 1,
            "source_key": f"{REFERENCE}-APP-{index:02d}",
            "event_type": "Aplicacion", "event_date": "2026-07-05",
            "reference": REFERENCE,
            "accounting_entry": f"AS-H-{index:02d}",
            "receipt": f"REC-H-{index:02d}",
            "employer_text": EMPLOYER,
            "client_number": f"1{index:03d}",
            "client_name": f"Cliente Prueba A {index:02d}",
            "national_id": f"SIM-A-{index:04d}",
            "loan_number": f"19{index:04d}",
            "currency": "USD", "amount": amount, "amount_usd": amount,
            "processing_route": "Historica", "historical_period": period.name,
            "effective": 1,
        })
    source.insert()
    reconcile_all_sources()
    source = frappe.get_doc("CN Source Import", source.name)
    if any(row.deposit_match_status == "Remesa conciliada" for row in source.rows):
        raise AssertionError("No debe aparecer una remesa antes del depósito.")

    deposit = frappe.get_doc({
        "doctype": "CN Remittance Allocation", "employer": EMPLOYER,
        "deposit_reference": REFERENCE, "deposit_date": "2026-07-20",
        "deposit_currency": "USD", "deposit_amount": 83,
        "detail_period": period.name,
        "notes": "Depósito histórico: el detalle por cliente llegará después.",
    }).insert()
    deposit.submit()
    deposit = frappe.get_doc("CN Remittance Allocation", deposit.name)
    if deposit.detail_status != "Detalle pendiente":
        raise AssertionError({"before_detail": deposit.detail_status})
    for index, amount in ((1, 41.0), (2, 42.0)):
        deposit.append("detail_rows", {
            "source_row": index + 1,
            "client_number": f"1{index:03d}",
            "client_name": f"Cliente Prueba A {index:02d}",
            "national_id": f"SIM-A-{index:04d}",
            "loan_number": f"19{index:04d}",
            "application_reference": REFERENCE,
            "deducted_usd": amount,
        })
    deposit.detail_file = f"/private/files/{REFERENCE}-detalle-simulado.xlsx"
    deposit.detail_source_file = deposit.detail_file
    deposit.detail_hash = f"simulated-{REFERENCE}"
    deposit.detail_count = 2
    deposit.detail_imported_on = datetime(2026, 7, 24, 10)
    deposit.save()
    reconcile_all_sources()
    deposit = frappe.get_doc("CN Remittance Allocation", deposit.name)
    source = frappe.get_doc("CN Source Import", source.name)
    period = frappe.get_doc("CN Reconciliation Period", period.name)
    if (
        deposit.detail_status != "Conciliado"
        or round(deposit.allocated_usd, 4) != 83
        or any(row.deposit_match_status != "Remesa conciliada" for row in source.rows)
        or period.status != "Historico conciliado"
    ):
        raise AssertionError({
            "detail_status": deposit.detail_status,
            "allocated_usd": deposit.allocated_usd,
            "application_statuses": [row.deposit_match_status for row in source.rows],
            "period_status": period.status,
        })
    frappe.db.commit()
    return {
        "historical_period": period.name, "applications": len(source.rows),
        "deposit": deposit.name, "detail_status": deposit.detail_status,
        "period_status": period.status,
    }


def audit():
    _assert_test_site()
    from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import (
        execute,
    )

    _columns, rows = execute({
        "employer": EMPLOYER, "reconciliation_mode": "Historica",
        "from_month": MONTH, "to_month": MONTH,
    })
    if (
        len(rows) != 1
        or rows[0].status != "Historico conciliado"
        or round(rows[0].applied_usd, 4) != 83
        or round(rows[0].remitted_usd, 4) != 83
        or round(rows[0].historical_pending_usd, 4) != 0
    ):
        raise AssertionError({"historical_summary": rows})
    return {
        "period": rows[0].name,
        "cut": rows[0].historical_label,
        "applied_usd": rows[0].applied_usd,
        "remitted_usd": rows[0].remitted_usd,
        "pending_usd": rows[0].historical_pending_usd,
    }
