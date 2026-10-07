"""Exercise portfolio persistence and compact auditing on the disposable site."""

from unittest.mock import patch
from uuid import uuid4

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import (
    cn_credit_portfolio_snapshot as importer,
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo puede ejecutarse en cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        snapshot = frappe.get_doc({
            "doctype": "CN Credit Portfolio Snapshot",
            "source_file": "/private/files/portfolio-audit-test.xlsx",
        }).insert()
        filters = {"reference_doctype": snapshot.doctype, "reference_name": snapshot.name,
                   "comment_type": "Comment"}
        version_filters = {"ref_doctype": snapshot.doctype, "docname": snapshot.name}
        file_doc = frappe._dict(file_name="portfolio-<test>.xlsx")
        digests = []
        for generation, count in ((1, 200), (2, 150)):
            content = f"synthetic-portfolio-{generation}".encode()
            records = [dict(
                source_row=i + 2, report_date="2099-01-31",
                credit_number=f"AUDIT{generation}{i}-1", client_name=f"Prueba {i}",
                client_number_core=f"AUDIT-{i}", is_convenio="No",
                credit_status="Corriente", raw_data='{"test": "preserved"}',
            ) for i in range(count)]
            with patch.object(importer, "_attached_file", return_value=(file_doc, content)), \
                 patch.object(importer, "parse_credit_portfolio", return_value=records), \
                 patch("frappe.core.doctype.version.version.Version.insert",
                       side_effect=AssertionError("Bulk import must not serialize Version")):
                result = importer.import_portfolio_snapshot(snapshot.name)
                assert result["row_count"] == count, result
                snapshot = frappe.get_doc(snapshot.doctype, result["snapshot_name"])
                filters["reference_name"] = snapshot.name
                version_filters["docname"] = snapshot.name
                unchanged = importer.import_portfolio_snapshot(snapshot.name)
                assert unchanged["unchanged"] and unchanged["row_count"] == count
            saved = frappe.get_doc(snapshot.doctype, snapshot.name)
            assert len(saved.rows) == count
            assert saved.rows[0].credit_number == f"AUDIT{generation}0-1"
            assert saved.rows[0].client_number_core == "AUDIT-0"
            assert saved.rows[0].raw_data == '{"test": "preserved"}'
            assert saved.status == "Importado"
            digests.append(saved.file_hash)
            comments = frappe.get_all("Comment", filters=filters, pluck="content")
            assert len(comments) == generation, comments
            audit = next(c for c in comments if saved.file_hash in c)
            assert "portfolio-&lt;test&gt;.xlsx" in audit, audit
            assert len(audit) < 2500, len(audit)
            if generation == 2:
                assert digests[0] in audit and "200" in audit
            assert not frappe.db.exists("Version", version_filters)

        # The exception applies only to bulk imports, not ordinary user edits.
        saved.notes = "Edición ordinaria con historial"
        saved.save()
        assert frappe.db.exists("Version", version_filters)
        return {"initial_rows": 200, "updated_rows": 150, "audit_comments": 2,
                "unchanged_import": "OK", "normal_edit_version": "OK"}
    finally:
        frappe.db.rollback()


def run_master_creation():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo puede ejecutarse en cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    token = uuid4().hex[:10]
    company = f"Cartera prueba {token}"
    number = f"CART-{token}"
    content = f"portfolio-master-{token}".encode()
    records = [dict(
        report_date="2098-02-28", credit_number=f"{token}-{i}-1",
        employer_text=company, client_number_core=number,
        client_name="Cliente de prueba", national_id=f"ID-{token}",
        credit_status="Corriente", is_convenio="Si",
    ) for i in range(2)]
    records.append(records[0] | {
        "employer_text": " N/A ", "client_number_core": f"SKIP-{token}",
        "national_id": "", "client_name": "Sin convenio",
    })
    try:
        # Simulate an already-imported file whose masters were not created.
        snapshot = frappe.get_doc({
            "doctype": "CN Credit Portfolio Snapshot",
            "source_file": "/private/files/portfolio-masters.xlsx",
            "report_date": "2098-02-28", "status": "Importado con alertas",
            "file_hash": importer.file_sha256(content), "rows": records,
        }).insert()
        with patch.object(importer, "_attached_file", return_value=(
            frappe._dict(file_name="portfolio-masters.xlsx"), content,
        )), patch.object(importer, "parse_credit_portfolio", return_value=records):
            result = importer.import_portfolio_snapshot(snapshot.name)
            assert result["created_employer_count"] == 1, result
            assert result["created_client_count"] == 1, result
            employer = frappe.get_doc("CN Employer", company)
            assert employer.employer_code == company
            customer = frappe.get_doc("CN Client", number)
            assert customer.employer == company
            assert customer.national_id == f"ID-{token}"
            saved = frappe.get_doc(snapshot.doctype, snapshot.name)
            assert all(row.matched_client == number for row in saved.rows[:2])
            assert not saved.rows[2].matched_client
            assert not frappe.db.exists("CN Client", f"SKIP-{token}")
            assert saved.unmatched_client_count == 0
            assert saved.status == "Importado"
            assert importer.import_portfolio_snapshot(snapshot.name)["unchanged"]
            assert frappe.db.count("CN Client", {"employer": company}) == 1
        return {"existing_file_reprocessed": "OK", "employers_created": 1,
                "clients_created": 1, "multiple_credits_no_duplicates": "OK",
                "na_skipped": "OK"}
    finally:
        frappe.db.rollback()
