"""Transactional Frappe smoke for cross-year deposits and historical routing.

With a test site's Frappe connection active, call ``run()`` from bench console
or a Python process with this repository on PYTHONPATH. The smoke inserts
test-only rows and rolls the transaction back in all cases.
"""

import json
from uuid import uuid4

import frappe
from frappe.utils import now_datetime

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
    get_control_data,
)


def run():
    periods = frappe.get_all(
        "CN Reconciliation Period",
        filters={"payroll_month": "2026-12-01"},
        fields=["name", "employer"], limit_page_length=1,
    )
    if not periods:
        raise AssertionError("Se requiere un período de diciembre 2026 en el sitio de prueba.")
    period = periods[0]
    prefix = "WQSMOKE-" + uuid4().hex[:12].upper()
    detail_name = prefix + "-DETAIL"
    allocation_name = prefix + "-ALLOC"
    import_name = prefix + "-IMPORT"
    row_name = prefix + "-ROW"
    now = now_datetime()
    try:
        for name, reference, detail_period, allocation_detail in (
            (detail_name, prefix + "-D", period.name, "[]"),
            (allocation_name, prefix + "-A", None,
             json.dumps([{"periodo": period.name, "importe_usd": 10}])),
        ):
            frappe.db.sql(
                """INSERT INTO `tabCN Remittance Allocation`
                (name, owner, creation, modified, modified_by, docstatus, idx, employer,
                 deposit_reference, deposit_date, deposit_currency, deposit_amount, amount_usd,
                 detail_period, detail_count, detail_status, detail_file, allocated_usd,
                 unallocated_usd, justified_surplus_usd, unclassified_usd, allocation_detail,
                 notes, result)
                VALUES (%s, 'Administrator', %s, %s, 'Administrator', 1, 0, %s,
                        %s, '2027-01-15', 'USD', 40, 40, %s, 1, 'Cargado',
                        '/private/files/smoke.xlsx', 0, 40, 0, 40, %s,
                        'Smoke transaccional', 'Parcial')""",
                (name, now, now, period.employer, reference, detail_period,
                 allocation_detail),
            )
        frappe.db.sql(
            """INSERT INTO `tabCN Accounting Import`
            (name, owner, creation, modified, modified_by, docstatus, idx,
             historical_backfill, status)
            VALUES (%s, 'Administrator', %s, %s, 'Administrator', 0, 0,
                    0, 'Importado')""",
            (import_name, now, now),
        )
        frappe.db.sql(
            """INSERT INTO `tabCN Source Row`
            (name, parent, parenttype, parentfield, idx, owner, creation, modified,
             modified_by, docstatus, event_type, event_date, effective, reference,
             amount, amount_usd, currency)
            VALUES (%s, %s, 'CN Accounting Import', 'rows', 1, 'Administrator',
                    %s, %s, 'Administrator', 0, 'Aplicacion', '2026-08-15', 1,
                    %s, 25, 25, 'USD')""",
            (row_name, import_name, now, now, prefix + "-H"),
        )

        data = get_control_data(year=2026)
        for reference in (prefix + "-D", prefix + "-A"):
            item = next(
                (item for item in data["work_items"] if reference in item["summary"]),
                None,
            )
            assert item is not None, reference
            assert item["kind"] == "unassigned_deposit", item
            assert item["period"] == period.name, item
        assert prefix + "-H" in {
            row.reference for row in data["unassigned_historical_applications"]
        }
        assert prefix + "-H" not in {
            row.reference for row in data["unassigned_operational_applications"]
        }
        return "Smoke OK: enero→diciembre y aplicación histórica sin bandera"
    finally:
        frappe.db.rollback()
        assert not frappe.db.exists("CN Remittance Allocation", detail_name)
        assert not frappe.db.exists("CN Remittance Allocation", allocation_name)
        assert not frappe.db.exists("CN Accounting Import", import_name)
