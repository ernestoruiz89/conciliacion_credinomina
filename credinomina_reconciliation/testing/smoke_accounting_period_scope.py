"""Selected-period reconciliation preserves other periods and shared deposits."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    results = []
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            for mode, month in (("Historica", "2025-07"), ("Operativa", "2026-09")):
                marker = "SCOPE-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "client_name": marker,
                    "client_number": marker, "employer": employer.name}).insert()
                periods = []
                for index, day in enumerate(("15", "30")):
                    period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                        "payroll_month": month + "-01", "reconciliation_mode": mode,
                        "collection_cycle": "Primera quincena" if index == 0 else "Segunda quincena",
                        "historical_scope": "Fecha exacta", "historical_application_date": month + "-" + day,
                        "application_basis": "Cobranza"})
                    if mode == "Operativa":
                        period.append("collection_rows", {"client": client.name, "client_name": marker,
                            "client_number": marker, "loan_number": f"18565{index}-1", "row_key": marker + str(index),
                            "expected_usd": 50, "deducted_usd": 50, "currency": "USD"})
                    periods.append(period.insert())
                imports = []
                for index, period in enumerate(periods):
                    # Keep the other operative application on a payroll claim
                    # so shared cash requires read-only evidence from that period.
                    assigned = "" if mode == "Operativa" and index == 1 else period.name
                    imports.append(frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                        "historical_period": assigned, "currency": "USD", "status": "Importado",
                        "source_file": f"/private/files/{marker}-{index}.csv",
                        "rows": [{"event_type": "Aplicacion", "source_key": marker + str(index),
                            "event_date": month + ("-15" if index == 0 else "-30"), "currency": "USD",
                            "amount": 50, "amount_usd": 50, "client": client.name, "client_number": marker,
                            "client_name": marker, "loan_number": f"18565{index}-1", "effective": 1,
                            "historical_period": assigned}]}).insert())
                api._reconcile_sources(employer.name, preserve_deposits=True)
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                    "deposit_date": month + "-30", "deposit_reference": marker, "deposit_currency": "USD",
                    "deposit_amount": 100, "detail_periods": [{"period": period.name} for period in periods],
                    "detail_file": f"/private/files/{marker}.xlsx", "detail_source_file": f"/private/files/{marker}.xlsx",
                    "detail_hash": marker, "detail_rows": [{"client": client.name, "client_number": marker,
                        "client_name": marker, "loan_number": f"18565{index}-1", "deducted_usd": 50,
                        "source_row": index + 2} for index in range(2)]}).insert()
                deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.allocated_usd == 100, deposit.as_dict()
                before_deposit = document_state(deposit)
                before_period = document_state(periods[1].reload())
                before_import = document_state(imports[1].reload())
                with patch.object(api, "load_scoped_imports", side_effect=AssertionError("Loaded company history")), \
                     patch.object(api, "_load_open_periods", side_effect=AssertionError("Loaded other periods")), \
                     patch.object(api, "_sync_registered_deposit_detail", side_effect=AssertionError("Rewrote deposits")):
                    result = api.reconcile_company_sources(imports[0].name)
                assert result["period"] == periods[0].name and result["rows"] == 1 and result["imports"] == 1, result
                assert result["matched"] == 1, result
                assert document_state(deposit.reload()) == before_deposit
                assert document_state(periods[1].reload()) == before_period
                assert document_state(imports[1].reload()) == before_import
                assert periods[0].reload().applied_usd == periods[0].remitted_usd == 50
                results.append(mode)
            return {"modes": results, "only_default_period": True, "other_imports_and_periods_unchanged": True,
                    "shared_deposit_preserved": True, "no_company_history_load": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
