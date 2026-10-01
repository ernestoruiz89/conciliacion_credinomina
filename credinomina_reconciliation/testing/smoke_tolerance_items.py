"""Rollback-only migration, signed tolerance and audit-guard integration test."""
import frappe
from frappe.utils import now_datetime

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.patches.v1_0 import integrate_reconciliation_movements as migration
from credinomina_reconciliation.tolerance_items import CATEGORY


def _reject(action):
    try:
        action()
    except frappe.ValidationError:
        return
    raise AssertionError("Se permitió modificar una partida automática manualmente.")


def _migration(marker):
    if not frappe.db.table_exists(migration.OLD):
        raise AssertionError("La prueba de migración requiere la tabla histórica en el sitio desechable.")
    names = []
    stamp = now_datetime()
    for state, amount in (("Vigente", 0.01), ("Revertido", -0.01)):
        name = f"CN-RND-{marker}-{state}"
        names.append(name)
        frappe.db.sql("""insert into `tabCN Reconciliation Movement`
            (name, movement_key, owner, creation, modified, modified_by, docstatus,
             status, signed_amount_usd, absorbed_cash_usd, tolerance_usd,
             deposit_date, deposit_reference, reason, reversed_on, reversal_reason)
            values (%s,%s,'Administrator',%s,%s,'Administrator',0,%s,%s,%s,0.01,
                    '2025-05-10','SMOKE','Auditoría de tolerancia',%s,%s)""",
            (name, name, stamp, stamp, state, amount, max(amount, 0),
             stamp if state == "Revertido" else None, "Reversión de prueba" if state == "Revertido" else None))
    comment = frappe.get_doc({"doctype": "Comment", "name": "TOL-COMMENT-" + marker,
        "comment_type": "Comment", "reference_doctype": migration.OLD,
        "reference_name": names[0], "content": "Conservar seguimiento"})
    comment.db_insert()
    migration.execute()
    assert not frappe.db.exists("DocType", migration.OLD)
    assert frappe.db.table_exists(migration.OLD)  # Recovery archive retained.
    for name in names:
        item = frappe.get_doc(migration.NEW, name)
        assert item.movement_key == name and item.category == CATEGORY
        assert item.docstatus == 1 and item.accounting_status == "No requiere registro"
        assert item.status in name
        assert item.amount_usd == item.signed_amount_usd
    assert frappe.db.get_value("Comment", comment.name, "reference_doctype") == migration.NEW
    before = frappe.db.count(migration.NEW)
    migration.execute()
    assert frappe.db.count(migration.NEW) == before


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = "tol-" + frappe.generate_hash(length=8)
    try:
        _migration(marker)
        for index, (applied, paid, delta) in enumerate(((46.52, 46.53, 0.01), (46.53, 46.52, -0.01))):
            company = f"{marker}-{index}"
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": company,
                "employer_code": company, "payroll_frequency": "Mensual", "rounding_tolerance_usd": 0.01}).insert()
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"}).insert()
            source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                "source_type": "Movimientos contables", "status": "Importado",
                "source_file": f"/private/files/{company}.xlsx", "historical_backfill": 1,
                "historical_period": period.name})
            source.append("rows", {"source_row": 2, "source_key": company, "event_type": "Aplicacion",
                "event_date": "2025-04-30", "client_name": company, "client_number": company,
                "loan_number": company, "reference": company, "currency": "USD", "amount": applied,
                "amount_usd": applied, "processing_route": "Historica", "historical_period": period.name,
                "effective": 1, "match_status": "Pendiente"})
            source.insert()
            _reconcile_sources(employer.name)
            source.reload()
            deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                "deposit_reference": company, "deposit_date": "2025-05-10", "deposit_currency": "USD",
                "deposit_amount": paid, "detail_period": period.name,
                "detail_file": f"/private/files/{company}-detail.xlsx",
                "detail_source_file": f"/private/files/{company}-detail.xlsx", "detail_hash": company})
            deposit.append("detail_rows", {"source_row": 2, "client_name": company,
                "client_number": company, "loan_number": company,
                "application_reference": company, "deducted_usd": paid})
            deposit.insert()
            deposit.submit()
            _reconcile_sources(employer.name)
            filters = {"employer": employer.name, "category": CATEGORY}
            name, = frappe.get_all(migration.NEW, filters=filters, pluck="name")
            item = frappe.get_doc(migration.NEW, name)
            assert item.amount_usd == delta and item.signed_amount_usd == delta
            assert item.status == "Vigente" and item.docstatus == 1
            assert item.accounting_status == "No requiere registro" and not item.voucher
            assert item.absorbed_cash_usd == max(delta, 0)
            period.reload()
            source.reload()
            assert source.rows[0].historical_balance_usd == 0
            deposit.reload()
            assert deposit.unallocated_usd == 0
            balances = (period.applied_usd, period.remitted_usd, deposit.allocated_usd)
            _reconcile_sources(employer.name)
            period.reload()
            deposit.reload()
            assert (period.applied_usd, period.remitted_usd, deposit.allocated_usd) == balances
            assert frappe.db.count(migration.NEW, filters) == 1
            edited = frappe.get_doc(migration.NEW, name)
            edited.description = "No permitido"
            _reject(edited.save)
            _reject(lambda: frappe.get_doc(migration.NEW, name).cancel())
            _reject(lambda: frappe.delete_doc(migration.NEW, name, force=True))
            edited = frappe.get_doc(migration.NEW, name)
            edited.category, edited.movement_key = "Otros ingresos", None
            _reject(edited.save)
            # Tolerance is withdrawn: reverse, keep history, and restore the gap.
            frappe.db.set_value("CN Employer", employer.name, "rounding_tolerance_usd", 0)
            _reconcile_sources(employer.name)
            item.reload()
            assert item.status == "Revertido" and item.reversed_on
            frappe.db.set_value("CN Employer", employer.name, "rounding_tolerance_usd", 0.01)
            _reconcile_sources(employer.name)
            item.reload()
            assert item.status == "Vigente" and not item.reversed_on
            assert frappe.db.count(migration.NEW, filters) == 1
            # Closing the period must still prevent automatic reversals.
            frappe.db.set_value("CN Reconciliation Period", period.name, "status", "Cerrado")
            frappe.db.set_value("CN Employer", employer.name, "rounding_tolerance_usd", 0)
            _reject(lambda: _reconcile_sources(employer.name))
            frappe.db.set_value("CN Employer", employer.name, "rounding_tolerance_usd", 0.01)
        return {"migration_retried_without_duplicates": True, "legacy_archive_and_comments_preserved": True,
                "signed_tolerances": [0.01, -0.01], "no_double_count": True,
                "manual_edits_blocked": True, "reversal_and_reactivation": True,
                "closed_period_guard": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
