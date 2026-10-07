"""Verify availability changes never query/write the child table; rollback only."""
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot.cn_credit_portfolio_snapshot import set_portfolio_disabled


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    previous_user = frappe.session.user
    frappe.set_user("Administrator")
    marker = "FAST-CUT-" + frappe.generate_hash(length=8)
    try:
        cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot",
            "source_file": f"/private/files/{marker}.xlsx", "report_date": "2094-10-31", "status": "Importado",
            "rows": [{"credit_number": f"{marker}-{i}", "credit_status": "VIGENTE"} for i in range(1000)]}).insert()
        before = frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
        old_modified = str(cut.modified)
        started = perf_counter()
        with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            result = set_portfolio_disabled(cut.name, 1, old_modified)
        elapsed = perf_counter() - started
        assert not any("tabCN Credit Portfolio Row" in str(call.args[0]) for call in sql.call_args_list)
        after = frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
        assert before == after
        assert result["disabled"] == 1
        cut.reload()
        assert cut.disabled == 1 and cut.row_count == 1000
        assert str(cut.modified) == result["modified"]
        version = frappe.get_last_doc("Version", filters={"ref_doctype": cut.doctype, "docname": cut.name})
        assert frappe.parse_json(version.data)["changed"] == [["disabled", 0, 1]]
        try:
            set_portfolio_disabled(cut.name, 0, old_modified)
        except frappe.TimestampMismatchError:
            pass
        else:
            raise AssertionError("Stale request accepted")
        frappe.set_user("Guest")
        try:
            set_portfolio_disabled(cut.name, 0, result["modified"])
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Unauthorized user changed availability")
        finally:
            frappe.set_user("Administrator")
        frappe.db.set_value(cut.doctype, cut.name, "docstatus", 1, update_modified=False)
        result = set_portfolio_disabled(cut.name, 0, result["modified"])
        assert result["disabled"] == 0
        assert frappe.db.get_value(cut.doctype, cut.name, "docstatus") == 1
        frappe.db.set_value(cut.doctype, cut.name, "docstatus", 2, update_modified=False)
        try:
            set_portfolio_disabled(cut.name, 1, result["modified"])
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Canceled snapshot modified")
        return {"rows": len(before), "toggle_seconds": round(elapsed, 3), "child_queries": 0,
                "audit_recorded": True, "stale_and_unauthorized_rejected": True,
                "submitted_allowed": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(previous_user)
