"""Exercise picker queries and normal exception saves; rollback all fixtures."""

from uuid import uuid4
import frappe

from credinomina_reconciliation.exception_selection import get_related_cases, resolve_related_case


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = uuid4().hex[:10]
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": f"Picker {marker}",
                                  "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                 "payroll_month": "2026-10-01", "reconciliation_mode": "Operativa",
                                 "collection_cycle": "Mensual"}).insert()
        # Direct child/source fixtures isolate the picker from file import and
        # reconciliation workflows, which have their own integration tests.
        collection = frappe.get_doc({
            "doctype": "CN Collection Row", "name": f"picker-c-{marker}",
            "parent": period.name, "parenttype": period.doctype, "parentfield": "collection_rows", "idx": 1,
            "client_name": "Ana Prueba", "client_number": "12", "loan_number": "100-1", "expected_usd": 100,
        })
        collection.db_insert()
        source = frappe.get_doc({"doctype": "CN Accounting Import", "name": f"picker-i-{marker}",
                                 "employer": employer.name, "status": "Borrador"})
        source.db_insert()
        row = frappe.get_doc({
            "doctype": "CN Source Row", "name": f"picker-s-{marker}", "parent": source.name,
            "parenttype": source.doctype, "parentfield": "rows", "idx": 1, "source_row": 15,
            "event_type": "Aplicacion", "effective": 1, "match_status": "Pendiente", "currency": "USD", "amount": 90,
            "client_name": "Ana Prueba", "client_number": "12", "loan_number": "100-1", "event_date": "2026-10-15",
            "collection_period": period.name, "collection_row_id": collection.name,
        })
        row.db_insert()
        candidates = get_related_cases(employer.name, "Cobranza", period.name, "Ana")
        assert len(candidates["rows"]) == 1
        application = get_related_cases(employer.name, "Aplicación", period.name, "Ana")["rows"][0]
        assert application["amount_usd"] == 90 and application["source_row"] == 15
        values = resolve_related_case(employer.name, "Aplicación", row.name, period.name)
        exception = frappe.get_doc({"doctype": "CN Reconciliation Exception", **values,
                                   "exception_type": "Pago parcial", "status": "Abierta", "amount_usd": 10})
        exception.flags.skip_comment_reconciliation = True
        exception.insert()
        exception.reload()
        assert exception.amount_usd == 10 and exception.client_name == "Ana Prueba"
        assert exception.collection_row_id == collection.name and exception.source_row == 15
        exception.loan_number = "INVALID"
        try:
            exception.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se permitió alterar el crédito del caso seleccionado.")
        frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
        assert not get_related_cases(employer.name, "Aplicación")["rows"]
        return {"collection_found": True, "application_found": True, "amount_preserved": True,
                "invalid_link_rejected": True, "closed_period_excluded": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
