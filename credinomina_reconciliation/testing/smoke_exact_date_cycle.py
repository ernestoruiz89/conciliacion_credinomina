"""Check extra operational cuts on the disposable site; roll back all fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_period import create_draft_period


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "EXACT-" + frappe.generate_hash(length=8)
    created = []

    def rejected(callback):
        try:
            callback()
        except frappe.ValidationError:
            return
        raise AssertionError("Invalid cut accepted")

    try:
        with patch.object(frappe, "enqueue"):
            for frequency in ("Mensual", "Quincenal"):
                employer = frappe.get_doc({"doctype": "CN Employer",
                    "employer_name": marker + frequency, "employer_code": marker + frequency,
                    "payroll_frequency": frequency}).insert()

                def period(cycle, cut=None, due=None):
                    return frappe.get_doc({"doctype": "CN Reconciliation Period",
                        "employer": employer.name, "payroll_month": "2026-09-01",
                        "reconciliation_mode": "Operativa", "application_basis": "Cobranza",
                        "collection_cycle": cycle, "cutoff_date": cut,
                        "remittance_due_date": due}).insert()

                for cycle in (["Mensual"] if frequency == "Mensual" else ["Primera quincena", "Segunda quincena"]):
                    created.append(period(cycle).name)
                first = period("Fecha exacta", "2026-09-15", "2026-09-16")
                second = period("Fecha exacta", "2026-09-20")
                created.extend([first.name, second.name])
                first.reload().save()
                assert str(first.cutoff_date) == "2026-09-15"
                assert str(first.remittance_due_date) == "2026-09-16"
                rejected(lambda: period("Fecha exacta", "2026-09-15"))
                rejected(lambda: period("Fecha exacta"))
                rejected(lambda: period("Fecha exacta", "2026-10-05"))
                rejected(lambda: period("Fecha exacta", "2026-09-22", "2026-09-21"))
                second.status = "Pendiente"
                second.save()
                second.reload()
                assert second.status == "Pendiente"
                second.cutoff_date = "2026-09-21"
                second.save()  # Empty pending periods may change date too.
                editable = frappe.get_doc({"doctype": "CN Reconciliation Period",
                    "employer": employer.name, "payroll_month": "2026-10-01",
                    "reconciliation_mode": "Operativa", "application_basis": "Cobranza",
                    "collection_cycle": "Mensual" if frequency == "Mensual" else "Segunda quincena",
                    "status": "Pendiente"}).insert()
                editable.reload()
                editable.collection_cycle = "Fecha exacta" if frequency == "Mensual" else "Primera quincena"
                editable.cutoff_date = "2026-10-15"
                editable.save()
                editable.reload()
                assert str(editable.cutoff_date) == "2026-10-15"
                created.append(editable.name)
                row = frappe.get_doc({"doctype": "CN Source Row", "name": marker + frequency,
                    "parent": marker, "parenttype": "CN Accounting Import", "parentfield": "rows",
                    "event_type": "Aplicacion", "historical_period": second.name})
                row.db_insert()
                second.reload()
                second.cutoff_date = "2026-09-22"
                rejected(second.save)

                source = frappe._dict(employer=employer.name)
                with patch("credinomina_reconciliation.accounting_period._source", return_value=(source, employer)):
                    result = create_draft_period(marker, {"payroll_month": "2026-09-01",
                        "collection_cycle": "Fecha exacta", "cutoff_date": "2026-09-25",
                        "application_basis": "Cobranza"})
                draft = frappe.get_doc("CN Reconciliation Period", result["name"])
                assert str(draft.cutoff_date) == "2026-09-25"
                created.append(draft.name)
            assert len(created) == len(set(created))
            return {"periods_created": len(created), "coexistence": True,
                    "empty_pending_cycle_and_date_editable": True,
                    "invalid_and_duplicate_dates_blocked": True, "linked_cut_protected": True,
                    "accounting_dialog_creation": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
