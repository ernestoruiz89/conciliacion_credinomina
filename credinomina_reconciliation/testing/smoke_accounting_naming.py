"""Read-only migration audit and rollback-only naming checks on the test site."""

import hashlib
import json

import frappe


def _test_site():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")


def audit():
    _test_site()
    doctype = "CN Accounting Import" if frappe.db.exists("DocType", "CN Accounting Import") else "CN Source Import"
    if doctype == "CN Accounting Import":
        assert not frappe.db.exists("DocType", "CN Source Import")
        assert not frappe.db.table_exists("CN Source Import")
        assert frappe.get_meta("CN Reconciliation Exception").get_field("source_import").options == doctype
        workspace = frappe.get_doc("Workspace", "Conciliacion Credinomina")
        links = list(workspace.links or []) + list(workspace.shortcuts or [])
        assert any(row.link_to == doctype for row in links)
        assert not any(row.link_to == "CN Source Import" for row in links)
    names = set(frappe.get_all(doctype, pluck="name", limit_page_length=0))
    rows = frappe.get_all("CN Source Row", filters={"parenttype": doctype},
        fields=["name", "parent", "source_key", "event_date", "amount_usd", "amount_nio",
                "effective", "match_status", "deposit_match_status", "collection_period",
                "historical_period", "historical_application_id", "historical_remitted_usd",
                "historical_balance_usd", "application_allocation_detail", "historical_detail"],
        order_by="name", limit_page_length=0)
    assert all(row.parent in names for row in rows)
    for row in rows:
        row.pop("parent")
    attachments = frappe.get_all("File", filters={"attached_to_doctype": doctype},
        fields=["name", "attached_to_name"], limit_page_length=0)
    assert all(row.attached_to_name in names for row in attachments)
    exceptions = frappe.get_all("CN Reconciliation Exception", filters={"source_import": ["is", "set"]},
        fields=["name", "source_import"], limit_page_length=0)
    assert all(row.source_import in names for row in exceptions)
    return {"doctype": doctype, "imports": len(names), "rows": len(rows),
            "row_data_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest(),
            "attachments": len(attachments), "exceptions": len(exceptions), "references": "OK"}


def run():
    _test_site()
    frappe.set_user("Administrator")
    marker = "accounting-name-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                  "employer_code": marker}).insert()
        document = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "source_type": "Movimientos contables", "source_file": f"/private/files/{marker}.xlsx",
            "currency": "USD"}).insert()
        draft = document.name
        assert draft.startswith("CONTA-BORRADOR-")
        file = frappe.get_doc({"doctype": "File", "file_name": marker + ".xlsx",
            "file_url": document.source_file, "attached_to_doctype": document.doctype,
            "attached_to_name": draft, "is_private": 1})
        file.db_insert()
        exception = frappe.get_doc({"doctype": "CN Reconciliation Exception", "name": marker,
                                   "source_import": draft})
        exception.db_insert()
        document.append("rows", {"source_row": 2, "event_type": "Aplicacion", "event_date": "2026-09-30",
            "source_key": marker, "client_name": marker, "amount": 10, "amount_usd": 10,
            "currency": "USD", "effective": 1, "processing_route": "Operativa"})
        document.save()
        first = f"CONTA-{marker}-9-2026-001"
        assert document.name == first, document.name
        assert frappe.db.get_value("File", file.name, "attached_to_name") == first
        assert frappe.db.get_value("CN Reconciliation Exception", marker, "source_import") == first
        child = document.rows[0].name
        assert frappe.db.get_value("CN Source Row", child, "parent") == first
        document.reload()
        document.save()
        assert document.name == first
        second = frappe.copy_doc(document)
        second.source_file = f"/private/files/{marker}-2.xlsx"
        second.insert()
        assert second.name == f"CONTA-{marker}-9-2026-002", second.name
        document.rows[0].event_date = "2026-10-01"
        document.save()
        assert document.name == f"CONTA-{marker}-10-2026-001"
        assert frappe.db.get_value("CN Source Row", child, "parent") == document.name
        assert frappe.db.get_value("File", file.name, "attached_to_name") == document.name
        from credinomina_reconciliation.patches.v1_0.rename_accounting_imports_by_month import execute

        before = set(frappe.get_all(document.doctype, pluck="name", limit_page_length=0))
        execute()
        execute()
        assert set(frappe.get_all(document.doctype, pluck="name", limit_page_length=0)) == before
        return {"draft": "OK", "monthly_company_sequence": "OK", "attachments_links_rows": "OK",
                "date_change": "OK", "idempotent_patch": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
