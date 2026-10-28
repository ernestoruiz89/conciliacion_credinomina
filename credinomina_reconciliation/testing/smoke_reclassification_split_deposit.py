"""Rollback-only verification of a partial cross-company cash reservation."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reclassification_distribution import (
    link_deposit, COMPANY_CREDIT, INCOME, OTHER, allocations, deposit_credits,
    record_credit_management, reverse_credit_management,
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "SPLIT-CASH-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            origin = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-B", employer_code=marker + "-B")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_name="Cliente destino", client_number=marker)).insert()
            loan = marker + "-1"
            frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot", source_file=f"/private/files/{marker}.xlsx",
                status="Importado", report_date="2039-04-28", rows=[dict(credit_number=loan, credit_status="CANCELADO",
                    employer=company.name, matched_client=client.name, client_number_core=marker, client_name=client.client_name)])).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-07-31", collection_cycle="Mensual")).insert()
            file = f"/private/files/{marker}.csv"
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-08-07", deposit_currency="USD", deposit_amount=1,
                detail_file=file, detail_source_file=file, detail_hash=marker, detail_count=1,
                detail_periods=[dict(period=period.name)], detail_rows=[dict(client=client.name,
                    client_name=client.client_name, client_number=marker, employer=company.name,
                    loan_number=loan, deducted_usd=1, source_row=2)])).insert()
            deposit.submit(); reconcile_deposit(deposit)
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar",
                    review_action="Pendiente de revisión", employer=origin.name, posting_date="2025-11-21",
                    currency="USD", amount=.10, source_date="2025-11-21", source_currency="USD", source_debit=.10,
                    source_credit=0, accounting_source_key=marker, source_voucher=marker, voucher=marker,
                    voucher_line=marker, description="Ajuste global del core")).insert()
            frappe.db.set_value(item.doctype, item.name, dict(docstatus=1, category="Compensación entre partidas",
                review_action="Compensación entre partidas", compensation_status="Sin compensar",
                compensation_pending_usd=.10), update_modified=False)
            item.reload(); deposit.reload()
            result = link_deposit(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                                  str(deposit.modified), .01, override=True, reason="Ajuste global verificado")
            item.reload(); deposit.reload()
            assert result["remaining_usd"] == .09
            assert deposit.detail_rows[0].client_credit_usd == .01
            assert deposit.detail_rows[0].pending_usd == .99
            assert deposit.justified_surplus_usd == .01
            assert item.source_debit == .10 and item.amount_usd == .10
            second = link_deposit(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                                  str(deposit.modified), .02, override=True, reason="Segunda porción verificada")
            item.reload(); deposit.reload()
            assert second["remaining_usd"] == .07
            assert deposit.detail_rows[0].client_credit_usd == .03
            assert deposit.detail_rows[0].pending_usd == .97
            assert deposit.justified_surplus_usd == .03
            company_credit = link_deposit(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                str(deposit.modified), .01, kind=COMPANY_CREDIT, override=True, reason="Pago duplicado de la empresa")
            item.reload(); deposit.reload()
            assert company_credit["remaining_usd"] == .06 and deposit.detail_rows[0].company_credit_usd == .01
            income = link_deposit(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                str(deposit.modified), .01, kind=INCOME, override=True, reason="Ingreso identificado")
            item.reload(); deposit.reload()
            assert income["remaining_usd"] == .05 and deposit.detail_rows[0].linked_usd == .01
            other = link_deposit(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                str(deposit.modified), .01, kind=OTHER, override=True, reason="Otro concepto sin cuenta por cobrar")
            item.reload(); deposit.reload()
            assert other["remaining_usd"] == .04 and deposit.detail_rows[0].linked_usd == .02
            assert deposit.detail_rows[0].client_credit_usd == .03 and deposit.detail_rows[0].company_credit_usd == .01
            assert deposit.detail_rows[0].pending_usd == .94 and deposit.justified_surplus_usd == .04
            from credinomina_reconciliation.deposit_distribution import get_distribution
            table = get_distribution(deposit.name)
            assert table["consistent"], table
            types = {row["category"] for row in table["rows"]}
            assert {"Saldo a favor del cliente", "Saldo a favor de la empresa", "Ingreso",
                    "Otro sin saldo a favor ni cuenta por cobrar"} <= types, table
            # A payment duplicated by the company remains a company credit;
            # only proven management resolves it, and reversal restores it.
            management = record_credit_management(item.name, company_credit["operation_id"], str(item.modified),
                "Devolución", .01, "2025-12-01", "DEV-EMPRESA")
            assert management["pending_usd"] == 0
            item.reload()
            company_entry = next(entry for entry in allocations(item) if entry["operation_id"] == company_credit["operation_id"])
            management_id = company_entry["management_history"][0]["operation_id"]
            before = deposit_credits([item], {deposit.name}, cutoff="2025-11-30")
            after = deposit_credits([item], {deposit.name}, cutoff="2025-12-01")
            assert next(row for row in before if row.reclassification_operation_id == company_credit["operation_id"]).credit_pending_usd == .01
            assert next(row for row in after if row.reclassification_operation_id == company_credit["operation_id"]).credit_pending_usd == 0
            reversal = reverse_credit_management(item.name, company_credit["operation_id"], str(item.modified),
                management_id, "2025-12-02", "Corrección de la gestión")
            assert reversal["pending_usd"] == .01
            item.reload()
            assert next(row for row in deposit_credits([item], {deposit.name})
                        if row.reclassification_operation_id == company_credit["operation_id"]).credit_pending_usd == .01
            for allocation_id in (income["operation_id"], other["operation_id"]):
                try:
                    record_credit_management(item.name, allocation_id, str(item.modified),
                        "Devolución", .01, "2025-12-03", "NO-CORRESPONDE")
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Ingreso u otro no puede generar una gestión de saldo a favor")
            try:
                reverse_credit_management(item.name, company_credit["operation_id"], str(item.modified),
                    management_id, "2025-12-03", "Reversión duplicada")
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("No se puede revertir dos veces la misma gestión")
            deposit.reload()
            assert deposit.detail_rows[0].company_credit_usd == .01
            third_company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-C",
                employer_code=marker + "-C")).insert()
            other_client = frappe.get_doc(dict(doctype="CN Client", employer=third_company.name,
                client_name="Otro cliente", client_number=marker + "-C")).insert()
            frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot", source_file=f"/private/files/{marker}-other.xlsx",
                status="Importado", report_date="2039-05-29", rows=[dict(credit_number=marker + "-2", credit_status="CANCELADO",
                    employer=third_company.name, matched_client=other_client.name,
                    client_number_core=other_client.client_number, client_name=other_client.client_name)])).insert()
            other_period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=third_company.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-07-31", collection_cycle="Mensual")).insert()
            other_file = f"/private/files/{marker}-other.csv"
            other_deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=third_company.name,
                deposit_reference=marker + "-C", deposit_date="2025-08-08", deposit_currency="USD", deposit_amount=1,
                detail_file=other_file, detail_source_file=other_file, detail_hash=marker + "-C", detail_count=1,
                detail_periods=[dict(period=other_period.name)], detail_rows=[dict(client=other_client.name,
                    client_name=other_client.client_name, client_number=other_client.client_number,
                    employer=third_company.name, loan_number=marker + "-2", deducted_usd=1, source_row=2)])).insert()
            other_deposit.submit(); reconcile_deposit(other_deposit); other_deposit.reload()
            second_company_credit = link_deposit(item.name, other_deposit.name, other_deposit.detail_rows[0].name,
                str(item.modified), str(other_deposit.modified), .01, kind=COMPANY_CREDIT, override=True,
                reason="Otra empresa del ajuste global")
            assert second_company_credit["remaining_usd"] == .03
            other_deposit.reload(); deposit.reload(); item.reload()
            assert other_deposit.detail_rows[0].company_credit_usd == .01
            assert deposit.detail_rows[0].company_credit_usd == .01
            # Standard Frappe document loads must never expose the raw journal
            # to a role that can read the origin but not all its destinations.
            email = marker.lower() + "@example.invalid"
            operator = frappe.get_doc(dict(doctype="User", email=email, first_name=marker,
                enabled=1, user_type="System User", send_welcome_email=0,
                roles=[dict(role="Supervisor Credinomina")])).insert(ignore_permissions=True)
            frappe.set_user(operator.name)
            visible = frappe.get_doc("CN Complementary Item", item.name)
            visible.apply_fieldlevel_read_permissions()
            assert not visible.as_dict().get("reclassification_allocations")
            visible.save()
            item.reload()
            assert len(allocations(item)) == 6, "Guardar no debe borrar el historial restringido"
            from frappe.client import get_value
            try:
                raw = get_value("CN Complementary Item", "reclassification_allocations", item.name)
            except frappe.PermissionError:
                raw = None
            assert not raw or not raw.get("reclassification_allocations")
            from credinomina_reconciliation.reclassification_distribution import distribution_history
            assert len(distribution_history(item.name)["entries"]) == 6
            assert get_distribution(deposit.name)["consistent"]
            authorized = link_deposit(item.name, other_deposit.name, other_deposit.detail_rows[0].name,
                str(item.modified), str(other_deposit.modified), .01, kind=INCOME, override=True,
                reason="Ingreso del movimiento global revisado")
            assert authorized['remaining_usd'] == .02
            other_deposit.reload()
            assert other_deposit.detail_rows[0].linked_usd == .01
            real_permission = frappe.has_permission
            def deny_deposit(doctype, *args, **kwargs):
                if doctype == "CN Remittance Allocation":
                    return False
                return real_permission(doctype, *args, **kwargs)
            with patch.object(frappe, "has_permission", side_effect=deny_deposit):
                try:
                    distribution_history(item.name)
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError("No se debe revelar una distribución con destino restringido")
            frappe.set_user("Administrator")
            return dict(client_credit=.03, company_credit=.02, income_and_other=.03,
                        detail_pending=.94, original_remaining=.02, rolled_back=True)
    finally:
        frappe.db.rollback()
