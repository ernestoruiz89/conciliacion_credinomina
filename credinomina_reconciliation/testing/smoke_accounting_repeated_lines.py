"""Physical repeats, preview, reconciliation, report and retries; rollback only."""
import csv
import io
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "repeat-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert().name
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF",
                         "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA", "NO_CREDITO", "NOMBRE_CLIENTE"])
        for tmov, tdoc, description, debit, credit in [
            (12, 5, "PAGO", 100, 0), (1, 1, "INTERNO", 20, 0),
            (2, 12, "U$30.00 DEPOSITO POR CONVENIO " + marker, 0, 30),
        ]:
            line = ["1602", "2025-04-15", tmov, tdoc, marker, marker + str(tdoc),
                    description, debit, credit, employer, marker + "-1", "Cliente " + marker]
            writer.writerows([line, line])
        file = save_file(marker + ".csv", stream.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": file.file_url, "currency": "USD"}
        plan = bulk._plan(options)
        assert not plan["issues"], plan["issues"]
        assert len(plan["duplicates"]) == 3, plan["duplicates"]
        assert plan["groups"][0]["count"] == 2
        assert len(plan["complementary"]) == len(plan["deposits"]) == 2
        created = bulk._create_imports(plan, options)
        assert len(created) == 5, created
        document = frappe.get_doc("CN Accounting Import", created[0]["name"])
        assert document.total_usd == 200 and len(document.rows) == 2
        old_ids = [row.name for row in document.rows]
        old_keys = [row.accounting_source_key for row in document.rows]
        # Real reconciliation must not ignore the second equal payment.
        accounting._reconcile_sources(employer)
        document.reload()
        assert all(row.effective and row.match_status != "Ignorado" for row in document.rows)
        accounting.import_source_file(document.name)
        document.reload()
        assert [row.name for row in document.rows] == old_ids
        assert [row.accounting_source_key for row in document.rows] == old_keys
        assert all(row.effective for row in document.rows) and document.total_usd == 200
        _, report, *_ = execute({"month": "2025-04-01", "employer": employer})
        assert len(report) == 6, report
        assert sum(row["debit_usd"] for row in report) == 240
        assert sum(row["credit_usd"] for row in report) == 60
        repeated = bulk._plan(options)
        assert not repeated["groups"] and not repeated["complementary"]
        assert len(repeated["already_imported"]) == 4
        counts = [frappe.db.count(dt) for dt in ("CN Accounting Import", "CN Complementary Item", "CN Remittance Allocation")]
        bulk._create_imports(repeated, options)
        assert counts == [frappe.db.count(dt) for dt in ("CN Accounting Import", "CN Complementary Item", "CN Remittance Allocation")]
        # An individual file with equal ledger values is also allowed. A new
        # source column distinguishes this file from the already loaded origin.
        individual_content = "\n".join(line + (",ORIGEN" if index == 0 else ",individual")
            for index, line in enumerate(stream.getvalue().splitlines())).encode()
        individual_file = save_file(marker + "-individual.csv", individual_content, None, None, is_private=1)
        individual = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer,
            "source_file": individual_file.file_url, "currency": "USD"}).insert()
        frappe.get_doc({"doctype": "File", "file_name": individual_file.file_name, "file_url": individual_file.file_url,
            "is_private": 1, "attached_to_doctype": individual.doctype, "attached_to_name": individual.name}).insert()
        with patch.object(accounting, "_reconcile_sources", return_value={}):
            response = accounting.import_source_file(individual.name)
            individual = frappe.get_doc("CN Accounting Import", response["import_name"])
            ids = [row.name for row in individual.rows]
            accounting.import_source_file(individual.name)
        individual.reload()
        assert len(individual.rows) == 6 and [row.name for row in individual.rows] == ids
        assert len({row.complementary_item for row in individual.rows if row.complementary_item}) == 2
        assert len({row.remittance_allocation for row in individual.rows if row.remittance_allocation}) == 2
        return {"physical_lines": 6, "similarities_warned": 3, "debits_usd": 240,
                "credits_usd": 60, "reconciliation_keeps_both_payments": True,
                "csv_reimport_stable": True, "same_file_retry_idempotent": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
