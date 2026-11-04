"""A metadata-only repair preserves confirmed cash and a closed period."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_client_number_repair as repair
from credinomina_reconciliation.patches.v1_0.repair_complementary_client_numbers import execute
from credinomina_reconciliation.reconciliation_scope import document_state


def exercise(item, client, deposit, source, period, fixed):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    # Reproduce old malformed metadata after cash was already reconciled.
    frappe.db.set_value(item.doctype, item.name, "client_number", client.client_name, update_modified=False)
    frappe.db.set_value(period.doctype, period.name, "status", "Cerrado", update_modified=False)
    item.reload(); period.reload()
    before = {doc.name: document_state(doc.reload()) for doc in (deposit, source, period, fixed)}
    original = frappe.db.get_value(item.doctype, item.name, "*", as_dict=True)
    versions = frappe.db.count("Version", {"ref_doctype": item.doctype, "docname": item.name})
    traces = frappe.db.count("CN Reconciliation Trace")
    # Check the real candidate query, then restrict mutations to this fixture.
    candidates = repair.candidate_rows()
    assert item.name in {row.name for row in candidates}
    own = [row for row in candidates if row.name == item.name]
    with patch.object(repair, "candidate_rows", return_value=own), \
            patch("credinomina_reconciliation.deposit_reconciliation.reconcile_deposit", side_effect=AssertionError("Replayed deposit")), \
            patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import._reconcile_sources",
                  side_effect=AssertionError("Replayed company")):
        preview = repair.run(dry_run=True)
        assert preview["would_repair"] == [item.name] and not preview["repaired"]
        assert frappe.db.get_value(item.doctype, item.name, "client_number") == client.client_name
        result = execute()
        assert result["repaired"] == [item.name] and not result["review"]
        after = frappe.db.get_value(item.doctype, item.name, "*", as_dict=True)
        assert after.client_number == client.client_number
        assert {key: value for key, value in after.items() if key != "client_number"} == {
            key: value for key, value in original.items() if key != "client_number"}
        assert before == {doc.name: document_state(doc.reload()) for doc in (deposit, source, period, fixed)}
        assert period.status == "Cerrado"
        assert traces == frappe.db.count("CN Reconciliation Trace")
        audit = frappe.get_all("Version", filters={"ref_doctype": item.doctype, "docname": item.name,
            "data": ["like", '%"cn_client_number_repair"%']}, fields=["data"])
        assert len(audit) == 1
        assert json.loads(audit[0].data)["changed"] == [["client_number", client.client_name, client.client_number]]
        assert not execute()["repaired"]
        assert frappe.db.count("Version", {"ref_doctype": item.doctype, "docname": item.name}) == versions + 1
        # Even a failure after the identifier write must not leave a partial
        # metadata repair or an unmatched audit entry behind.
        frappe.db.set_value(item.doctype, item.name, "client_number", client.client_name, update_modified=False)
        record_version = repair._record_version
        def failed_audit(*args):
            record_version(*args)
            raise RuntimeError("Injected audit failure")
        with patch.object(repair, "_record_version", side_effect=failed_audit):
            try:
                execute()
                raise AssertionError("Expected injected failure")
            except RuntimeError as error:
                assert "Injected audit failure" in str(error)
        assert frappe.db.get_value(item.doctype, item.name, "client_number") == client.client_name
        assert frappe.db.count("Version", {"ref_doctype": item.doctype, "docname": item.name}) == versions + 1
        assert before == {doc.name: document_state(doc.reload()) for doc in (deposit, source, period, fixed)}
