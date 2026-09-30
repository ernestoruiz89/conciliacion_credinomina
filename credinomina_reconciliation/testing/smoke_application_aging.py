"""Exercise the aging report against real SQL, rolling back every fixture."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute
from credinomina_reconciliation.rounding import sum_money


def run():
    frappe.set_user("Administrator")
    marker = "AGING-" + frappe.generate_hash(length=10)
    try:
        # Raw document inserts intentionally avoid reconciliation callbacks:
        # these fixtures represent an already-imported, unpaid historical file.
        def insert(doctype, suffix, **fields):
            doc = frappe.get_doc({"doctype": doctype, "name": marker + suffix, **fields})
            doc.db_insert()
            return doc

        employer = insert("CN Employer", "-E", employer_name=marker, employer_code=marker, grace_days=10)
        period = insert("CN Reconciliation Period", "-P", employer=employer.name,
                        payroll_month="2025-04-01", reconciliation_mode="Historica")
        source = insert("CN Source Import", "-I", employer=employer.name,
                        source_type="Movimientos contables", status="Importado", historical_backfill=1)
        for i, amount in enumerate((16.21, 17.70, 27.14, 21.71, 20.40, 58.61)):
            insert("CN Source Row", f"-R{i}", parent=source.name, parenttype=source.doctype,
                   parentfield="rows", idx=i + 1, event_type="Aplicacion", effective=1,
                   match_status="Conciliado", currency="USD", amount=amount, event_date="2025-04-30",
                   historical_period=period.name, client_name="Cliente de prueba",
                   client_number=f"T{i}", loan_number=f"T{i}-1")
        result = execute({"employer": employer.name, "as_of_date": "2025-05-11"})
        rows = result[1]
        assert len(rows) == 6, rows
        assert sum_money(row["amount_usd"] for row in rows) == sum_money([161.77])
        assert all(row["age_days"] == 1 for row in rows)
        assert sum_money(row["days_1_30"] for row in rows) == sum_money([161.77])
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa.antiguedad_de_saldos_por_empresa import execute as grouped
        company_rows = grouped({"employer": employer.name, "as_of_date": "2025-05-11"})[1]
        assert len(company_rows) == 1, company_rows
        assert company_rows[0]["amount_usd"] == 161.77
        assert company_rows[0]["days_1_30"] == 161.77
        assert "client_name" not in company_rows[0]
        return {"ok": True, "rows": 6, "pending_usd": 161.77,
                "due_date": "2025-05-10", "days_overdue": 1, "rolled_back": True}
    finally:
        frappe.db.rollback()
