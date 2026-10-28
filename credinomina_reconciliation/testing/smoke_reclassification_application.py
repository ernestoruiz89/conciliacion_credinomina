"""Rollback-only integration test for split application adjustments."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reclassification_distribution import link_application, preview_destination


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "SPLIT-APP-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            origin = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-B", employer_code=marker + "-B")).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-06-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-06-30", collection_cycle="Mensual")).insert()
            parent = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, currency="USD",
                source_file=f"/private/files/{marker}.csv", status="Importado", historical_period=period.name,
                rows=[dict(event_type="Aplicacion", event_date="2025-06-30", source_key=marker,
                    client_name="Cliente destino", client_number="555", loan_number="123-1", amount=1,
                    amount_usd=1, currency="USD", effective=1, historical_period=period.name)])).insert()
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar",
                    review_action="Pendiente de revisión", employer=origin.name, posting_date="2025-07-01",
                    currency="USD", amount=.10, source_date="2025-07-01", source_currency="USD",
                    source_debit=0, source_credit=.10, accounting_source_key=marker, source_voucher=marker,
                    voucher=marker, voucher_line=marker, description="Ajuste global del core")).insert()
            frappe.db.set_value(item.doctype, item.name, dict(docstatus=1, category="Compensación entre partidas",
                review_action="Compensación entre partidas", compensation_status="Sin compensar",
                compensation_pending_usd=.10), update_modified=False)
            item.reload(); parent.reload()
            assert preview_destination(item.name, parent.name, 'application')['rows'] == []
            assert preview_destination(item.name, parent.name, 'application', override=1)['rows'][0]['pending_usd'] == 1
            result = link_application(item.name, parent.rows[0].name, str(item.modified), .01,
                                      override=True, reason="Ajuste global verificado")
            item.reload(); parent.reload()
            assert result["remaining_usd"] == .09
            assert parent.rows[0].application_adjustment_usd == .01, parent.rows[0].as_dict()
            assert parent.rows[0].net_applied_usd == .99
            assert preview_destination(item.name, parent.name, 'application', override=1)['rows'][0]['pending_usd'] == .99
            assert item.source_credit == .10 and item.amount_usd == .10
            other_company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-C",
                employer_code=marker + "-C")).insert()
            other_period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=other_company.name,
                payroll_month="2025-06-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-06-30", collection_cycle="Mensual")).insert()
            other_parent = frappe.get_doc(dict(doctype="CN Accounting Import", employer=other_company.name,
                currency="USD", source_file=f"/private/files/{marker}-other.csv", status="Importado",
                historical_period=other_period.name, rows=[dict(event_type="Aplicacion", event_date="2025-06-30",
                    source_key=marker + "-other", client_name="Otro cliente", client_number="556", loan_number="124-1",
                    amount=1, amount_usd=1, currency="USD", effective=1, historical_period=other_period.name)])).insert()
            second = link_application(item.name, other_parent.rows[0].name, str(item.modified), .02,
                                      override=True, reason="Segunda empresa del ajuste global")
            item.reload(); other_parent.reload(); parent.reload()
            assert second["remaining_usd"] == .07
            assert parent.rows[0].application_adjustment_usd == .01
            assert other_parent.rows[0].application_adjustment_usd == .02
            try:
                link_application(item.name, other_parent.rows[0].name, str(item.modified), .08,
                                 override=True, reason="Supera el remanente")
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("La suma de porciones no puede superar el importe del core")
            item.reload(); other_parent.reload()
            assert other_parent.rows[0].application_adjustment_usd == .02
            with imported_item(marker + "-IDENTITY"):
                unique = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar",
                    review_action="Pendiente de revisión", employer=other_company.name, client_name="Otro cliente",
                    posting_date="2025-07-01", currency="USD", amount=.02, source_date="2025-07-01",
                    source_currency="USD", source_debit=0, source_credit=.02, accounting_source_key=marker + "-IDENTITY",
                    source_voucher=marker + "-IDENTITY", voucher=marker + "-IDENTITY", voucher_line=marker + "-IDENTITY",
                    description="Ajuste de un crédito único")).insert()
            frappe.db.set_value(unique.doctype, unique.name, dict(docstatus=1, category="Compensación entre partidas",
                review_action="Compensación entre partidas", compensation_status="Sin compensar", compensation_pending_usd=.02),
                update_modified=False)
            unique.reload()
            for index in range(2):
                link_application(unique.name, other_parent.rows[0].name, str(unique.modified), .01,
                                 reason="Aplicación verificada")
                unique.reload(); other_parent.reload()
                if index == 0:
                    assert not unique.loan_number and not unique.client_number and not unique.reference
            assert (unique.loan_number, unique.client_number) == ("124-1", "556")
            assert not unique.reference  # A voucher is not a deposit reference.
            from credinomina_reconciliation.reclassification_distribution import allocations
            from credinomina_reconciliation.reclassification_removal import preview, remove
            operation = allocations(unique)[0]['operation_id']
            checked = preview(unique.name, operation)
            assert checked['remaining_usd'] == .01
            try:
                remove(unique.name, operation, checked['token'] + '-stale', 'Prueba de concurrencia')
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError('Una vista previa inválida no puede eliminar distribuciones')
            remove(unique.name, operation, checked['token'], 'Corrección del vínculo')
            unique.reload(); other_parent.reload()
            assert len(allocations(unique)) == 1
            assert other_parent.rows[0].application_adjustment_usd == .03
            return dict(application_adjustment=.03, original_remaining=.07,
                        different_company_authorized=True, rolled_back=True)
    finally:
        frappe.db.rollback()
