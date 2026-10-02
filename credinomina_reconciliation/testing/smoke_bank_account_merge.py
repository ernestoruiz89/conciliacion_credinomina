"""Real Frappe rename/merge with linked deposits; isolated site, rollback only."""
from unittest.mock import patch

import frappe


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    previous_user = frappe.session.user
    frappe.set_user("Administrator")
    marker = "BANKMERGE-" + frappe.generate_hash(length=10)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()

        def account(suffix, number):
            return frappe.get_doc({"doctype": "CN Bank Account", "account_name": marker + suffix,
                "bank_name": "Banco Prueba", "account_number": number, "currency": "NIO",
                "active": 1, "notes": suffix}).insert()

        source = account("-Origen", "001-234 567")
        target = account("-Destino", "001234567")
        different = account("-Distinta", "234567")
        deposits = []
        for index, bank in enumerate([source, source, target]):
            deposit = frappe.get_doc({"doctype": "CN Remittance Allocation",
                "employer": employer.name, "bank_account": bank.name,
                "deposit_reference": marker + str(index), "deposit_date": "2025-04-23",
                "deposit_currency": "NIO", "deposit_amount": 824.78, "fx_rate": 36.6243}).insert()
            if index == 1:
                deposit.submit()
            deposit.reload()
            deposits.append(deposit)
        fields = ["deposit_amount", "amount_usd", "fx_rate", "deposit_currency", "deposit_date",
                  "result", "docstatus", "allocated_usd", "unallocated_usd"]
        before = {doc.name: {key: doc.get(key) for key in fields} for doc in deposits}
        with patch.object(frappe, "enqueue"):
            try:
                frappe.rename_doc(source.doctype, source.name, different.name, merge=True,
                                  show_alert=False, rebuild_search=False)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("A suffix match must never authorize merging accounts")
            assert frappe.db.get_value(deposits[0].doctype, deposits[0].name, "bank_account") == source.name
            frappe.rename_doc(source.doctype, source.name, target.name, merge=True,
                              show_alert=False, rebuild_search=False)
            assert not frappe.db.exists(source.doctype, source.name)
            target.reload()
            assert target.account_name == target.name and target.notes == "-Destino"
            assert target.account_number == "001234567"
            for deposit in deposits:
                deposit.reload()
                assert deposit.bank_account == target.name
                assert {key: deposit.get(key) for key in fields} == before[deposit.name]
            assert frappe.db.exists("Comment", {"reference_doctype": target.doctype,
                "reference_name": target.name, "comment_type": "Edit"})
            renamed = marker + "-Renombrada"
            frappe.rename_doc(target.doctype, target.name, renamed, show_alert=False, rebuild_search=False)
            assert frappe.db.get_value(target.doctype, renamed, "account_name") == renamed
            assert all(frappe.db.get_value(doc.doctype, doc.name, "bank_account") == renamed for doc in deposits)
        return {"merge": "OK", "draft_and_submitted_links": "OK", "amounts_unchanged": "OK",
                "mismatch_blocked": "OK", "audit": "OK", "normal_rename": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(previous_user)
