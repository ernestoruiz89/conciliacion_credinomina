"""Exercise three months of payroll reconciliation on one disposable site.

Run with ``bench --site cn-reconciliation-test.local execute
credinomina_reconciliation.testing.simulate_three_employers.run``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    reconcile_all_sources,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING


TEST_SITE = "cn-reconciliation-test.local"
MONTHS = ("2026-09-01", "2026-10-01", "2026-11-01")
EMPLOYERS = (
    ("Simulación Convenio Alfa", "CN-SIM-A", "A"),
    ("Simulación Convenio Beta", "CN-SIM-B", "B"),
    ("Simulación Convenio Gamma", "CN-SIM-C", "C"),
)


def _assert_test_site():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Simulación limitada al sitio {TEST_SITE}")


def _client_number(letter, index):
    return f"{ord(letter) - 64}{index:03d}"


def _loan_number(letter, index):
    return f"{ord(letter) - 64}9{index:04d}"


def _client_name(letter, index):
    return f"Cliente Prueba {letter} {index:02d}"


def _reference(letter, month):
    return f"CN-SIM-{letter}-{month[0:7].replace('-', '')}"


def _deposit_date(month):
    year, number, _day = (int(part) for part in month.split("-"))
    if number == 12:
        return date(year + 1, 1, 12)
    return date(year, number + 1, 12)


def _get_or_create_employer(name, code):
    if not frappe.db.exists("CN Employer", name):
        frappe.get_doc({
            "doctype": "CN Employer", "employer_name": name,
            "employer_code": code, "payroll_frequency": "Mensual",
            "grace_days": 10,
        }).insert()
    return name


def _get_or_create_clients(letter, employer):
    clients = {}
    for index in range(1, 21):
        number = _client_number(letter, index)
        existing = frappe.db.get_value("CN Client", {"client_number": number}, "name")
        if existing:
            clients[index] = existing
            continue
        document = frappe.get_doc({
            "doctype": "CN Client", "employer": employer,
            "client_name": _client_name(letter, index),
            "client_number": number, "national_id": f"SIM-{letter}-{index:04d}",
        })
        document.insert()
        clients[index] = document.name
    return clients


def _get_or_create_period(employer, letter, month, month_index, clients):
    existing = frappe.db.get_value(
        "CN Reconciliation Period",
        {"employer": employer, "payroll_month": month}, "name",
    )
    if existing:
        return frappe.get_doc("CN Reconciliation Period", existing)
    reference = _reference(letter, month)
    document = frappe.get_doc({
        "doctype": "CN Reconciliation Period", "employer": employer,
        "payroll_month": month, "reconciliation_mode": "Operativa",
        "collection_cycle": "Mensual", "status": "Cobranza cargada",
        "deduction_basis": "Detalle de empresa",
        "deduction_evidence_date": f"{month[:7]}-30" if month_index == 1 else f"{month[:7]}-28",
        "notes": "Datos sintéticos de la prueba de conciliación.",
    })
    for index in range(1, 21):
        expected = 40 + index
        deducted = expected if index <= 18 else (expected / 2 if index == 19 else 0)
        document.append("collection_rows", {
            "row_key": f"{reference}-{index:02d}", "source_row": index + 1,
            "client": clients[index], "client_number": _client_number(letter, index),
            "client_name": _client_name(letter, index),
            "national_id": f"SIM-{letter}-{index:04d}",
            "loan_number": _loan_number(letter, index),
            "installment_number": month_index + 1,
            "total_installments": 12,
            "expected_usd": expected, "expected_nio": expected * 36.5,
            "deducted_usd": deducted,
            "deducted_nio": deducted * 36.5,
            "deduction_currency": "USD",
            "deduction_status": (
                "Deduccion total" if index <= 18
                else "Deduccion parcial" if index == 19 else "No deducido"
            ),
            "deduction_evidence_date": document.deduction_evidence_date,
            "application_reference": reference,
            "application_status": "Pendiente",
        })
    document.insert()
    return document


def _get_or_create_import(employer, letter, month, month_index):
    marker = f"/private/files/{_reference(letter, month)}-simulado.xlsx"
    existing = frappe.db.get_value("CN Source Import", {"source_file": marker}, "name")
    if existing:
        return frappe.get_doc("CN Source Import", existing)
    reference = _reference(letter, month)
    source_date = _deposit_date(month).replace(day=5)
    document = frappe.get_doc({
        "doctype": "CN Source Import", "source_type": SOURCE_ACCOUNTING,
        "source_file": marker, "status": "Importado",
        "notes": "Aplicaciones sintéticas; sin archivo físico.",
    })
    for index in range(1, 20):
        amount = 40 + index if index <= 18 else (40 + index) / 2
        document.append("rows", {
            "source_row": index + 1,
            "source_key": f"{reference}-APP-{index:02d}",
            "event_type": "Aplicacion", "event_date": source_date,
            "reference": reference, "voucher": f"AS-{letter}-{month_index + 1}-{index:02d}",
            "accounting_entry": f"AS-{letter}-{month_index + 1}-{index:02d}",
            "receipt": f"REC-{letter}-{month_index + 1}-{index:02d}",
            "employer_text": employer,
            "client_name": _client_name(letter, index),
            "client_number": _client_number(letter, index),
            "national_id": f"SIM-{letter}-{index:04d}",
            "loan_number": _loan_number(letter, index),
            "installment_number": month_index + 1,
            "currency": "USD", "amount": amount, "amount_usd": amount,
            "processing_route": "Operativa", "effective": 1,
            "match_status": "Pendiente", "deposit_match_status": "Pendiente",
        })
    document.insert()
    return document


def _get_or_create_deposit(employer, letter, month):
    reference = _reference(letter, month)
    existing = frappe.db.get_value(
        "CN Remittance Allocation",
        {"deposit_reference": reference, "employer": employer}, "name",
    )
    if existing:
        return frappe.get_doc("CN Remittance Allocation", existing)
    amount = sum(40 + index for index in range(1, 19)) + (40 + 19) / 2
    document = frappe.get_doc({
        "doctype": "CN Remittance Allocation", "employer": employer,
        "deposit_reference": reference, "deposit_date": _deposit_date(month),
        "deposit_currency": "USD", "deposit_amount": amount,
        "notes": "Depósito sintético; detalle de clientes llega después.",
    })
    document.insert()
    document.submit()
    return frappe.get_doc("CN Remittance Allocation", document.name)


def _add_late_detail(document, letter, month, clients):
    simulated_import = datetime.combine(
        _deposit_date(month) + timedelta(days=3), time(10),
    )
    if document.detail_hash:
        if (
            document.detail_hash == f"simulated-{_reference(letter, month)}"
            and not document.detail_imported_on
        ):
            document.detail_imported_on = simulated_import
            document.save()
        return document
    reference = _reference(letter, month)
    for index in range(1, 20):
        amount = 40 + index if index <= 18 else (40 + index) / 2
        document.append("detail_rows", {
            "source_row": index + 1,
            "row_key": f"{reference}-{index:02d}",
            "client": clients[index], "identity_reason": "Identificador exacto",
            "client_name": _client_name(letter, index),
            "client_number": _client_number(letter, index),
            "national_id": f"SIM-{letter}-{index:04d}",
            "loan_number": _loan_number(letter, index),
            "installment_number": MONTHS.index(month) + 1,
            "application_reference": reference,
            "deducted_usd": amount,
        })
    document.detail_file = f"/private/files/{reference}-detalle-simulado.xlsx"
    document.detail_source_file = document.detail_file
    document.detail_hash = f"simulated-{reference}"
    document.detail_count = 19
    document.detail_imported_on = simulated_import
    document.save()
    return document


def run():
    _assert_test_site()
    frappe.set_user("Administrator")
    for employer, code, letter in EMPLOYERS:
        _get_or_create_employer(employer, code)
        clients = _get_or_create_clients(letter, employer)
        for month_index, month in enumerate(MONTHS):
            _get_or_create_period(employer, letter, month, month_index, clients)
            _get_or_create_import(employer, letter, month, month_index)
    first = reconcile_all_sources()
    for employer, _code, letter in EMPLOYERS:
        clients = _get_or_create_clients(letter, employer)
        for month in MONTHS:
            deposit = _get_or_create_deposit(employer, letter, month)
            if not deposit.detail_hash and (
                deposit.detail_status != "Detalle pendiente"
                or round(deposit.allocated_usd or 0, 4) != 0
            ):
                raise AssertionError({
                    "deposit": deposit.name,
                    "before_detail": deposit.detail_status,
                    "allocated_usd": deposit.allocated_usd,
                })
            if not (letter == "C" and month == MONTHS[-1]):
                _add_late_detail(deposit, letter, month, clients)
    final = reconcile_all_sources()
    simulated_periods = frappe.get_all(
        "CN Reconciliation Period",
        filters={
            "employer": ["in", [row[0] for row in EMPLOYERS]],
            "payroll_month": ["in", MONTHS],
        },
        pluck="name", limit_page_length=100,
    )
    simulated_deposits = frappe.get_all(
        "CN Remittance Allocation",
        filters={"deposit_reference": ["in", [
            _reference(letter, month)
            for _employer, _code, letter in EMPLOYERS for month in MONTHS
        ]]},
        pluck="name", limit_page_length=100,
    )
    counts = {
        "employers": frappe.db.count("CN Employer", {"employer_code": ["like", "CN-SIM-%"]}),
        "clients": frappe.db.count("CN Client", {"national_id": ["like", "SIM-%"]}),
        "periods": len(simulated_periods),
        "collection_rows": frappe.db.count("CN Collection Row", {"parent": ["in", simulated_periods]}),
        "applications": frappe.db.count("CN Source Row", {"event_type": "Aplicacion", "source_key": ["like", "CN-SIM-%"]}),
        "deposits": len(simulated_deposits),
        "detail_rows": frappe.db.count("CN Remittance Detail", {"parent": ["in", simulated_deposits]}),
    }
    assert counts == {
        "employers": 3, "clients": 60, "periods": 9,
        "collection_rows": 180, "applications": 171,
        "deposits": 9, "detail_rows": 152,
    }, counts
    frappe.db.commit()
    return {"counts": counts, "before_deposits": first, "after_late_detail": final}


def audit():
    """Verify final balances and the customer/company aging report in the test site."""
    _assert_test_site()
    frappe.set_user("Administrator")
    from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import (
        execute,
    )
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo.estado_de_cuenta_operativo import (
        execute as account_statement,
    )
    from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import (
        execute as reconciliation_summary,
    )
    from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
        get_control_data,
    )

    _columns, rows, _message, _chart, summary = execute({
        "as_of_date": "2027-02-01", "to_month": "2026-11-01",
    })
    totals = {item["label"]: item["value"] for item in summary}
    assert totals["CxC a empleados (cuota no deducida)"] == 805.5, totals
    assert totals["Deducido sin remesa asignada"] == 920.5, totals
    assert len(rows) == 37, len(rows)
    assert all(abs(sum(row[key] for key in (
        "not_due", "days_1_30", "days_31_60", "days_61_90",
        "days_over_90", "without_date",
    )) - row["amount_usd"]) < 0.00005 for row in rows)
    company = [row for row in rows if row["balance_type"] == "Deducido sin remesa asignada"]
    assert len(company) == 19 and all(row["days_31_60"] > 0 for row in company)
    _columns, beta_rows, _message, _chart, beta_summary = execute({
        "as_of_date": "2027-02-01", "to_month": "2026-11-01",
        "employer": "Simulación Convenio Beta",
    })
    assert len(beta_rows) == 6 and beta_summary[0]["value"] == 268.5
    _columns, client_rows, _message, _chart, client_summary = execute({
        "as_of_date": "2027-02-01", "to_month": "2026-11-01",
        "client_number": "1019",
    })
    assert len(client_rows) == 3 and client_summary[0]["value"] == 88.5
    month_filter = {"from_month": "2026-09-01", "to_month": "2026-11-01"}
    _summary_columns, summary_rows = reconciliation_summary(month_filter)
    _account_columns, account_rows = account_statement(month_filter)
    assert len(summary_rows) == 9, len(summary_rows)
    assert len(account_rows) == 180, len(account_rows)
    assert round(sum(row.employer_receivable_usd for row in account_rows), 4) == 920.5
    dashboard = get_control_data(year=2026)
    operative_dashboard_periods = [
        row for row in dashboard["periods"]
        if row["reconciliation_mode"] == "Operativa"
    ]
    assert len(operative_dashboard_periods) == 10, len(operative_dashboard_periods)
    from frappe.desk.desk_page import get as get_desk_page

    page = get_desk_page("control-credinomina")
    report = frappe.get_doc("Report", "Antiguedad de Saldos")
    workspace = frappe.get_doc("Workspace", "Conciliacion Credinomina")
    assert page.page_name == "control-credinomina" and page.script
    assert report.report_type == "Script Report" and not report.disabled
    report_links = [link for link in workspace.links if link.link_to == report.name]
    control_card = next(
        link for link in workspace.links
        if link.type == "Card Break" and link.label == "Control y consultas"
    )
    assert len(report_links) == 1 and control_card.link_count == 6, [
        (link.label, link.link_to) for link in workspace.links
    ]
    details = frappe.get_all(
        "CN Remittance Allocation",
        filters={"deposit_reference": ["in", [
            _reference(letter, month)
            for _employer, _code, letter in EMPLOYERS for month in MONTHS
        ]]},
        fields=[
            "name", "deposit_reference", "deposit_date", "detail_imported_on",
            "detail_status", "detail_count", "allocated_usd", "unallocated_usd",
        ],
        order_by="name asc",
    )
    assert sum(row.detail_status == "Conciliado" for row in details) == 8, details
    assert sum(row.detail_status == "Detalle pendiente" for row in details) == 1, details
    for row in details:
        month = next(
            month for month in MONTHS
            if row.deposit_reference.endswith(month[:7].replace("-", ""))
        )
        expected_deposit = _deposit_date(month)
        assert row.deposit_date == expected_deposit, row
        if row.detail_status == "Conciliado":
            assert row.detail_imported_on.date() == expected_deposit + timedelta(days=3), row
        else:
            assert not row.detail_imported_on and round(row.unallocated_usd, 4) == 920.5, row
    return {
        "report_rows": len(rows), "totals": totals,
        "beta_aging_rows": len(beta_rows),
        "client_1019_aging_rows": len(client_rows),
        "summary_periods": len(summary_rows),
        "customer_statement_rows": len(account_rows),
        "dashboard_periods": len(dashboard["periods"]),
        "page_and_aging_report_available": True,
        "reconciled_deposits": 8, "pending_detail_deposits": 1,
        "next_month_deposits_with_late_detail_verified": 8,
        "unallocated_pending_usd": sum(row.unallocated_usd for row in details if row.detail_status == "Detalle pendiente"),
    }
