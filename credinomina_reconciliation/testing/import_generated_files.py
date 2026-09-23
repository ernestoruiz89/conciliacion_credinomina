"""Run the four real import actions against artifact-generated test workbooks."""

from __future__ import annotations

from pathlib import Path

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    import_collection, import_employer_response,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import (
    import_remittance_detail,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    import_source_file,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING


TEST_SITE = "cn-reconciliation-test.local"
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tmp" / "spreadsheets"
EMPLOYER = "Simulación Convenio Alfa"
MONTH = "2026-12-01"
REFERENCE = "CN-SIM-A-202612"


def _attach(path: Path, document):
    file_doc = save_file(
        path.name, path.read_bytes(), document.doctype, document.name,
        is_private=1,
    )
    return file_doc.file_url


def run():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    planilla = FIXTURES / "planilla_A_dic_2026.xlsx"
    movements = FIXTURES / "movimientos_A_dic_2026.xlsx"
    if not planilla.is_file() or not movements.is_file():
        raise RuntimeError("Genere antes los dos libros en tmp/spreadsheets.")
    if frappe.db.exists("CN Reconciliation Period", {"employer": EMPLOYER, "payroll_month": MONTH}):
        raise RuntimeError("El período de prueba diciembre 2026 ya existe.")

    period = frappe.get_doc({
        "doctype": "CN Reconciliation Period", "employer": EMPLOYER,
        "payroll_month": MONTH, "reconciliation_mode": "Operativa",
        "collection_cycle": "Mensual", "notes": "Prueba de importación real de archivos.",
    }).insert()
    period.collection_file = _attach(planilla, period)
    period.save()
    collection_result = import_collection(period.name)
    period = frappe.get_doc("CN Reconciliation Period", period.name)
    if len(period.collection_rows) != 20 or period.status != "Cobranza cargada":
        raise AssertionError("La cobranza no quedó cargada sin conciliación.")
    if frappe.db.count("CN Client Alias", {"parent": period.collection_rows[1].client}) != 1:
        raise AssertionError("No se reconoció la variante verificada de nombre.")

    period.employer_response_file = _attach(planilla, period)
    period.deduction_evidence_date = "2026-12-31"
    period.save()
    deduction_result = import_employer_response(period.name)
    if deduction_result["matched"] != 20 or deduction_result["unmatched"]:
        raise AssertionError(deduction_result)

    source = frappe.get_doc({
        "doctype": "CN Source Import", "source_type": SOURCE_ACCOUNTING,
        "source_file": "/private/files/pendiente-simulacion.xlsx",
    }).insert()
    source.source_file = _attach(movements, source)
    source.save()
    application_result = import_source_file(source.name)
    source = frappe.get_doc("CN Source Import", source.name)
    if len(source.rows) != 19 or any(row.match_status != "Conciliado" for row in source.rows):
        raise AssertionError("Las 19 aplicaciones no enlazaron con la cobranza.")

    deposit = frappe.get_doc({
        "doctype": "CN Remittance Allocation", "employer": EMPLOYER,
        "deposit_reference": REFERENCE, "deposit_date": "2027-01-12",
        "deposit_currency": "USD", "deposit_amount": 920.5,
        "notes": "Depósito registrado antes de recibir el detalle por cliente.",
    }).insert()
    deposit.submit()
    deposit = frappe.get_doc("CN Remittance Allocation", deposit.name)
    if deposit.detail_status != "Detalle pendiente":
        raise AssertionError(f"Se esperaba detalle pendiente: {deposit.detail_status}")
    deposit.detail_file = _attach(planilla, deposit)
    deposit.save()
    detail_result = import_remittance_detail(deposit.name)
    deposit = frappe.get_doc("CN Remittance Allocation", deposit.name)
    if (
        len(deposit.detail_rows) != 20
        or deposit.detail_status != "Conciliado"
        or round(deposit.allocated_usd, 4) != 920.5
        or round(deposit.unallocated_usd, 4) != 0
        or not deposit.detail_imported_on
    ):
        raise AssertionError({
            "detail_rows": len(deposit.detail_rows),
            "detail_status": deposit.detail_status,
            "allocated_usd": deposit.allocated_usd,
            "unallocated_usd": deposit.unallocated_usd,
        })
    # The employee with no deduction is retained as a documented zero row.
    if sum(row.match_status == "No deducido" for row in deposit.detail_rows) != 1:
        raise AssertionError("No se conservó la fila no deducida.")
    applications = frappe.get_all(
        "CN Source Row",
        filters={"reference": REFERENCE, "event_type": "Aplicacion"},
        fields=["name", "match_status", "deposit_match_status"],
        limit_page_length=100,
    )
    if len(applications) != 19 or any(
        row.match_status != "Conciliado"
        or row.deposit_match_status != "Remesa conciliada"
        for row in applications
    ):
        raise AssertionError({"application_states": applications})
    frappe.db.commit()
    return {
        "period": period.name, "collection": collection_result,
        "deduction": deduction_result, "applications": application_result,
        "deposit": deposit.name, "detail": detail_result,
        "detail_status": deposit.detail_status,
        "allocated_usd": deposit.allocated_usd,
        "remitted_applications": len(applications),
    }


def audit():
    """Read-only check for the persisted late-detail import scenario."""
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import (
        execute as aging_report,
    )
    periods = frappe.get_all(
        "CN Reconciliation Period",
        filters={"employer": EMPLOYER, "payroll_month": MONTH},
        pluck="name", limit_page_length=2,
    )
    deposits = frappe.get_all(
        "CN Remittance Allocation",
        filters={"employer": EMPLOYER, "deposit_reference": REFERENCE},
        pluck="name", limit_page_length=2,
    )
    applications = frappe.get_all(
        "CN Source Row",
        filters={"reference": REFERENCE, "event_type": "Aplicacion"},
        fields=["match_status", "deposit_match_status"], limit_page_length=100,
    )
    if len(periods) != 1 or len(deposits) != 1 or len(applications) != 19:
        raise AssertionError("No se encontró una única prueba completa de diciembre.")
    period = frappe.get_doc("CN Reconciliation Period", periods[0])
    deposit = frappe.get_doc("CN Remittance Allocation", deposits[0])
    if (
        len(period.collection_rows) != 20
        or len(deposit.detail_rows) != 20
        or deposit.detail_status != "Conciliado"
        or round(deposit.allocated_usd, 4) != 920.5
        or not deposit.detail_imported_on
        or any(
            row.match_status != "Conciliado"
            or row.deposit_match_status != "Remesa conciliada"
            for row in applications
        )
    ):
        raise AssertionError("La conciliación tardía no persistió correctamente.")
    _columns, aging_rows, _message, _chart, aging_summary = aging_report({
        "as_of_date": "2027-02-01", "employer": EMPLOYER,
        "from_month": MONTH, "to_month": MONTH,
    })
    if (
        len(aging_rows) != 2
        or len(aging_summary) != 1
        or aging_summary[0]["label"] != "Cuota no deducida al trabajador"
        or aging_summary[0]["value"] != 89.5
    ):
        raise AssertionError("El faltante del trabajador no quedó separado de la remesa.")
    return {
        "collection_month": str(period.payroll_month),
        "application_date": "2027-01-05",
        "deposit_date": str(deposit.deposit_date),
        "detail_import_recorded": True,
        "collection_rows": len(period.collection_rows),
        "application_rows": len(applications),
        "detail_rows": len(deposit.detail_rows),
        "deposit_status": deposit.detail_status,
        "allocated_usd": deposit.allocated_usd,
        "employee_shortfall_usd": aging_summary[0]["value"],
    }
