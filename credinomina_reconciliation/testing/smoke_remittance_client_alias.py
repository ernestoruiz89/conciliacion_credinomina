"""Unknown detail name -> verified client alias; disposable site and rollback."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import remittance_client_alias as api


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    tables = ["CN Employer", "CN Client", "CN Client Alias", "CN Accounting Import", "CN Source Row",
              "CN Remittance Allocation", "CN Remittance Detail"]
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "DETAIL-ALIAS-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_number=marker,
                employee_number=marker + "-EMP", client_name="JEFFRY RAMON GARCIA SANCHEZ")).insert()
            frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, currency="USD", status="Importado",
                source_file=f"/private/files/{marker}.csv", rows=[dict(event_type="Aplicacion", event_date="2026-10-15",
                    currency="USD", amount=10, amount_usd=10, effective=1, source_key=marker,
                    client=client.name, client_number=client.client_number, client_name=client.client_name, loan_number="12671-1")])).insert()
            for status, alias in [(0, "JEFRI RAMON GARCIA SANCHEZ"), (1, "JEFREY RAMON GARCIA SANCHEZ")]:
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date="2026-10-15", deposit_reference=f"{marker}-{status}", deposit_currency="USD", deposit_amount=10,
                    detail_file=f"/private/files/{marker}-{status}.csv", detail_source_file=f"/private/files/{marker}-{status}.csv",
                    detail_hash=f"{marker}-{status}", detail_rows=[dict(source_row=66, client_name=alias, loan_number="12671-1", deducted_usd=10)])).insert()
                if status:
                    deposit.submit()
                deposit.reload()
                row = deposit.detail_rows[0]
                assert not row.client and row.identity_reason == api.NAME_ISSUE
                saved_cash = {key: deposit.get(key) for key in ("deposit_amount", "amount_usd", "allocated_usd", "unallocated_usd", "allocation_detail")}
                data = api.preview_alias(deposit.name, row.name)
                assert data["client"] == client.name and data["alias_name"] == alias
                if not status:
                    # A failed deposit save must not leave the alias in the registry.
                    frappe.db.savepoint("detail_alias_failure")
                    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
                    with patch.object(CNRemittanceAllocation, "save", side_effect=frappe.ValidationError("save failed")):
                        try:
                            api.create_alias(deposit.name, row.name, client.name, data["modified"], data["client_modified"])
                        except frappe.ValidationError:
                            frappe.db.rollback(save_point="detail_alias_failure")
                        else:
                            raise AssertionError("Expected failed deposit save")
                    assert not client.reload().aliases
                api.create_alias(deposit.name, row.name, client.name, data["modified"], data["client_modified"])
                deposit.reload(); client.reload()
                assert deposit.docstatus == status and deposit.detail_rows[0].client == client.name
                assert deposit.detail_rows[0].client_name == alias
                assert alias in [row.alias_name for row in client.aliases]
                assert saved_cash == {key: deposit.get(key) for key in saved_cash}, (saved_cash, {key: deposit.get(key) for key in saved_cash})
                assert not deposit.targets
            return dict(draft_and_submitted_supported=True, source_name_preserved=True, verified_alias_saved=True,
                        cash_and_targets_preserved=True, failed_save_rolled_back=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
