"""Load detail and refresh portfolio evidence on drafts and submitted deposits."""
import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "DETAIL-CUT-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
            "client_number": marker, "client_name": "Cliente prueba corte"}).insert()
        cuts = []
        # Explicitly selected cuts may differ from the period month.
        for month, loan, state in ((4, "OLD", "SANEADO"), (5, "PREVIOUS", "CANCELADO"), (6, "CURRENT", "VIGENTE")):
            cuts.append(frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot",
                "source_file": f"/private/files/{marker}-{month}.xlsx", "status": "Importado",
                "report_date": f"2039-{month:02d}-28", "rows": [{
                    "credit_number": f"{marker}-{loan}-1", "credit_status": state,
                    "employer": employer.name, "matched_client": client.name,
                    "client_number_core": marker, "client_name": client.client_name}]}).insert())
        periods = []
        imports = []
        for month, cut in ((6, cuts[0]), (7, cuts[2])):
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": f"2025-{month:02d}-01", "reconciliation_mode": "Historica",
                "historical_scope": "Fecha exacta", "historical_application_date": f"2025-{month:02d}-15",
                "collection_cycle": "Mensual"}).insert()
            periods.append(period)
            imports.append(frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                "source_file": f"/private/files/{marker}-{month}.csv", "currency": "USD",
                "status": "Importado", "historical_period": period.name,
                "portfolio_snapshot": cut.name, "rows": [{
                    "source_key": f"{marker}-{month}", "event_type": "Aplicacion",
                    "event_date": f"2025-{month:02d}-15", "currency": "USD", "amount": 10, "amount_usd": 10,
                    "historical_period": period.name, "portfolio_snapshot_used": cut.name}]}).insert())
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_date": "2025-07-30", "deposit_reference": marker, "deposit_currency": "USD",
            "deposit_amount": 100, "detail_periods": [{"period": p.name} for p in reversed(periods)]}).insert()
        records = [{"source_row": i, "client_number": marker, "client_name": client.client_name,
                    "loan_number": f"{marker}-{loan}-1", "deducted_usd": 10}
                   for i, loan in enumerate(("CURRENT", "PREVIOUS", "OLD", "UNKNOWN"), 1)]
        _apply_remittance_detail(deposit, records, b"synthetic detail", f"/private/files/{marker}-detail.csv")
        deposit.reload()
        assert [r.portfolio_credit_status for r in deposit.detail_rows] == [
            "VIGENTE", "CANCELADO", "No Identificado", "No Identificado"]
        assert [str(r.portfolio_report_date) for r in deposit.detail_rows[:2]] == ["2039-06-28", "2039-05-28"]
        assert not deposit.detail_rows[2].portfolio_report_date
        assert deposit.detail_rows[1].portfolio_snapshot_used == cuts[1].name
        assert deposit.allocated_usd == 0
        # Automatic portfolio choices are stored on application rows.
        imports[-1].portfolio_snapshot = None
        imports[-1].save()
        _apply_remittance_detail(deposit, records, b"synthetic reload", f"/private/files/{marker}-detail.csv")
        deposit.reload()
        assert deposit.detail_rows[0].portfolio_snapshot_used == cuts[-1].name
        deposit.submit()
        deposit.detail_rows[0].loan_number = f"{marker}-PREVIOUS-1"
        deposit.save()
        deposit.reload()
        assert deposit.detail_rows[0].portfolio_credit_status == "CANCELADO"
        # Changing selected periods refreshes the source even after submission.
        deposit.set("detail_periods", [{"period": periods[0].name}])
        deposit.save()
        deposit.reload()
        assert deposit.detail_rows[2].portfolio_credit_status == "SANEADO"
        assert deposit.detail_rows[2].portfolio_snapshot_used == cuts[0].name
        assert deposit.detail_rows[0].portfolio_credit_status == "No Identificado"
        return {"loaded_detail_enriched": True, "latest_period_used": True,
                "unselected_previous_cut_used": True, "older_cut_not_searched": True,
                "submitted_credit_and_period_changes_refreshed": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
