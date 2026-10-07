"""Real Deleted Document recovery; disposable site only, always rolled back."""
import json

import frappe
from frappe.core.doctype.deleted_document.deleted_document import restore

from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "RESTORE-" + frappe.generate_hash(length=10)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
            "client_number": marker, "client_name": "Cliente restauración"}).insert()
        items = []
        for suffix in ("VALID", "CANCELED"):
            item = frappe.get_doc({"doctype": "CN Complementary Item", "employer": employer.name,
                "category": "Otros ingresos", "posting_date": "2025-06-30", "currency": "USD",
                "amount": 10, "reference": marker + suffix, "description": "Prueba restauración"})
            item.flags.defer_reconciliation = True
            item.insert()
            item.submit()
            items.append(item)
        frappe.db.set_value(items[1].doctype, items[1].name, "docstatus", 2)
        payload = {
            "doctype": "CN Remittance Allocation", "name": marker, "docstatus": 2,
            "employer": employer.name, "deposit_date": "2025-06-30", "deposit_currency": "NIO",
            "deposit_amount": 123788.11, "fx_rate": 36.6243,
            "deposit_reference": marker, "deposit_voucher": "001011639",
            "accounting_source_key": marker, "source_file": "/private/files/restore-test.xlsx",
            "source_row": 6355, "source_account": "160209013004", "source_credit": 123788.11,
            "source_currency": "NIO", "source_fx_rate": 36.6243, "source_date": "2025-06-30",
            "source_voucher": "001011639", "source_description": "Evidencia original",
            "source_file_hash": marker, "accounting_reference": marker, "tmov": "02", "tdoc": "12",
            "result": "Conciliado", "allocated_usd": 99, "allocation_detail": '[{"amount_usd":99}]',
            "targets": [{"complementary_item": items[1].name, "amount_usd": -9.3},
                        {"complementary_item": items[0].name, "amount_usd": 10},
                        {"complementary_item": marker + "-MISSING", "amount_usd": 5}],
            "detail_rows": [{"client_name": client.client_name, "client_number": marker,
                             "amount_usd": 100, "matched_targets": '[{"amount_usd":99}]'}],
        }
        # Ordinary inserts still reject each invalid link independently.
        for target, error in ((payload["targets"][0], frappe.CancelledLinkError),
                              (payload["targets"][2], frappe.LinkValidationError)):
            try:
                frappe.get_doc(dict(payload, docstatus=0, targets=[target])).insert()
            except error:
                pass
            else:
                raise AssertionError("Normal insert bypassed link validation")
        deleted = frappe.get_doc({"doctype": "Deleted Document", "deleted_doctype": payload["doctype"],
            "deleted_name": marker, "data": json.dumps(payload)}).insert()
        restore(deleted.name, alert=False)
        deleted.reload()
        assert json.loads(deleted.data) == payload, "Original deleted snapshot was changed"
        recovered = frappe.get_doc(payload["doctype"], deleted.new_name)
        assert deleted.restored and recovered.docstatus == 0
        assert recovered.accounting_source_key == marker
        for field in EVIDENCE_FIELDS:
            if field in payload:
                assert str(recovered.get(field)) == str(payload[field]), field
        assert len(recovered.targets) == 1 and recovered.targets[0].complementary_item == items[0].name
        assert recovered.targets[0].idx == 1
        assert recovered.allocated_usd == 0 and recovered.allocation_detail == "[]"
        assert recovered.result == "Pendiente" and recovered.detail_rows[0].matched_targets == "[]"
        assert recovered.detail_rows[0].pending_usd == 100
        comments = frappe.get_all("Comment", filters={"reference_doctype": recovered.doctype,
                                  "reference_name": recovered.name}, pluck="content")
        assert any(items[1].name in comment and "-9.3" in comment for comment in comments)
        assert frappe.db.get_value(items[1].doctype, items[1].name, "docstatus") == 2
        assert frappe.db.get_value(items[0].doctype, items[0].name, "docstatus") == 1
        assert frappe.get_meta(recovered.doctype).get_field("accounting_tab").depends_on == "eval:!!doc.accounting_source_key"
        return {"restored_as_draft": True, "accounting_evidence_preserved": True,
                "invalid_targets_removed_and_audited": True, "valid_target_preserved": True,
                "normal_link_validation_preserved": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
