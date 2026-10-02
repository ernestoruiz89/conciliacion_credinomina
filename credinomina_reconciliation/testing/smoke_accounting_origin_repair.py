"""Reproduce crossed REPSA/CENTROLAC origins on real saved data, rollback only."""
import csv
import io
import json
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.accounting_origin_repair import repair_accounting_origins
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute
from credinomina_reconciliation.parsers import parse_accounting_movements, source_key


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "ORIG-" + frappe.generate_hash(length=8)
    try:
        repsa, centrolac = [frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
            "employer_code": marker + suffix, "payroll_frequency": "Mensual"}).insert().name
            for suffix in ("-REPSA", "-CENTROLAC")]
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                         "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA", "TMOV", "TDOC"])
        loan = str(900000000 + int(marker[-8:], 36) % 100000000) + "-1"
        account = "160209013004"

        def application(day, index):
            writer.writerow([account, f"2025-04-{day}", f"{marker}-{day}-{index}", f"REF-{index}",
                f"PRESTAMO {loan} NO. DOCUM {index} CLIENTE BENJAMIN FRANCISCO HERNANDEZ BRENES CONVENIO {repsa}",
                1870.77 if index == 5 else 366.24, 0, repsa, "12", "05"])

        # The fifth local CSV row has physical position 6, but ORIGINAL row 6
        # is a deposit by a different employer, not this client's application.
        for index in range(1, 5):
            application(30, index)
        writer.writerow([account, "2025-04-01", "00101922", "67670162",
            f"C$128,185.05 DEPOSITO POR CONVENIO {centrolac} EN LA CUENTA BANPRO 3268 C$ EL DIA 01/04/2025",
            0, 113167.26, centrolac, "02", "12"])
        application(30, 5)
        for index in range(1, 6):
            application(26, index)
        original_file = save_file(marker + ".csv", stream.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": original_file.file_url, "currency": "NIO", "manual_fx_rate": 36.6243}
        plan = bulk._plan(options)
        assert len(plan["groups"]) == 2 and len(plan["deposits"]) == 1, plan
        created = bulk._create_imports(plan, options)
        names = [entry["name"] for entry in created if entry.get("doctype") != "CN Remittance Allocation"]
        documents = [frappe.get_doc("CN Accounting Import", name) for name in names]
        imported_deposit = frappe.get_doc("CN Remittance Allocation", plan["deposits"][0]["remittance_allocation"])
        assert imported_deposit.source_row == 6
        assert not imported_deposit.detail_rows and not imported_deposit.targets
        expected = {document.name: [(row.name, row.source_row, row.accounting_source_key) for row in document.rows]
                    for document in documents}
        file_proofs = {document.name: document.file_hash for document in documents}
        for document in documents:
            file_doc = frappe.get_doc("File", {"file_url": document.source_file})
            content = file_doc.get_content()
            content = content.encode() if isinstance(content, str) else content
            seeds = parse_accounting_movements(file_doc.file_name, content)
            for row, seed in zip(document.rows, seeds):
                local_position = row.idx + 1
                wrong_key = (imported_deposit.accounting_source_key if local_position == 6 else
                             source_key("accounting-line-v2", document.bulk_source_hash, local_position, seed["accounting_source_key"]))
                frappe.db.set_value("CN Source Row", row.name,
                    {"source_row": local_position, "accounting_source_key": wrong_key}, update_modified=False)
            document.reload()

        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": repsa,
            "payroll_month": "2025-04-01", "reconciliation_mode": "Historica", "historical_scope": "Fecha exacta",
            "historical_application_date": "2025-04-30"}).insert()
        closed_import = next(document for document in documents if str(document.bulk_event_date) == "2025-04-30")
        application_row = closed_import.rows[-1]
        frappe.db.set_value("CN Source Row", application_row.name,
                            {"historical_period": period.name, "processing_route": "Historica"})
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": repsa,
            "deposit_date": "2025-05-10", "deposit_reference": marker + "-VALID", "deposit_currency": "USD",
            "deposit_amount": 40, "targets": [{"historical_application": application_row.name, "amount_usd": 40}]}).insert()
        deposit.submit()
        allocation = [{"tipo": "Aplicacion historica", "periodo": period.name,
                       "aplicacion_id": application_row.name, "credito": loan, "importe_usd": 40, "origen": "Manual"}]
        frappe.db.set_value("CN Remittance Allocation", deposit.name,
            {"result": "Conciliado", "allocated_usd": 40, "allocation_detail": json.dumps(allocation)})
        frappe.db.set_value("CN Source Row", application_row.name, {"historical_period": period.name,
            "historical_remitted_usd": 40, "historical_balance_usd": 11.08, "deposit_match_status": "Depósito parcial",
            "historical_detail": json.dumps(allocation), "remittance_allocation": imported_deposit.name})
        frappe.db.set_value("CN Reconciliation Period", period.name,
            {"status": "Cerrado", "applied_usd": 91.08, "remitted_usd": 40})
        before_deposits = [frappe.get_doc("CN Remittance Allocation", name).as_dict()
                           for name in (deposit.name, imported_deposit.name)]
        before_period = frappe.get_doc("CN Reconciliation Period", period.name).as_dict()
        before_source = frappe.get_doc("CN Source Row", application_row.name).as_dict()
        with patch.object(accounting, "_reconcile_sources", side_effect=AssertionError("Repair must not reconcile")):
            preview = repair_accounting_origins(dry_run=True, import_names=names)
            assert preview["rows_changed"] == 6 and not preview["issues"], preview
            assert frappe.db.get_value("CN Source Row", application_row.name, "source_row") == 6
            result = repair_accounting_origins(dry_run=False, import_names=names)
            assert result["rows_changed"] == 6 and result["imports_changed"] == 2 and not result["issues"], result
            repeated = repair_accounting_origins(dry_run=False, import_names=names)
            assert repeated["rows_changed"] == 0 and not repeated["issues"], repeated
        for name in names:
            document = frappe.get_doc("CN Accounting Import", name)
            assert [(row.name, row.source_row, row.accounting_source_key) for row in document.rows] == expected[name]
            assert document.file_hash == file_proofs[name]
            assert frappe.db.count("Comment", {"reference_doctype": document.doctype,
                "reference_name": name, "comment_type": "Info"}) == 1
        after_source = frappe.get_doc("CN Source Row", application_row.name).as_dict()
        for field in before_source:
            if field not in {"source_row", "accounting_source_key", "remittance_allocation"}:
                assert before_source[field] == after_source[field], (field, before_source[field], after_source[field])
        assert before_deposits == [frappe.get_doc("CN Remittance Allocation", name).as_dict()
                                  for name in (deposit.name, imported_deposit.name)]
        assert before_period == frappe.get_doc("CN Reconciliation Period", period.name).as_dict()

        base = {"from_date": "2025-04-01", "to_date": "2025-04-30"}
        _, rows, *_ = execute({**base, "employer": repsa})
        assert len(rows) == 10 and all(not row["remittance_allocation"] and not row["bank_account"] for row in rows)
        assert all(not row["warning"] for row in rows), rows
        _, cash, *_ = execute({**base, "employer": centrolac})
        assert len(cash) == 1 and cash[0]["remittance_allocation"] == imported_deposit.name
        assert cash[0]["client_name"] == cash[0]["client_number"] == cash[0]["loan_number"] == ""
        assert cash[0]["credit_nio"] == 113167.26 and cash[0]["credit_usd"] == 3089.95
        # Another individual reload and whole-file retry must keep physical origins.
        open_import = next(document for document in documents if str(document.bulk_event_date) == "2025-04-26")
        with patch.object(accounting, "_reconcile_sources", return_value={}):
            accounting.import_source_file(open_import.name)
            accounting.import_source_file(open_import.name)
        open_import.reload()
        assert [(row.name, row.source_row, row.accounting_source_key) for row in open_import.rows] == expected[open_import.name]
        assert not bulk._plan(options)["groups"], "Retry must not recreate already imported physical lines"
        file_doc = frappe.get_doc("File", {"file_url": open_import.source_file})
        content = file_doc.get_content()
        content = content.encode() if isinstance(content, str) else content
        cells = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        cells[-1][cells[0].index("CN_FILA_ORIGEN")] = "6"  # Forge the other employer's deposit origin.
        changed = io.StringIO()
        csv.writer(changed).writerows(cells)
        with patch.object(accounting, "_attached_file", return_value=(file_doc, changed.getvalue().encode())):
            try:
                accounting.import_source_file(open_import.name)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Changed CSV must not reuse an unrelated physical origin")
        open_import.reload()
        assert [(row.name, row.source_row, row.accounting_source_key) for row in open_import.rows] == expected[open_import.name]
        assert before_deposits == [frappe.get_doc("CN Remittance Allocation", name).as_dict()
                                  for name in (deposit.name, imported_deposit.name)]
        return {"repaired_rows": 6, "applications": 10, "imports": 2, "real_report_company_filters": "OK",
            "deposit_without_client": "OK", "original_credit_nio_usd": "OK", "idempotent_and_audited": "OK",
            "closed_period_partial_allocation_and_stable_ids_preserved": "OK",
            "individual_csv_reloads_and_whole_file_retry": "OK", "forged_csv_origin_rejected": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
