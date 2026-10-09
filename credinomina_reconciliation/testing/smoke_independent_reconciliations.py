"""Both reconciliation orders, real cash and closure guards; rollback only."""
from unittest.mock import patch
import csv
import io

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.application_deposit_detail import preview_application_detail
from credinomina_reconciliation.remittance_selection import get_pending_targets
from credinomina_reconciliation.provisional_adjustments import generate_proposals
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as period_api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period, reconcile_first


def must_stay_open(period):
    try:
        close_period(period.name)
    except frappe.ValidationError:
        period.reload()
        assert period.status != "Cerrado"
    else:
        raise AssertionError("Closed with one reconciliation missing")


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            for basis, first, difference in (
                ("Cobranza", False, 0), ("Cobranza", True, 0),
                ("Detalle de empresa", False, 0), ("Detalle de empresa", True, 0),
                ("Cobranza", False, 0.01), ("Detalle de empresa", True, 0.01),
            ):
                marker = "INDEPENDENT-" + frappe.generate_hash(length=8)
                employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                    "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
                client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                    "client_name": marker, "client_number": marker}).insert()
                period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                    "payroll_month": "2026-09-01", "reconciliation_mode": "Operativa",
                    "collection_cycle": "Mensual", "application_basis": basis}).insert()
                source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                    "source_file": f"/private/files/{marker}.csv", "currency": "USD", "status": "Importado",
                    "historical_period": period.name, "rows": [{"event_type": "Aplicacion", "source_key": marker,
                        "event_date": "2026-09-30", "currency": "USD", "amount": 100, "amount_usd": 100,
                        "client": client.name, "client_name": marker, "client_number": marker,
                        "loan_number": "109136-1", "effective": 1, "historical_period": period.name}]}).insert()
                _reconcile_sources(employer.name, preserve_deposits=True)
                source.reload(); period.reload()
                assert not period.collection_rows and period.applied_usd == period.pending_usd == 100, period.as_dict()

                def add_collection():
                    period.reload()
                    def content(deduction=False):
                        stream = io.StringIO()
                        writer = csv.writer(stream)
                        writer.writerow(["Nro. Cliente", "Nombre y Apellidos del Cliente", "Nro. Crédito",
                                         "Monto de la cuota en US$", "Fila ID", *(["Deducido US$"] if deduction else [])])
                        writer.writerow([marker, marker, "109136-1", 100 + difference, marker,
                                         *([100 + difference] if deduction else [])])
                        return stream.getvalue().encode("utf-8")
                    with patch.object(period_api, "_attached_file", return_value=(
                        frappe._dict(file_name="cobranza.csv"), content())):
                        period_api.import_collection(period.name)
                    period.reload()
                    if basis == "Detalle de empresa":
                        period.employer_response_file = f"/private/files/{marker}-empresa.xlsx"
                        period.deduction_evidence_date = "2026-09-30"
                        period.save()
                        with patch.object(period_api, "_attached_file", return_value=(
                            frappe._dict(file_name="empresa.csv"), content(True))):
                            period_api.import_employer_response(period.name)
                    reconcile_first(period.name)
                    period.reload(); source.reload()
                    assert period.collection_rows[0].quality_status.startswith("Conforme") == (not difference)
                    assert period.collection_rows[0].deducted_usd == (100 + difference if basis == "Detalle de empresa" else 0)

                if first:
                    add_collection()
                    assert period.remitted_usd == 0
                    must_stay_open(period)
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                    "deposit_date": "2026-10-01", "deposit_reference": marker, "deposit_currency": "USD",
                    "deposit_amount": 100, "detail_periods": [{"period": period.name}],
                    "detail_file": f"/private/files/{marker}.xlsx", "detail_source_file": f"/private/files/{marker}.xlsx",
                    "detail_hash": marker, "detail_rows": [{"client": client.name, "client_number": marker,
                        "client_name": marker, "loan_number": "109136-1", "deducted_usd": 100, "source_row": 2}]}).insert()
                preview = preview_application_detail(deposit.name)
                assert len(preview["rows"]) == 1 and preview["total_usd"] == 100, preview
                picker = get_pending_targets(deposit.name)
                assert any(row.get("historical_application") == source.rows[0].name for row in picker["rows"]), picker
                deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload(); source.reload(); period.reload()
                assert deposit.allocated_usd == 100 and deposit.detail_rows[0].pending_usd == 0, deposit.as_dict()
                assert source.rows[0].deposit_match_status == "Depósito conciliado", source.rows[0].as_dict()
                assert period.applied_usd == period.remitted_usd == 100 and period.pending_usd == 0, period.as_dict()
                cash = deposit.allocation_detail
                if not first:
                    assert not period.collection_rows
                    must_stay_open(period)
                    add_collection()
                for _ in range(2):
                    reconcile_first(period.name)
                    deposit.reload(); period.reload(); source.reload()
                    assert deposit.allocation_detail == cash
                    assert period.applied_total_usd == period.remitted_total_usd == 100 and period.pending_usd == 0, period.as_dict()
                    assert period.collection_rows[0].application_status == "Aplicado y remitido", period.collection_rows[0].as_dict()
                    assert not preview_application_detail(deposit.name)["rows"] or preview_application_detail(deposit.name)["total_usd"] == 100
                if difference:
                    generate_proposals(period.name)
                    period.reload()
                    assert period.status == "Parcial"
                    assert period.collection_rows[0].quality_difference_usd == -difference
                    assert period.provisional_adjustments[0].state == "Pendiente de revisión"
                    assert source.rows[0].quality_status == "Con diferencias"
                close_period(period.name)
                period.reload()
                assert period.status == "Cerrado"
                if difference:
                    assert period.status_before_close == "Parcial"
                    assert period.collection_rows[0].quality_difference_usd == -difference
                    assert period.provisional_adjustments[0].state == "Pendiente de revisión"
                    assert period.applied_total_usd == period.remitted_total_usd == 100
                    assert period.pending_usd == 0
                    assert not frappe.db.exists("CN Complementary Item", {"period": period.name})
                    _reconcile_sources(employer.name, preserve_deposits=True)
                    period.reload(); deposit.reload()
                    assert period.status == "Cerrado" and deposit.allocation_detail == cash
            return {"second_without_payroll": True, "first_without_deposit": True,
                    "both_required_to_close": True, "late_payroll_preserves_cash": True,
                    "informational_difference_does_not_block_close": True,
                    "pending_proposal_and_cash_preserved": True,
                    "no_double_counting": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
