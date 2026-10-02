"""Rollback-only smoke for pending deposit detail linked without detail_period."""

from uuid import uuid4

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    _pending_remittance_details_for_period,
    close_period,
    record_control_cut,
)


def _employer(marker, suffix):
    return frappe.get_doc({
        "doctype": "CN Employer", "employer_name": f"Smoke Link {marker}-{suffix}",
        "employer_code": f"SPL{marker}{suffix}", "payroll_frequency": "Mensual",
    }).insert(ignore_permissions=True)


def _period(employer, month):
    return frappe.get_doc({
        "doctype": "CN Reconciliation Period", "employer": employer.name,
        "payroll_month": month, "reconciliation_mode": "Operativa",
        "collection_cycle": "Mensual",
    }).insert(ignore_permissions=True)


def _remittance(employer, marker, suffix, amount, **fields):
    document = frappe.get_doc({
        "doctype": "CN Remittance Allocation", "employer": employer.name,
        "deposit_reference": f"LNK-{marker}-{suffix}",
        "deposit_voucher": f"V-{marker}-{suffix}",
        "deposit_date": "2026-11-15", "deposit_currency": "USD",
        "deposit_amount": amount, "notes": "Smoke: detalle de depósito pendiente",
        **fields,
    }).insert(ignore_permissions=True)
    document.submit()
    document._reconcile()  # Confirmation and reconciliation are separate actions.
    document.reload()
    assert document.detail_status == "Detalle pendiente", document.detail_status
    return document


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    marker = uuid4().hex[:8]
    try:
        employer = _employer(marker, "A")
        other_employer = _employer(marker, "B")
        period = _period(employer, "2026-10-01")
        other_period = _period(employer, "2026-11-01")
        foreign_period = _period(other_employer, "2026-10-01")
        period.append("collection_rows", {
            "row_key": marker + "-ROW", "client_name": "Cliente de prueba",
            "client_number": marker + "-CLIENT", "loan_number": marker + "-LOAN",
            "expected_usd": 50, "deducted_usd": 50,
            "deduction_status": "Deduccion total", "application_status": "Pendiente",
        })
        period.status = "Pendiente"
        period.save()

        by_target = _remittance(
            employer, marker, "TARGET", 60,
            targets=[{
                "period": period.name, "row_key": marker + "-ROW", "amount_usd": 50,
            }],
        )
        by_allocation = _remittance(employer, marker, "AUTO", 20)
        by_other_period = _remittance(
            employer, marker, "OTHER", 15, detail_period=other_period.name,
        )
        by_other_employer = _remittance(
            other_employer, marker, "FOREIGN", 30, detail_period=foreign_period.name,
        )
        frappe.db.set_value(
            "CN Remittance Allocation", by_allocation.name,
            "allocation_detail", '[{"tipo":"Cobranza","periodo":"' + period.name + '"}]',
            update_modified=False,
        )

        related = _pending_remittance_details_for_period(period)
        assert related == sorted([by_target.name, by_allocation.name]), related
        assert _pending_remittance_details_for_period(other_period) == [by_other_period.name]
        assert _pending_remittance_details_for_period(foreign_period) == [by_other_employer.name]
        try:
            close_period(period.name)
        except frappe.ValidationError as exc:
            assert "detalles de depósito" in str(exc), str(exc)
        else:
            raise AssertionError("El período cerró con detalle pendiente vinculado por target/allocación.")
        cut = record_control_cut(
            period.name, "Revisar los depósitos pendientes de detalle por cliente"
        )
        assert "detalles de depósito pendientes" in cut["summary"]
        assert "detalles de depósito pendientes 0" not in cut["summary"]
        print({
            "target_link": by_target.name in related,
            "allocation_link": by_allocation.name in related,
            "other_period_excluded": by_other_period.name not in related,
            "other_employer_excluded": by_other_employer.name not in related,
            "closure_blocked": True, "control_cut_counts_linked_detail": True,
            "rolled_back": True,
        })
    finally:
        frappe.db.rollback()
