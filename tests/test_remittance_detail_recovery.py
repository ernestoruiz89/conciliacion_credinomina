import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.allocation_origin import FIFO
from credinomina_reconciliation.remittance_detail_recovery import recovery_plan
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as controller


class ReimportRecoveryTests(unittest.TestCase):
    def case(self):
        old = dict(name="OLD", source_row=2, row_key="", client="C", client_number="C", national_id="ID",
            employer="EMP", loan_number="00123-1", deducted_nio=4242.61, amount_usd=115.84,
            identity_reason="Identificadores coincidentes", matched_targets=json.dumps([
                dict(claim_id="H:A", amount_usd=68.87, fifo_applications=[dict(application_id="A", amount_usd=68.87)])]))
        middle = {**old, "name": "MIDDLE", "loan_number": "", "matched_targets": "[]"}
        current = frappe._dict({**middle, "name": "CURRENT", "client": "", "client_number": "", "employer": ""})
        versions = [dict(data=json.dumps(dict(changed=[["detail_status", "Conciliado", "Cargado"]],
            removed=[["detail_rows", old]], added=[["detail_rows", middle]]))),
            dict(data=json.dumps(dict(changed=[], removed=[["detail_rows", middle]], added=[["detail_rows", current]])))]
        ledger = [dict(tipo="Aplicacion historica", aplicacion_id="A", fila_detalle="OLD", importe_usd=68.87,
            origen=FIFO, aplicaciones_fifo=[dict(application_id="A", amount_usd=68.87)])]
        doc = frappe._dict(name="DEP", doctype="CN Remittance Allocation", detail_rows=[current], targets=[],
            allocated_usd=68.87, allocation_detail=json.dumps(ledger))
        return doc, versions

    def test_two_reimports_recover_only_metadata_and_resolved_identity(self):
        doc, versions = self.case()
        before = copy.deepcopy(doc)
        plan = recovery_plan(doc, versions)
        self.assertEqual(doc, before)
        self.assertEqual(plan["mapping"], {"OLD": "CURRENT"})
        self.assertEqual(plan["updates"]["CURRENT"]["loan_number"], "00123-1")
        self.assertEqual(plan["updates"]["CURRENT"]["employer"], "EMP")
        self.assertEqual(plan["updates"]["CURRENT"]["client_number"], "C")
        self.assertEqual(plan["updates"]["CURRENT"]["linked_usd"], 68.87)
        self.assertEqual(plan["updates"]["CURRENT"]["pending_usd"], 46.97)
        recovered = json.loads(plan["ledger"])
        recovered[0]["fila_detalle"] = "OLD"
        self.assertEqual(recovered, json.loads(doc.allocation_detail))
        doc.allocation_detail = plan["ledger"]
        self.assertIsNone(recovery_plan(doc, versions))

    def test_changed_identity_amount_file_ambiguous_or_missing_evidence_never_recovers(self):
        for change in ("loan", "person", "employer", "deduction", "file", "cache", "duplicate", "missing", "capacity", "origin", "parts", "malformed"):
            with self.subTest(change=change):
                doc, versions = self.case()
                if change == "loan": doc.detail_rows[0].loan_number = "OTHER-1"
                if change == "person": doc.detail_rows[0].client_number = "OTHER"
                if change == "employer": doc.detail_rows[0].employer = "OTHER"
                if change == "deduction": doc.detail_rows[0].deducted_nio = 4242.62
                if change == "capacity": doc.detail_rows[0].amount_usd = 68.86
                if change == "missing": versions = []
                if change == "malformed": versions[0]["data"] = "invalid"
                if change in ("file", "cache", "duplicate"):
                    data = json.loads(versions[0]["data"])
                    if change == "file": data["changed"].append(["detail_hash", "OLD", "NEW"])
                    if change == "cache": data["removed"][0][1]["matched_targets"] = "[]"
                    if change == "duplicate": data["added"].append(["detail_rows", {**data["added"][0][1], "name": "DUP"}])
                    versions[0]["data"] = json.dumps(data)
                if change in ("origin", "parts"):
                    ledger = json.loads(doc.allocation_detail)
                    if change == "origin": ledger[0]["origen"] = "Selección manual"
                    if change == "parts": ledger[0]["aplicaciones_fifo"][0]["application_id"] = "OTHER"
                    doc.allocation_detail = json.dumps(ledger)
                self.assertIsNone(recovery_plan(doc, versions))

    def test_identical_file_does_not_save_replace_rows_or_clear_manual_links(self):
        doc, _ = self.case()
        doc.update(detail_hash=controller.file_sha256(b"same"), detail_source_file="detail.xlsx", save=Mock())
        doc.targets = [frappe._dict(detail_row="CURRENT", complementary_item="CORE", amount_usd=46.97)]
        before = copy.deepcopy(doc)
        with patch("credinomina_reconciliation.client_credit.guard_detail_replacement"), \
                patch.object(controller, "load_client_index") as clients:
            controller._apply_remittance_detail(doc, [], b"same", "detail.xlsx")
        doc.save.assert_not_called()
        clients.assert_not_called()
        self.assertEqual(doc.detail_rows, before.detail_rows)
        self.assertEqual(doc.targets, before.targets)
        self.assertEqual(doc.allocation_detail, before.allocation_detail)

    def test_different_file_cannot_delete_confirmed_rows(self):
        doc, _ = self.case()
        doc.update(detail_hash="OLD", save=Mock())
        with patch("credinomina_reconciliation.client_credit.guard_detail_replacement"), \
                self.assertRaisesRegex(frappe.ValidationError, "filas conciliadas"):
            controller._apply_remittance_detail(doc, [], b"different", "detail.xlsx")
        doc.save.assert_not_called()
