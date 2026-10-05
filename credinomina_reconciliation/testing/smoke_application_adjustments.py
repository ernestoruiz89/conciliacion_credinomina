"""Rollback-only tests of real financial recalculation, not mocked reconciliation."""
import frappe

from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute as aging


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "ADJ-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                   "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        historical = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                     "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"}).insert()
        operative = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                    "payroll_month": "2026-09-01", "reconciliation_mode": "Operativa",
                                    "collection_cycle": "Mensual"})
        operative.append("collection_rows", {"row_key": marker, "client_name": "Cliente prueba",
                         "client_number": marker, "loan_number": marker + "-1", "expected_usd": 100,
                         "deducted_usd": 100, "deduction_status": "Deduccion total"})
        operative.insert()

        def source(period=None):
            document = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                                       "source_file": f"/private/files/{marker}-{period.name if period else 'none'}.csv",
                                       "status": "Importado", "currency": "USD"})
            document.append("rows", {"event_type": "Aplicacion", "event_date": "2025-04-15" if period == historical else "2026-09-15",
                "source_key": marker + (period.name if period else "unlinked"), "currency": "USD", "amount": 100,
                "amount_usd": 100, "effective": 1, "historical_period": historical.name if period == historical else "",
                "processing_route": "Historica" if period == historical else "Operativa", "employer_text": employer.name,
                "client_number": marker if period == operative else marker + "-H", "client_name": "Cliente prueba",
                "loan_number": marker + "-1" if period == operative else marker + "-H-1"})
            return document.insert()

        hist = source(historical)
        op = source(operative)
        _reconcile_sources(employer.name)
        hist.reload(); op.reload()
        assert op.rows[0].collection_period == operative.name

        def item(document, amount):
            return frappe.get_doc({"doctype": "CN Complementary Item", "employer": employer.name,
                "review_action": "Ajuste de aplicación", "category": "Ajuste de aplicación", "posting_date": "2026-10-01",
                "currency": "USD", "amount": amount, "application_adjustment_usd": amount,
                "related_application": document.rows[0].name, "description": "NC de prueba",
                "review_notes": "Ajuste documentado en core"}).insert()

        partial = item(hist, 30)
        assert frappe.db.get_value("CN Source Row", hist.rows[0].name, "net_applied_usd") == 100
        confirm_adjustment(partial.name)
        hist.reload(); historical.reload()
        assert hist.rows[0].amount == 100 and hist.rows[0].application_adjustment_usd == 30
        assert hist.rows[0].net_applied_usd == historical.applied_usd == hist.rows[0].historical_balance_usd == 70
        assert (historical.applied_total_usd, historical.remitted_total_usd, historical.pending_usd) == (100, 30, 70)
        balances = aging({"employer": employer.name, "as_of_date": "2026-10-15"})[1]
        assert sum(row["amount_usd"] for row in balances) == 170, balances
        full = item(hist, 70)
        confirm_adjustment(full.name)
        hist.reload(); historical.reload()
        assert hist.rows[0].amount == 100 and hist.rows[0].net_applied_usd == 0
        assert hist.rows[0].deposit_match_status == "Aplicación compensada"
        assert historical.applied_usd == 0 and historical.remitted_usd == 0 and historical.status == "Conciliado"
        assert (historical.applied_total_usd, historical.remitted_total_usd, historical.pending_usd) == (100, 100, 0)
        assert hist.exception_count == 0
        assert all(row.get("source_import") != hist.name for row in aging({"employer": employer.name})[1])
        try:
            item(hist, 1)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Over-adjustment accepted")
        # Cancel restores the net amount, not the original accounting evidence.
        full.reload(); full.cancel()
        hist.reload()
        assert hist.rows[0].net_applied_usd == 70 and hist.rows[0].amount == 100
        historical.reload()
        assert (historical.applied_total_usd, historical.remitted_total_usd, historical.pending_usd) == (100, 30, 70)
        # Operative partial reduction leaves the deduction intact and lowers applied.
        op_part = item(op, 25)
        confirm_adjustment(op_part.name)
        op.reload(); operative.reload()
        assert op.rows[0].net_applied_usd == 75
        assert operative.applied_usd == 75 and operative.deducted_usd == 100, operative.as_dict()
        assert (operative.applied_total_usd, operative.remitted_total_usd, operative.pending_usd) == (100, 25, 75)
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                    "deposit_reference": marker, "deposit_date": "2025-05-01",
                    "deposit_currency": "USD", "deposit_amount": 70}).insert()
        from credinomina_reconciliation.remittance_selection import get_pending_targets
        pending = get_pending_targets(deposit.name)["rows"]
        assert next(row for row in pending if row.get("historical_application") == hist.rows[0].name)["pending_cents"] == 7000
        assert not any(row.get("complementary_item") in {partial.name, op_part.name} for row in pending)
        deposit.append("targets", {"historical_application": hist.rows[0].name, "amount_usd": 70})
        deposit.save()
        reserved = item(hist, 10)
        try:
            confirm_adjustment(reserved.name)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Deposit target reservation must block adjustment")
        deposit.set("targets", [])
        deposit.save()
        deposit.append("targets", {"complementary_item": partial.name, "amount_usd": 30})
        try:
            deposit.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("An adjustment cannot also be a cash destination")
        # Metadata patch is idempotent and does not change original monetary evidence.
        from credinomina_reconciliation.patches.v1_0.initialize_application_net_amounts import execute as initialize
        initialize(); initialize()
        hist.reload()
        assert hist.rows[0].amount == 100 and hist.rows[0].net_applied_usd == 70
        # A confirmed offset plus the actual deposit cover the original 100 once.
        deposit.reload()
        deposit.set("targets", [])
        deposit.append("targets", {"historical_application": hist.rows[0].name, "amount_usd": 70})
        deposit.save(); deposit.submit()
        from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
        reconcile_deposit(deposit)
        historical.reload()
        assert (historical.applied_total_usd, historical.remitted_total_usd, historical.pending_usd) == (100, 100, 0)
        # Closed periods cannot accept adjustments, including cancellation.
        frappe.db.set_value("CN Reconciliation Period", historical.name, "status", "Cerrado")
        blocked = item(hist, 10)
        try:
            confirm_adjustment(blocked.name)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Closed period accepted adjustment")
        try:
            partial.reload(); partial.cancel()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Closed period accepted cancellation")
        from credinomina_reconciliation.patches.v1_0.backfill_period_financial_totals import execute as backfill
        historical.reload()
        evidence = {field: historical.get(field) for field in ("status", "modified", "historical_fingerprint",
                                                              "applied_usd", "remitted_usd")}
        frappe.db.set_value("CN Reconciliation Period", historical.name,
            dict(applied_total_usd=0, remitted_total_usd=0, pending_usd=99), update_modified=False)
        backfill(); backfill()
        historical.reload()
        assert (historical.applied_total_usd, historical.remitted_total_usd, historical.pending_usd) == (100, 100, 0)
        assert evidence == {field: historical.get(field) for field in evidence}
        return {"historical_partial_and_full": "OK", "operative_partial": "OK", "aging": "OK",
                "cancel_restores_net": "OK", "closed_period_and_overadjustment_guards": "OK",
                "deposit_guards_and_net_picker": "OK", "idempotent_patch": "OK",
                "gross_period_totals_and_closed_backfill": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
