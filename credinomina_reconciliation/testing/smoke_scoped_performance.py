"""Replay real shared-payer fixtures: scoped reads and no-op saves, rollback only."""
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.testing import smoke_paying_employers as fixtures
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.paying_employers import reconciliation_companies
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import reconcile_remittance


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    measurements = []
    original_get_doc = frappe.get_doc

    def reconcile_and_replay(employer):
        companies = set(reconciliation_companies(employer))

        def scoped_get_doc(*args, **kwargs):
            doc = original_get_doc(*args, **kwargs)
            if args and args[0] == "CN Accounting Import":
                assert doc.employer in companies, (doc.name, doc.employer)
            return doc

        with patch.object(frappe, "get_doc", side_effect=scoped_get_doc):
            first = _reconcile_sources(employer)
            started = perf_counter()
            replay = _reconcile_sources(employer)
            elapsed = perf_counter() - started
        assert replay["saved_imports"] == 0, replay
        for key in ("rows", "matched", "pending", "imports"):
            assert first[key] == replay[key], (key, first[key], replay[key])
        measurements.append({"rows": replay["rows"], "unchanged_saves": replay["saved_imports"],
                             "seconds": round(elapsed, 3)})
        return first

    with patch.object(fixtures, "_reconcile_sources", side_effect=reconcile_and_replay):
        fixtures.run()
    cross_month = _cross_month()
    print({"scoped_reads": "OK", "unchanged_imports_not_saved": "OK", "replays": measurements,
           "cross_month_and_outside_company": cross_month,
           "rolled_back": True})


def _cross_month():
    marker = "PERF-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            companies, imports, periods = [], [], []
            for suffix in ("A", "B"):
                company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker + suffix,
                                          "employer_code": marker + suffix, "payroll_frequency": "Mensual"}).insert()
                companies.append(company)
                period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name,
                                         "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
                                         "historical_scope": "Mensual", "collection_cycle": "Mensual"}).insert()
                periods.append(period)
                imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
                    "source_file": f"/private/files/{marker}-{suffix}.csv", "status": "Importado",
                    "historical_backfill": 1, "historical_period": period.name,
                    "rows": [{"source_row": 2, "source_key": marker + suffix, "event_type": "Aplicacion",
                        "event_date": "2025-04-15", "employer_text": company.name, "client_name": "Cliente " + suffix,
                        "client_number": marker + suffix, "loan_number": marker + suffix + "-1", "currency": "USD",
                        "amount": 100, "amount_usd": 100, "processing_route": "Historica",
                        "historical_period": period.name, "reference": marker + suffix, "effective": 1}]}).insert()
                imports.append(imported)
            imports[1].reload()
            outsider_before = imports[1].as_dict()
            deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": companies[0].name,
                "deposit_reference": marker, "deposit_date": "2025-06-20", "deposit_currency": "USD",
                "deposit_amount": 100, "targets": [{"historical_application": imports[0].rows[0].name,
                                                       "amount_usd": 100}]}).insert()
            deposit.submit()
            events = []
            with patch.object(frappe, "publish_realtime", side_effect=lambda event, data, **kwargs: events.append((event, data, kwargs))):
                result = reconcile_remittance(deposit.name, "test-progress")
            deposit.reload()
            periods[0].reload()
            imports[1].reload()
            assert result["imports"] == 1 and result["rows"] == 1, result
            assert deposit.allocated_usd == 100 and periods[0].remitted_usd == 100
            assert imports[1].as_dict() == outsider_before, "Changed unrelated company"
            progress = [entry for entry in events if entry[0] == "cn_remittance_reconciliation_progress"]
            assert [entry[1]["percent"] for entry in progress] == [5, 30, 50, 75, 90]
            assert all(entry[2]["user"] == "Administrator" for entry in progress)
            replay = reconcile_remittance(deposit.name)
            assert replay["saved_imports"] == 0, replay
            deposit.reload()
            deposit.cancel()
            periods[0].reload()
            assert periods[0].remitted_usd == 0, "Cancellation must release assignments within the same scope"
            return "OK"
    finally:
        frappe.db.rollback()
