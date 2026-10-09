"""Verify picker eligibility and real linking against rollback-only fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_review import application_candidates
from credinomina_reconciliation.application_adjustments import confirm_adjustment


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "PICKER-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Mensual")).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD",
                historical_backfill=1, historical_period=period.name, rows=[dict(
                    event_type="Aplicacion", event_date="2025-07-15", currency="USD", amount=100,
                    amount_usd=100, effective=1, historical_period=period.name, source_key=marker)])).insert()

            def new_item(amount):
                return frappe.get_doc(dict(doctype="CN Complementary Item", employer=employer.name,
                    category="Ajuste de aplicación", review_action="Ajuste de aplicación",
                    posting_date="2026-10-01", currency="USD", amount=amount,
                    description="Ajuste de prueba", review_notes="Ajuste documentado de prueba")).insert()

            item = new_item(30)
            source.reload()
            original = source.as_dict()
            candidates = application_candidates(item.name, source.name)
            assert len(candidates) == 1 and candidates[0]["adjustable_usd"] == 100
            assert frappe.get_doc(source.doctype, source.name).as_dict() == original
            item.related_application = candidates[0]["name"]
            item.application_adjustment_usd = 30
            item.save()
            confirm_adjustment(item.name)
            pending = new_item(70)
            candidates = application_candidates(pending.name, source.name)
            assert len(candidates) == 1 and candidates[0]["adjustable_usd"] == 70
            assert candidates[0]["application_adjustment_usd"] == 30
            # Draft deposit targets reserve cash, too.
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2025-07-15", deposit_reference=marker, deposit_currency="USD", deposit_amount=70,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=70)])).insert()
            assert application_candidates(pending.name, source.name) == []
            deposit.set("targets", [])
            deposit.save()
            frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
            assert application_candidates(pending.name, source.name) == []
            frappe.db.set_value(period.doctype, period.name, "status", "Pendiente")
            candidates = application_candidates(pending.name, source.name)
            assert len(candidates) == 1
            pending.related_application = candidates[0]["name"]
            pending.application_adjustment_usd = 70
            pending.save()
            confirm_adjustment(pending.name)
            source.reload()
            assert source.rows[0].deposit_match_status == "Aplicación compensada"
            assert source.exception_count == 0
            another = new_item(1)
            assert application_candidates(another.name, source.name) == []
            return dict(valid_candidate_linked_and_confirmed=True, confirmed_adjustments_respected=True,
                deposit_reservations_excluded=True, closed_period_excluded=True,
                fully_adjusted_excluded=True, rolled_back=True)
    finally:
        frappe.db.rollback()
