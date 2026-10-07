"""Exercise actual removal hooks on the disposable site; always roll back."""
import frappe
from unittest.mock import patch

from credinomina_reconciliation.accounting_evidence import CORE_REMOVAL_ROLE, has_core_removal_role
from credinomina_reconciliation.patches.v1_0.add_core_removal_role import execute


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    previous_user = frappe.session.user
    frappe.set_user("Administrator")
    marker = "CORE-REMOVAL-" + frappe.generate_hash(length=8)
    try:
        assignments = frappe.db.count("Has Role", {"role": CORE_REMOVAL_ROLE})
        execute()
        execute()
        assert frappe.db.count("Role", {"name": CORE_REMOVAL_ROLE}) == 1
        assert frappe.db.count("Has Role", {"role": CORE_REMOVAL_ROLE}) == assignments
        # Isolate both authorization cases regardless of the site's prior roles.
        frappe.db.delete("Has Role", {"parent": "Administrator", "parenttype": "User", "role": CORE_REMOVAL_ROLE})
        frappe.db.set_value("Role", CORE_REMOVAL_ROLE, "disabled", 0)
        assert not has_core_removal_role(), "Administrator must not implicitly bypass the role"
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "source_file": f"/private/files/{marker}.csv", "file_hash": marker,
            "status": "Importado", "currency": "USD", "rows": [{
                "event_type": "Aplicacion", "event_date": "2025-04-04", "source_row": 2,
                "source_key": marker, "accounting_source_key": marker, "currency": "USD",
                "amount": 10, "amount_usd": 10, "effective": 1,
            }]}).insert()
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_reference": marker, "deposit_date": "2025-04-04", "deposit_currency": "USD",
            "deposit_amount": 10, "accounting_source_key": marker + "DEP"}).insert()
        item = frappe.get_doc({"doctype": "CN Complementary Item", "employer": employer.name,
            "accounting_source_key": marker + "ITEM", "category": "Otros ingresos",
            "posting_date": "2025-04-04", "currency": "USD", "amount": 10,
            "reference": marker, "description": "Prueba autorización core",
            "review_action": "Partida de depósito", "review_notes": "Revisado", "amount_reviewed": 1})
        item.flags.defer_reconciliation = True
        item.insert()

        def rejected(action):
            frappe.db.savepoint("core_removal_denied")
            try:
                action()
            except frappe.ValidationError as exc:
                frappe.db.rollback(save_point="core_removal_denied")
                assert CORE_REMOVAL_ROLE in str(exc), str(exc)
            else:
                raise AssertionError("Core movement removed without its explicit role")

        for doc in (imported, deposit, item):
            rejected(lambda: frappe.delete_doc(doc.doctype, doc.name))
        for doc in (deposit, item):
            doc.submit()
            rejected(lambda: frappe.get_doc(doc.doctype, doc.name).cancel())
            assert frappe.db.get_value(doc.doctype, doc.name, "docstatus") == 1

        frappe.get_doc({"doctype": "Has Role", "parent": "Administrator", "parenttype": "User",
                       "parentfield": "roles", "role": CORE_REMOVAL_ROLE}).insert(ignore_permissions=True)
        assert has_core_removal_role()
        for doc in (deposit, item):
            frappe.get_doc(doc.doctype, doc.name).cancel()
            assert frappe.db.get_value(doc.doctype, doc.name, "docstatus") == 2
        for doc in (imported, deposit, item):
            # Cleanup jobs run after commit; this test always rolls back.
            with patch.object(frappe, "enqueue") as enqueue:
                frappe.delete_doc(doc.doctype, doc.name)
                assert all(call.kwargs.get("enqueue_after_commit") for call in enqueue.call_args_list)
            assert not frappe.db.exists(doc.doctype, doc.name)
        return {"role_creation_idempotent": True, "no_automatic_assignments": True,
                "three_doctypes_protected": True, "explicit_role_allows_removal": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(previous_user)
