"""Exercise source-evidence locking against a real disposable Frappe site."""

import json

import frappe

from credinomina_reconciliation.period_lock import period_write_action


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba sólo se ejecuta en el sitio de prueba desechable.")
    frappe.set_user("Administrator")
    marker = "source-close-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": marker,
            "employer_code": marker, "payroll_frequency": "Quincenal",
        }).insert(ignore_permissions=True)
        periods = []
        for cycle in ("Primera quincena", "Segunda quincena"):
            period = frappe.get_doc({
                "doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2027-04-01", "reconciliation_mode": "Operativa",
                "collection_cycle": cycle, "status": "Pendiente",
            })
            period.append("collection_rows", {
                "row_key": marker + cycle, "source_row": 2,
                "client_name": "Cliente de prueba", "client_number": marker + "-C",
                "loan_number": marker + "-L", "installment_number": "1",
                "expected_usd": 50, "deduction_status": "Pendiente de detalle",
                "application_status": "Pendiente",
            })
            period.insert(ignore_permissions=True)
            periods.append(period)
        first, second = periods
        source = frappe.get_doc({
            "doctype": "CN Accounting Import",
            "source_file": f"/private/files/{marker}.xlsx", "status": "Importado",
        })
        source.append("rows", {
            "source_row": 2, "source_key": marker, "event_type": "Aplicacion",
            "event_date": "2027-05-05", "reference": marker,
            "client_name": "Cliente de prueba", "client_number": marker + "-C",
            "loan_number": marker + "-L", "installment_number": "1",
            "currency": "USD", "amount": 100, "amount_usd": 100,
            "processing_route": "Operativa", "effective": 1,
            "collection_period": first.name,
            "application_allocation_detail": json.dumps([
                {"period": first.name, "collection_row_id": first.collection_rows[0].name,
                 "amount_usd": 50},
                {"period": second.name, "collection_row_id": second.collection_rows[0].name,
                 "amount_usd": 50},
            ]),
        })
        source.insert(ignore_permissions=True)
        second.status = "Cerrado"
        with period_write_action("close"):
            second.save(ignore_permissions=True)

        source.reload()
        source.rows[0].manual_fx_rate = 36.9
        try:
            source.save(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
            assert second.name in str(exc), exc
        else:
            raise AssertionError("Se permitió alterar una aplicación de quincena cerrada.")
        source.reload()
        assert not source.rows[0].manual_fx_rate

        source.source_file = f"/private/files/{marker}-reemplazo.xlsx"
        try:
            source.save(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió reemplazar el archivo fuente de un cierre.")
        source.reload()
        source.rows[0].match_reason = "Recalculo idempotente"
        try:
            source.save(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió cambiar el resultado auditado manualmente.")
        source.reload()
        assert source.rows[0].match_reason != "Recalculo idempotente"
        source.append("rows", {
            "source_row": 3, "source_key": marker + "-adicional",
            "event_type": "Aplicacion", "event_date": "2027-05-05",
            "currency": "USD", "amount": 10, "amount_usd": 10,
            "processing_route": "Operativa", "collection_period": second.name,
        })
        try:
            source.save(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió agregar una fila ligada al cierre.")
        source.reload()
        assert len(source.rows) == 1

        fresh = frappe.get_doc({
            "doctype": "CN Accounting Import",
            "source_file": f"/private/files/{marker}-nuevo.xlsx", "status": "Importado",
        })
        fresh.append("rows", {
            "source_row": 2, "source_key": marker + "-nuevo",
            "event_type": "Aplicacion", "event_date": "2027-05-05",
            "currency": "USD", "amount": 10, "amount_usd": 10,
            "processing_route": "Operativa", "collection_period": second.name,
        })
        try:
            fresh.insert(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió importar una nueva fila al cierre.")

        try:
            source.delete(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió eliminar la fuente de un cierre.")

        historical = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2026-08-01", "reconciliation_mode": "Historica",
            "historical_scope": "Mensual", "status": "Pendiente",
        }).insert(ignore_permissions=True)
        historical_source = frappe.get_doc({
            "doctype": "CN Accounting Import",
            "source_file": f"/private/files/{marker}-historico.xlsx",
            "historical_period": historical.name, "status": "Importado",
        })
        historical_source.append("rows", {
            "source_row": 2, "source_key": marker + "-historico",
            "event_type": "Aplicacion", "event_date": "2026-08-15",
            "currency": "USD", "amount": 10, "amount_usd": 10,
            "processing_route": "Historica",
        })
        historical_source.insert(ignore_permissions=True)
        historical.status = "Cerrado"
        with period_write_action("close"):
            historical.save(ignore_permissions=True)
        historical_source.reload()
        historical_source.append("rows", {
            "source_row": 3, "source_key": marker + "-historico-adicional",
            "event_type": "Aplicacion", "event_date": "2026-08-16",
            "currency": "USD", "amount": 5, "amount_usd": 5,
            "processing_route": "Historica",
        })
        try:
            historical_source.save(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió agregar una aplicación heredada a histórico cerrado.")
        historical_source.reload()
        try:
            historical_source.delete(ignore_permissions=True)
        except frappe.ValidationError as exc:
            assert "Reabrir período" in str(exc), exc
        else:
            raise AssertionError("Se permitió borrar una fuente heredada de histórico cerrado.")
        return {
            "second_payroll_half_locked": True,
            "source_file_locked": True,
            "derived_links_locked": True,
            "new_rows_locked": True,
            "source_delete_locked": True,
            "inherited_historical_link_locked": True,
        }
    finally:
        frappe.db.rollback()
