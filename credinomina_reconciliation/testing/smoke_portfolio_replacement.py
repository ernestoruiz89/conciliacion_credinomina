"""Replace a disabled monthly portfolio without losing old rows; rollback only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as portfolio


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "REPLACE-" + frappe.generate_hash(length=8)
    doctype = "CN Credit Portfolio Snapshot"

    def draft():
        return frappe.get_doc({"doctype": doctype, "source_file": f"/private/files/{marker}.xlsx"}).insert()

    def load(doc, date="2092-07-31", content=None):
        records = [{"source_row": 2, "report_date": date, "credit_number": marker + "-1",
                    "client_name": "Prueba", "is_convenio": "No", "credit_status": "Corriente"}]
        with patch.object(portfolio, "_attached_file", return_value=(frappe._dict(file_name="test.xlsx"), content or marker.encode())), \
             patch.object(portfolio, "parse_credit_portfolio", return_value=records):
            result = portfolio.import_portfolio_snapshot(doc.name)
        return frappe.get_doc(doctype, result["snapshot_name"])

    def rejected(action):
        frappe.db.savepoint("replacement_rejected")
        try:
            action()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("A second active portfolio was accepted")
        finally:
            frappe.db.rollback(save_point="replacement_rejected")

    try:
        old = load(draft())
        assert old.name == "CARTERA-7-2092"
        original_rows = frappe.get_all("CN Credit Portfolio Row", filters={"parent": old.name}, fields=["*"])
        replacement = draft()
        rejected(lambda: load(replacement, "2092-07-15", (marker + "-different").encode()))
        rejected(lambda: load(replacement))  # Identical file is blocked while active.
        portfolio.set_portfolio_disabled(old.name, 1, str(old.modified))
        replacement = load(replacement)  # The same file is allowed after disabling its old cut.
        assert replacement.name == "CARTERA-7-2092-2" and not replacement.disabled
        assert original_rows == frappe.get_all("CN Credit Portfolio Row", filters={"parent": old.name}, fields=["*"])
        old.reload()
        # Both the fast checkbox and ordinary Save enforce the same rule.
        with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            rejected(lambda: portfolio.set_portfolio_disabled(old.name, 0, str(old.modified)))
        assert not any("tabCN Credit Portfolio Row" in str(call.args[0]) for call in sql.call_args_list)
        old.disabled = 0
        rejected(old.save)
        old.reload()
        # A confirmed cut also cannot bypass the active-month rule.
        frappe.db.set_value(doctype, old.name, "docstatus", 1, update_modified=False)
        old.reload()
        rejected(lambda: portfolio.set_portfolio_disabled(old.name, 0, str(old.modified)))
        portfolio.set_portfolio_disabled(replacement.name, 1, str(replacement.modified))
        portfolio.set_portfolio_disabled(old.name, 0, str(old.modified))
        old.reload()
        assert old.docstatus == 1 and not old.disabled
        # Same month in another year and another month in the same year are independent.
        assert load(draft(), "2093-07-31", (marker + "-year").encode()).name == "CARTERA-7-2093"
        assert load(draft(), "2092-08-31", (marker + "-month").encode()).name == "CARTERA-8-2092"
        portfolio.set_portfolio_disabled(old.name, 1, str(old.modified))
        third = load(draft())
        assert third.name == "CARTERA-7-2092-3"
        return {"disabled_cut_replaced": True, "same_file_allowed_after_disabling": True,
                "distinct_names": True, "reactivation_guarded": True, "submitted_guarded": True,
                "old_rows_preserved": True, "toggle_child_queries": 0, "rolled_back": True}
    finally:
        frappe.db.rollback()
