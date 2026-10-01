"""Synthetic April–June 2025 historical reconciliation on copilot-demo.local.

The three employers respectively make partial, no, and excess payments.
No real bank or core files are imported: source and detail paths are clearly
marked as simulated while the actual Frappe documents exercise reconciliation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
    reconcile_all_sources,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING


TEST_SITE = "copilot-demo.local"
MONTHS = (
    ("2025-04-01", "2025-05-05", "2025-05-12", (60, 40)),
    ("2025-05-01", "2025-06-05", "2025-06-13", (72, 48)),
    ("2025-06-01", "2025-07-05", "2025-07-15", (54, 36)),
)
EMPLOYERS = (
    ("Simulación histórica 2025 - Pago parcial", "SIM-H25-PAR", "P", "partial"),
    ("Simulación histórica 2025 - Sin pago", "SIM-H25-NOP", "N", "unpaid"),
    ("Simulación histórica 2025 - Pago de más", "SIM-H25-EXC", "E", "excess"),
)


def _assert_test_site():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Esta simulación solo puede ejecutarse en {TEST_SITE}.")


def _reference(letter, month):
    return f"SIM-H25-{letter}-{month[:7].replace('-', '')}"


def _client_fields(letter, index):
    return {
        "client_number": f"H25{letter}{index:03d}",
        "employee_number": f"SIM-{letter}-{index:03d}",
        "client_name": f"Cliente Simulado {letter} {index}",
        "national_id": f"SIM-H25-{letter}-{index:04d}",
        "loan_number": f"H25-{letter}-{index:03d}",
    }


def _preflight():
    for employer, code, _letter, _scenario in EMPLOYERS:
        if frappe.db.exists("CN Employer", employer) or frappe.db.exists(
            "CN Employer", {"employer_code": code}
        ):
            raise RuntimeError(
                f"Ya existe {employer}; no se duplicará ni modificará la simulación."
            )


def _create_employer_and_clients(employer, code, letter):
    frappe.get_doc({
        "doctype": "CN Employer",
        "employer_name": employer,
        "employer_code": code,
        "payroll_frequency": "Mensual",
        "notes": "SIMULACIÓN HISTÓRICA. Empresa ficticia; no corresponde a cobros reales.",
    }).insert()
    for index in (1, 2):
        frappe.get_doc({
            "doctype": "CN Client",
            "employer": employer,
            **{key: value for key, value in _client_fields(letter, index).items()
               if key != "loan_number"},
        }).insert()


def _create_period_and_applications(employer, letter, month, applied_on, amounts):
    reference = _reference(letter, month)
    period = frappe.get_doc({
        "doctype": "CN Reconciliation Period",
        "employer": employer,
        "payroll_month": month,
        "reconciliation_mode": "Historica",
        "historical_scope": "Mensual",
        "notes": (
            "SIMULACIÓN HISTÓRICA. Solo aplicaciones del core frente a depósitos; "
            "no se presume cobranza enviada ni deducción al empleado."
        ),
    }).insert()
    source = frappe.get_doc({
        "doctype": "CN Accounting Import",
        "source_type": SOURCE_ACCOUNTING,
        "source_file": f"/private/files/{reference}-SIMULADO-sin-archivo.xlsx",
        "historical_backfill": 1,
        "historical_period": period.name,
        "status": "Importado",
        "notes": "SIMULACIÓN: dos aplicaciones ficticias; no existe archivo físico.",
    })
    for index, amount in enumerate(amounts, start=1):
        source.append("rows", {
            "source_row": index + 1,
            "source_key": f"{reference}-APP-{index:02d}",
            "event_type": "Aplicacion",
            "event_date": applied_on,
            "reference": reference,
            "accounting_entry": f"AS-{reference}-{index:02d}",
            "receipt": f"REC-{reference}-{index:02d}",
            "employer_text": employer,
            **_client_fields(letter, index),
            "installment_number": int(month[5:7]) - 3,
            "currency": "USD",
            "amount": amount,
            "amount_usd": amount,
            "processing_route": "Historica",
            "historical_period": period.name,
            "effective": 1,
        })
    source.insert()
    return period


def _create_deposit_and_detail(employer, letter, period, month, paid_on, amounts, scenario):
    reference = _reference(letter, month)
    paid_amounts = amounts[:1] if scenario == "partial" else amounts
    allocated = sum(paid_amounts)
    extra = 10 if scenario == "excess" else 0
    deposit_date = date.fromisoformat(paid_on)
    deposit = frappe.get_doc({
        "doctype": "CN Remittance Allocation",
        "employer": employer,
        "deposit_reference": reference,
        "deposit_voucher": f"DEP-{reference}",
        "deposit_date": paid_on,
        "deposit_currency": "USD",
        "deposit_amount": allocated + extra,
        "detail_period": period.name,
        "notes": (
            "SIMULACIÓN HISTÓRICA: depósito ficticio posterior a la aplicación. "
            "El detalle por cliente se registra dos días después."
        ),
    }).insert()
    deposit.submit()
    deposit = frappe.get_doc("CN Remittance Allocation", deposit.name)
    for index, amount in enumerate(paid_amounts, start=1):
        deposit.append("detail_rows", {
            "source_row": index + 1,
            **_client_fields(letter, index),
            "application_reference": reference,
            "deducted_usd": amount,
        })
    deposit.detail_file = f"/private/files/{reference}-DETALLE-SIMULADO-sin-archivo.xlsx"
    deposit.detail_source_file = deposit.detail_file
    deposit.detail_hash = f"simulated-{reference}"
    deposit.detail_count = len(paid_amounts)
    deposit.detail_imported_on = datetime.combine(deposit_date + timedelta(days=2), time(10))
    deposit.save()
    reconcile_all_sources()
    if extra:
        surplus = frappe.get_doc({
            "doctype": "CN Complementary Item",
            "category": "Saldo a favor de la empresa", "currency": "USD", "posting_date": deposit_date,
            "period": period.name,
            "registered_deposit": deposit.name,
            "reference": reference,
            "amount": extra,
            "reason_type": "Error de la empresa",
            "description": (
                "SIMULACIÓN: la empresa remitió US$10 adicionales. Se conserva como "
                "saldo a favor documentado, sin aplicarlo a ningún crédito."
            ),
        }).insert()
        surplus.submit()


def run():
    """Create the nine periods once, verify them, and commit atomically."""
    _assert_test_site()
    frappe.set_user("Administrator")
    _preflight()
    try:
        for employer, code, letter, _scenario in EMPLOYERS:
            _create_employer_and_clients(employer, code, letter)
        for employer, _code, letter, _scenario in EMPLOYERS:
            for month, applied_on, _paid_on, amounts in MONTHS:
                _create_period_and_applications(
                    employer, letter, month, applied_on, amounts
                )
        reconcile_all_sources()
        for employer, _code, letter, scenario in EMPLOYERS:
            if scenario == "unpaid":
                continue
            for month, _applied_on, paid_on, amounts in MONTHS:
                period_name = frappe.db.get_value(
                    "CN Reconciliation Period",
                    {"employer": employer, "payroll_month": month}, "name",
                )
                _create_deposit_and_detail(
                    employer, letter, frappe.get_doc("CN Reconciliation Period", period_name),
                    month, paid_on, amounts, scenario,
                )
        reconcile_all_sources()
        result = audit()
        frappe.db.commit()
        return result
    except Exception:
        frappe.db.rollback()
        raise


def audit():
    """Read-only verification of the three distinct historical outcomes."""
    _assert_test_site()
    frappe.set_user("Administrator")
    from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
        get_control_data,
    )
    from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import (
        execute as reconciliation_summary,
    )

    report = []
    expected_status = {
        "partial": "Parcial",
        "unpaid": "Pendiente",
        "excess": "Conciliado",
    }
    for employer, _code, letter, scenario in EMPLOYERS:
        for month, applied_on, paid_on, amounts in MONTHS:
            period_name = frappe.db.get_value(
                "CN Reconciliation Period",
                {"employer": employer, "payroll_month": month}, "name",
            )
            if not period_name:
                raise AssertionError(f"Falta período {employer} {month}")
            period = frappe.get_doc("CN Reconciliation Period", period_name)
            applied = sum(amounts)
            remitted = amounts[0] if scenario == "partial" else 0 if scenario == "unpaid" else applied
            pending = applied - remitted
            if (
                period.reconciliation_mode != "Historica"
                or period.status != expected_status[scenario]
                or round(period.applied_usd or 0, 4) != applied
                or round(period.remitted_usd or 0, 4) != remitted
            ):
                raise AssertionError({
                    "period": period.name, "status": period.status,
                    "applied": period.applied_usd, "remitted": period.remitted_usd,
                })
            applications = frappe.get_all(
                "CN Source Row",
                filters={"historical_period": period.name, "event_type": "Aplicacion"},
                fields=["event_date", "amount", "historical_balance_usd"],
                limit_page_length=10,
            )
            if (
                len(applications) != 2
                or {str(row.event_date) for row in applications} != {applied_on}
                or round(sum(row.historical_balance_usd or 0 for row in applications), 4) != pending
            ):
                raise AssertionError({"period": period.name, "applications": applications})
            reference = _reference(letter, month)
            deposit_name = frappe.db.get_value(
                "CN Remittance Allocation", {"deposit_reference": reference}, "name",
            )
            if scenario == "unpaid":
                if deposit_name:
                    raise AssertionError(f"No debe haber depósito: {reference}")
                surplus = 0
            else:
                deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
                surplus = 10 if scenario == "excess" else 0
                if (
                    str(deposit.deposit_date) != paid_on
                    or round(deposit.allocated_usd or 0, 4) != remitted
                    or round(deposit.unallocated_usd or 0, 4) != surplus
                    or round(deposit.justified_surplus_usd or 0, 4) != surplus
                    or round(deposit.unclassified_usd or 0, 4) != 0
                    or len(deposit.detail_rows) != (1 if scenario == "partial" else 2)
                ):
                    raise AssertionError({
                        "deposit": deposit.name, "allocated": deposit.allocated_usd,
                        "unallocated": deposit.unallocated_usd,
                        "justified": deposit.justified_surplus_usd,
                        "unclassified": deposit.unclassified_usd,
                        "details": len(deposit.detail_rows),
                    })
                if surplus and not frappe.db.exists("CN Complementary Item", {
                    "category": "Saldo a favor de la empresa",
                    "registered_deposit": deposit_name,
                    "result": "Saldo a favor documentado", "docstatus": 1,
                }):
                    raise AssertionError(f"Falta saldo a favor documentado: {reference}")
            report.append({
                "empresa": employer,
                "mes": month[:7],
                "escenario": scenario,
                "periodo": period.name,
                "aplicado_usd": applied,
                "depositado_usd": remitted + surplus,
                "conciliado_usd": remitted,
                "pendiente_usd": pending,
                "saldo_a_favor_usd": surplus,
                "estado": period.status,
            })
    dashboard = get_control_data(year=2025)
    dashboard_periods = {
        item["name"] for item in dashboard["periods"]
        if item["employer"] in {employer for employer, *_rest in EMPLOYERS}
    }
    report_columns, report_rows = reconciliation_summary({
        "reconciliation_mode": "Historica",
        "from_month": "2025-04-01",
        "to_month": "2025-06-01",
    })
    if (
        len(dashboard_periods) != 9
        or len(report_rows) != 9
        or not report_columns
        or round(dashboard["totals"].get("applied_usd", 0), 4) != 930
        or round(dashboard["totals"].get("remitted_usd", 0), 4) != 496
        or round(dashboard["totals"].get("historical_pending_usd", 0), 4) != 434
        or round(dashboard["totals"].get("documented_credit_usd", 0), 4) != 30
        or round(dashboard["totals"].get("worker_gap_usd", 0), 4) != 0
    ):
        raise AssertionError({
            "dashboard": dashboard["totals"],
            "dashboard_periods": len(dashboard_periods),
            "report_rows": len(report_rows),
        })
    return {
        "site": TEST_SITE,
        "employers": len(EMPLOYERS),
        "periods": len(report),
        "applications": len(report) * 2,
        "deposits": sum(item[3] != "unpaid" for item in EMPLOYERS) * len(MONTHS),
        "totals": {
            "applied_usd": sum(row["aplicado_usd"] for row in report),
            "deposited_usd": sum(row["depositado_usd"] for row in report),
            "reconciled_usd": sum(row["conciliado_usd"] for row in report),
            "pending_usd": sum(row["pendiente_usd"] for row in report),
            "documented_credit_usd": sum(row["saldo_a_favor_usd"] for row in report),
        },
        "rows": report,
    }
