"""A September cent adjustment must not recalculate a closed July period."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_adjustments import confirm_adjustment, MIXED_STATUS
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "ADJ-SCOPE-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker,
                payroll_frequency="Mensual", rounding_tolerance_usd=0)).insert()
            periods = [frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month=month, reconciliation_mode="Historica")).insert()
                for month in ("2025-07-01", "2025-09-01")]
            july, september = periods
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD", historical_backfill=1,
                rows=[dict(event_type="Aplicacion", event_date=day, currency="USD", amount=amount,
                    amount_usd=amount, effective=1, historical_period=period.name, source_key=marker + day)
                    for period, day, amount in [(july, "2025-07-15", 10), (september, "2025-09-30", 25.96)]])).insert()
            engine._reconcile_sources(employer.name, preserve_deposits=True)
            source.reload()
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2025-09-30", deposit_reference=marker, deposit_currency="USD", deposit_amount=25.95,
                targets=[dict(historical_application=source.rows[1].name, amount_usd=25.95)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            source.reload(); september.reload(); deposit.reload()
            assert source.rows[1].historical_balance_usd == 0.01
            # Keep a legacy closed period which a company-wide pass would reject.
            frappe.db.set_value(july.doctype, july.name, dict(status="Cerrado",
                applied_usd=10, remitted_usd=10, historical_fingerprint="legacy-closed-fingerprint"), update_modified=False)
            july.reload()
            july_before = july.as_dict()
            # Frappe refreshes child modified timestamps when saving the shared
            # parent. All financial/identity/state fields must stay unchanged.
            def row_evidence(row):
                return {key: value for key, value in row.as_dict().items() if key not in {"modified", "modified_by"}}
            july_row_before = row_evidence(source.rows[0])
            deposit_before = deposit.as_dict()
            item = frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name, period=september.name,
                category="Ajuste de aplicación", review_action="Ajuste de aplicación", posting_date="2026-10-09",
                currency="USD", amount=0.01, application_adjustment_usd=0.01,
                related_application=source.rows[1].name, description="Inmaterial", review_notes="Inmaterial")).insert()
            # Guard against accidentally falling back to the company-wide engine.
            with patch.object(engine, "_reconcile_sources", side_effect=AssertionError("Company-wide reconciliation")):
                confirm_adjustment(item.name)
            source.reload(); september.reload(); deposit.reload()
            assert source.rows[1].amount == 25.96 and source.rows[1].net_applied_usd == 25.95
            assert source.rows[1].historical_remitted_usd == 25.95
            assert source.rows[1].historical_balance_usd == 0
            assert source.rows[1].deposit_match_status == MIXED_STATUS
            assert (september.applied_total_usd, september.remitted_total_usd, september.pending_usd) == (25.96, 25.96, 0)
            assert row_evidence(source.rows[0]) == july_row_before
            assert frappe.get_doc(july.doctype, july.name).as_dict() == july_before
            assert deposit.as_dict() == deposit_before
            # Cancellation uses the same scoped cash-preserving pass.
            item.reload(); item.cancel()
            source.reload(); deposit.reload()
            assert source.rows[1].historical_balance_usd == 0.01
            assert row_evidence(source.rows[0]) == july_row_before
            assert frappe.get_doc(july.doctype, july.name).as_dict() == july_before
            assert deposit.as_dict() == deposit_before
            unassigned = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}-unassigned.csv", status="Importado", currency="USD",
                rows=[dict(event_type="Aplicacion", event_date="2026-09-30", currency="USD", amount=5,
                    amount_usd=5, effective=1, source_key=marker + "-unassigned")])).insert()
            full = frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name,
                category="Ajuste de aplicación", review_action="Ajuste de aplicación", posting_date="2026-10-09",
                currency="USD", amount=5, application_adjustment_usd=5, related_application=unassigned.rows[0].name,
                description="Ajuste sin período", review_notes="Ajuste documentado")).insert()
            confirm_adjustment(full.name)
            unassigned.reload()
            assert unassigned.rows[0].net_applied_usd == 0
            assert unassigned.rows[0].deposit_match_status == "Aplicación compensada"
            assert unassigned.exception_count == 0
            assert frappe.get_doc(july.doctype, july.name).as_dict() == july_before
            return dict(cent_adjustment_confirmed=True, unrelated_closed_period_unchanged=True,
                same_import_other_row_unchanged=True, deposit_unchanged=True, cancellation_scoped=True,
                unassigned_full_adjustment_scoped=True, rolled_back=True)
    finally:
        frappe.db.rollback()
