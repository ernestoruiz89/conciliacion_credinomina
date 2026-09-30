"""Exercise the upgrade on the disposable site; roll back all records/metadata."""
import json
import frappe

from credinomina_reconciliation.patches.v1_0.integrate_deposit_surplus import execute, OLD, NEW


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo se permite en el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    if not frappe.db.table_exists(OLD) or not frappe.db.exists("DocType", OLD):
        raise RuntimeError("Esta prueba de actualización requiere el DocType anterior instalado.")
    marker = "credit-migration-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                   "employer_code": marker}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"}).insert()
        frappe.db.set_value("CN Reconciliation Period", period.name, "status", "Cerrado")
        names = []
        for status in (0, 1, 2):
            name = f"{marker}-{status}"
            names.append(name)
            frappe.db.sql("""insert into `tabCN Deposit Surplus`
                (name, owner, creation, modified, modified_by, docstatus, idx,
                 period, employer, deposit_reference, deposit_voucher, amount_usd,
                 reason_type, explanation, support_file, result)
                values (%s, 'Administrator', '2025-05-01 10:00:00', '2025-05-02 11:00:00',
                 'Administrator', %s, 0, %s, %s, %s, 'BANK-RECEIPT', 16.84,
                 'Error de la empresa', 'Saldo pendiente de devolución', '/private/files/evidence.pdf',
                 'Saldo a favor documentado')""", (name, status, period.name, employer.name, marker))
        comment = frappe.get_doc({"doctype": "Comment", "name": marker,
                                  "comment_type": "Comment", "reference_doctype": OLD,
                                  "reference_name": names[1], "content": "Seguimiento preservado"})
        comment.db_insert()
        version = frappe.get_doc({"doctype": "Version", "name": marker,
                                  "ref_doctype": OLD, "docname": names[1], "data": '{"changed": []}'})
        version.db_insert()
        file = frappe.get_doc({"doctype": "File", "name": marker, "file_name": "evidence.pdf",
                               "file_url": "/private/files/evidence.pdf", "is_private": 1,
                               "attached_to_doctype": OLD, "attached_to_name": names[1]})
        file.db_insert()

        # Collision must stop before deleting metadata or changing the source.
        collision = frappe.get_doc({"doctype": NEW, "name": names[0], "category": "Otros ingresos"})
        collision.db_insert()
        try:
            execute()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("La migración sobrescribió una partida existente.")
        assert frappe.db.exists("DocType", OLD)
        frappe.db.delete(NEW, {"name": names[0]})

        execute()
        assert not frappe.db.exists("DocType", OLD)
        assert frappe.db.table_exists(OLD), "Se debe conservar el archivo SQL de recuperación"
        for status, name in enumerate(names):
            doc = frappe.get_doc(NEW, name)
            assert doc.docstatus == status and doc.amount_usd == 16.84 and doc.amount == 16.84
            assert doc.period == period.name and doc.reference == marker
            assert doc.description == "Saldo pendiente de devolución"
            assert doc.legacy_surplus_id == name and json.loads(doc.legacy_surplus_snapshot)["name"] == name
            assert str(doc.creation) == "2025-05-01 10:00:00" and str(doc.modified) == "2025-05-02 11:00:00"
            assert not doc.voucher and doc.accounting_status == "Pendiente de registro"
        assert frappe.db.get_value("Comment", marker, "reference_doctype") == NEW
        assert frappe.db.get_value("Version", marker, "ref_doctype") == NEW
        assert frappe.db.get_value("File", marker, "attached_to_doctype") == NEW
        assert frappe.db.get_value("CN Reconciliation Period", period.name, "status") == "Cerrado"
        # Retry must be safe, even after completing accounting follow-up.
        frappe.db.set_value(NEW, names[1], "voucher", "ACCOUNTING-ENTRY")
        execute()
        assert frappe.db.get_value(NEW, names[1], "voucher") == "ACCOUNTING-ENTRY"
        assert frappe.db.count(NEW, {"legacy_surplus_id": ["in", names]}) == 3
        return {"migration": "ok", "states": 3, "attachments_and_history": True,
                "closed_period_unchanged": True, "idempotent": True, "collision_protected": True}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
