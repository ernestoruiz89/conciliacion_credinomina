import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import frappe
from credinomina_reconciliation import reconciliation_audit as audit


class ReconciliationAuditTests(unittest.TestCase):
    def test_no_change_does_not_append_noise(self):
        before = audit.snapshot({"allocation_detail": '[{"importe_usd":1}]'})
        after = audit.snapshot({"allocation_detail": '[{ "importe_usd": 1 }] '})
        with patch.object(audit.frappe, "get_doc") as create:
            self.assertIsNone(audit.record_transition("D", before, after))
        create.assert_not_called()

    def test_change_keeps_old_and_new_scopes_without_overwriting(self):
        before = audit.snapshot({"amount_usd": 100, "allocated_usd": 80,
            "allocation_detail": '[{"periodo":"OLD","importe_usd":80}]'})
        after = audit.snapshot({"amount_usd": 100, "allocated_usd": 100,
            "allocation_detail": '[{"periodo":"NEW","importe_usd":100}]'})
        version = Mock(name="VERSION")
        with patch.object(audit.frappe, "local", SimpleNamespace(form_dict={"cmd": "test.command"})), \
             patch.object(audit.frappe, "get_doc", return_value=version) as create:
            with audit.audit_reason("Conciliar depósito", "Detalle corregido"):
                audit.record_transition("D", before, after)
        args = create.call_args.args[0]
        self.assertEqual((args["doctype"], args["ref_doctype"], args["docname"]), ("Version", "CN Remittance Allocation", "D"))
        evidence = json.loads(args["data"])["cn_reconciliation"]
        self.assertEqual(evidence["periods"], ["NEW", "OLD"])
        self.assertEqual(evidence["before"], before)
        self.assertEqual(evidence["after"], after)
        self.assertEqual(evidence["reason"], "Detalle corregido")
        self.assertIsNone(audit._context.get())
        version.insert.assert_called_once_with(ignore_permissions=True)

    def test_history_checks_parent_permission_and_paginates(self):
        deposit = frappe._dict(doctype="CN Remittance Allocation", name="D", check_permission=Mock())
        records = [frappe._dict(name=str(i), owner="operator", creation="2026-10-03", data=json.dumps({"cn_reconciliation": {"before": {}, "after": {}}})) for i in range(21)]
        with patch.object(audit.frappe, "get_doc", return_value=deposit), patch.object(audit.frappe, "get_all", return_value=records) as query:
            result = audit.get_history("D", 20)
        deposit.check_permission.assert_called_once_with("read")
        self.assertEqual(query.call_args.kwargs["limit_start"], 20)
        self.assertEqual((len(result["rows"]), result["has_more"], result["next_start"]), (20, True, 40))
