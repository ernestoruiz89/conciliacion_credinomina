"""Real snapshot rename, link preservation and disabled selection; rollback only."""
import frappe

from credinomina_reconciliation.bulk_accounting_import import _required_portfolio_snapshot
from credinomina_reconciliation.credit_portfolio import enrich_accounting_records
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import get_company_portfolio_snapshots


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "CUT-AVAIL-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        cuts = []
        for month in (6, 7):
            cuts.append(frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot",
                "source_file": f"/private/files/{marker}-{month}.xlsx", "status": "Importado",
                "report_date": f"2096-{month:02d}-28", "rows": [{"credit_number": marker,
                    "employer": employer.name, "client_name": "Cliente prueba"}]}).insert())
        cut = cuts[1]
        source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "source_file": f"/private/files/{marker}.csv", "currency": "USD", "status": "Importado",
            "portfolio_snapshot": cut.name, "rows": [{"event_type": "Aplicacion", "event_date": "2025-07-15",
                "source_key": marker, "currency": "USD", "amount": 10, "amount_usd": 10,
                "portfolio_snapshot_used": cut.name}]}).insert()
        # Use the ordinary permission-checked rename API, with no force bypass.
        new_name = frappe.rename_doc(cut.doctype, cut.name, marker + "-RENAMED", rebuild_search=False)
        cut = frappe.get_doc(cut.doctype, new_name)
        source.reload()
        assert source.portfolio_snapshot == new_name
        assert source.rows[0].portfolio_snapshot_used == new_name
        assert cut.rows[0].parent == new_name
        cut.disabled = 1
        cut.save()
        source.notes = "Se conserva el vínculo histórico"
        source.save()
        assert source.portfolio_snapshot == new_name
        choices = get_company_portfolio_snapshots(cut.doctype, "", "name", 0, 20, {"employer": employer.name})
        assert [row[0] for row in choices] == [cuts[0].name]
        for action in (lambda: _required_portfolio_snapshot(new_name),
                       lambda: enrich_accounting_records([], new_name)):
            try:
                action()
            except frappe.ValidationError as exc:
                assert "desactivado" in str(exc)
            else:
                raise AssertionError("Disabled snapshot accepted")
        result = enrich_accounting_records([{"event_type": "Aplicacion", "event_date": "2096-07-15",
            "loan_number": marker}], employer=employer.name, register_clients=False)
        assert result[0]["portfolio_snapshot_used"] == cuts[0].name
        # Support legacy/custom submitted snapshots without making this DocType submittable.
        frappe.db.set_value(cut.doctype, cut.name, "docstatus", 1)
        for row in cut.rows:
            frappe.db.set_value(row.doctype, row.name, "docstatus", 1)
        cut.reload()
        cut.disabled = 0
        cut.save()
        cut.reload()
        assert cut.docstatus == 1 and not cut.disabled
        assert _required_portfolio_snapshot(new_name) == new_name
        choices = get_company_portfolio_snapshots(cut.doctype, "", "name", 0, 20, {"employer": employer.name})
        assert new_name in [row[0] for row in choices]
        return {"rename_preserves_links": True, "disabled_excluded": True,
                "automatic_fallback": True, "existing_links_preserved": True,
                "editable_after_submit": True, "reactivation": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
