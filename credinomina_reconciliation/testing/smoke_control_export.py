"""Read-only demo-site smoke for the control-page Excel download."""

from __future__ import annotations

import io

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
    export_control_excel,
)


def run(year=2025, employer=None):
    frappe.local.response = frappe._dict()
    export_control_excel(year=year, employer=employer)
    response = frappe.local.response
    assert response.type == "download"
    assert response.filename.endswith(".xlsx")
    book = load_workbook(io.BytesIO(response.filecontent), read_only=True)
    assert book.sheetnames == [
        "Resumen", "Detalle cliente", "Cruces", "Partidas y excepciones", "Gestiones",
    ]
    summary = book["Resumen"]
    period_rows = list(summary.iter_rows(min_row=20, values_only=True))
    assert period_rows, "No se encontraron períodos de prueba."
    if employer:
        names = {row[1] for row in period_rows}
        assert len(names) == 1, "El filtro de empresa mostró otros convenios."
    historical = [row for row in period_rows if row[3] == "Histórica"]
    assert historical, "Falta cobertura histórica."
    assert all(row[9] == "N/D" and row[10] == "N/D" for row in historical)
    detail_count = max(book["Detalle cliente"].max_row - 4, 0)
    link_count = max(book["Cruces"].max_row - 4, 0)
    assert detail_count > 0
    return {
        "filename": response.filename,
        "periods": len(period_rows),
        "historical_periods": len(historical),
        "detail_rows": detail_count,
        "linked_rows": link_count,
        "issue_rows": max(book["Partidas y excepciones"].max_row - 4, 0),
        "action_rows": max(book["Gestiones"].max_row - 4, 0),
        "bytes": len(response.filecontent),
    }


def run_scoped(year=2025):
    period = frappe.get_all(
        "CN Reconciliation Period",
        filters={"payroll_month": ["between", [f"{year}-01-01", f"{year}-12-31"]]},
        fields=["employer"], limit_page_length=1,
    )[0]
    return run(year=year, employer=period.employer)
