"""Saved deposit evidence and monthly control; isolated site, always rollback."""
import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation.accounting_deposits import plan_deposits, create_deposits
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override, file_sha256
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute
from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import import_source_file


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "DEPSMOKE-" + frappe.generate_hash(length=10)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        banks_before = set(frappe.get_all("CN Bank Account", pluck="name"))
        content = ("Fecha Aplica,Cuenta Contable,Descripcion,TMov,TDoc,No. Cmpte,No. Ref,Debito del Mes,Credito del Mes,Empresa\n"
            f"2025-04-04,1602,C$4394.92 DEPOSITO POR CONVENIO {marker} EN LA CUENTA BAC 987654321 C$ EL DIA 03/04/2025,02,12,{marker},{marker},0,3662.43,{marker}\n"
            f"2025-04-05,1602,U$25.00 DEPOSITO POR CONVENIO SIN IDENTIFICAR,02,12,{marker}B,{marker}B,0,915.61,NO REGISTRADA\n").encode()
        file = save_file(marker + ".csv", content, "CN Employer", employer.name, is_private=1)
        rows = apply_accounting_currency_override(parse_accounting_movements("test.csv", content), "NIO", 36.6243)
        plan = plan_deposits(rows, [employer.as_dict()])
        created = create_deposits(plan, file.file_url, file_sha256(content))
        assert len(created) == 2
        first, unknown = created
        assert first.docstatus == 0 and first.deposit_amount == 4394.92 and first.amount_usd == 120
        assert first.bank_account == "NO IDENTIFICADA" and first.source_credit == 3662.43
        assert str(first.deposit_date) == "2025-04-03" and str(first.source_date) == "2025-04-04"
        assert unknown.bank_account == "NO IDENTIFICADA" and unknown.employer == "NO IDENTIFICADA" and unknown.amount_usd == 25
        assert set(frappe.get_all("CN Bank Account", pluck="name")) == banks_before | {"NO IDENTIFICADA"}
        assert not frappe.db.get_value("CN Bank Account", "NO IDENTIFICADA", "currency")
        assert not create_deposits(plan, file.file_url, file_sha256(content))
        assert plan[0]["remittance_allocation"] == first.name
        def rejected(action):
            frappe.db.savepoint("imported_removal")
            try:
                action()
            except frappe.ValidationError as exc:
                frappe.db.rollback(save_point="imported_removal")
                assert "histórico contable" in str(exc), str(exc)
            else:
                raise AssertionError("Imported accounting evidence was removed")
        rejected(lambda: frappe.delete_doc(first.doctype, first.name))
        # Legacy canceled imports must remain protected too.
        frappe.db.set_value(unknown.doctype, unknown.name, "docstatus", 2)
        rejected(lambda: frappe.delete_doc(unknown.doctype, unknown.name))
        frappe.db.set_value(unknown.doctype, unknown.name, "docstatus", 0)
        item = frappe.get_doc({"doctype": "CN Complementary Item", "employer": employer.name,
            "accounting_source_key": marker + "-ITEM", "source_file": file.file_url, "source_row": 4,
            "category": "Por clasificar", "posting_date": "2025-04-04", "currency": "USD",
            "amount": 10, "description": "Movimiento histórico de prueba"}).insert()
        rejected(lambda: frappe.delete_doc(item.doctype, item.name))
        item.category = "Otros ingresos"
        item.reference = marker + "-ITEM"
        item.review_action = "Partida de depósito"
        item.review_notes = "Ingreso identificado y revisado"
        item.amount_reviewed = 1
        item.flags.defer_reconciliation = True
        item.submit()
        rejected(lambda: frappe.get_doc(item.doctype, item.name).cancel())
        frappe.db.set_value(item.doctype, item.name, "docstatus", 2)
        rejected(lambda: frappe.delete_doc(item.doctype, item.name))
        # A confirmed deposit may lack detail, but never lack a company.
        try:
            unknown.employer = ""
            unknown.submit()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Unidentified employer was submitted")
        unknown.reload()
        first.source_credit = 1
        try:
            first.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Evidence was editable")
        first.reload()
        bulk_plan = bulk._plan({"source_file": file.file_url, "currency": "NIO", "manual_fx_rate": 36.6243})
        assert not bulk_plan["groups"] and not bulk_plan["issues"] and len(bulk_plan["deposits"]) == 2
        summary = bulk._summary(bulk_plan)
        assert summary["deposit_count"] == 2 and summary["rows"] == 0
        bulk_result = bulk._create_imports(bulk_plan, {"source_file": file.file_url, "currency": "NIO", "manual_fx_rate": 36.6243})
        assert len(bulk_result) == 2 and all(row["doctype"] == "CN Remittance Allocation" for row in bulk_result)
        document = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "source_file": file.file_url, "currency": "NIO", "manual_fx_rate": 36.6243, "status": "Importado",
            "rows": [{**rows[0], "remittance_allocation": first.name, "effective": 0}]}).insert()
        _, data, *_ = execute({"month": "2025-04-01", "employer": employer.name})
        data = [row for row in data if row["evidence_key"] == first.accounting_source_key]
        assert len(data) == 1 and data[0]["credit_nio"] == 3662.43 and data[0]["credit_usd"] == 100, data
        assert data[0]["state"] == first.result and data[0]["accounting_import"] == document.name
        assert data[0]["bank_account"] == first.bank_account
        for result in ("Revisar detalle", "Conciliado"):
            frappe.db.set_value("CN Remittance Allocation", first.name, "result", result)
            _, filtered, _, _, totals = execute({"month": "2025-04-01", "employer": employer.name,
                                                 "movement_type": "Depósito", "state": result})
            assert len(filtered) == 1 and filtered[0]["state"] == result
            assert next(kpi["value"] for kpi in totals if kpi["label"] == "Movimientos") == 1
        frappe.db.set_value("CN Remittance Allocation", first.name, "result", first.result)
        frappe.get_doc({"doctype": "File", "file_name": file.file_name, "file_url": file.file_url,
            "is_private": 1, "attached_to_doctype": document.doctype, "attached_to_name": document.name}).insert()
        response = import_source_file(document.name)
        document = frappe.get_doc("CN Accounting Import", response["import_name"])
        assert len(document.rows) == 2
        assert all(row.remittance_allocation and not row.effective for row in document.rows)
        assert document.total_usd == 0, "Deposit mirrors must not become new applications/cash"
        assert frappe.db.count("CN Remittance Allocation", {"source_file": file.file_url}) == 2
        first.reload()
        first.submit()
        rejected(lambda: frappe.get_doc(first.doctype, first.name).cancel())
        assert frappe.db.get_value(first.doctype, first.name, "docstatus") == 1
        _, data, *_ = execute({"month": "2025-04-01", "employer": employer.name})
        assert next(row for row in data if row["evidence_key"] == first.accounting_source_key)["state"] == "Pendiente"
        return {"cash_vs_ledger": "OK", "only_holding_bank_created": "OK", "unknown_bank_and_company": "OK",
                "idempotence": "OK", "individual_and_bulk": "OK", "no_duplicate_cash": "OK",
                "report_mirror_once": "OK", "evidence_immutable": "OK",
                "imported_deposit_and_item_removal_blocked": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
