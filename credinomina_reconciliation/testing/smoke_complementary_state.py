"""Real lifecycle, filter and migration checks on a disposable site; rollback."""
from unittest.mock import patch
from uuid import uuid4

import frappe

from credinomina_reconciliation import complementary_state as service
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.complementary_compensation import confirm_compensation, reverse_compensation
from credinomina_reconciliation.patches.v1_0 import backfill_complementary_reconciliation_status as migration


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    tables = ["CN Employer", "CN Complementary Item", "CN Remittance Allocation", "CN Complementary Offset"]
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "COMP-STATE-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            item = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name,
                posting_date="2026-10-01", reference=marker, category="Otros ingresos", review_action="Partida de depósito",
                description=marker, currency="USD", amount=100)).insert().submit()

            def state(doc, expected):
                # Execute the exact pre-commit callbacks without committing the
                # disposable financial fixtures to the local database.
                frappe.db.before_commit.run()
                doc.reload()
                assert doc.reconciliation_status == expected, (doc.name, doc.reconciliation_status, expected)
                visible = frappe.get_list(service.DOCTYPE, filters={"name": doc.name, service.FIELD: expected}, pluck="name")
                assert visible == [doc.name]

            state(item, "Pendiente")

            def deposit(amount, label):
                doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date="2026-10-10", deposit_reference=marker + label, deposit_currency="USD", deposit_amount=amount,
                    targets=[dict(complementary_item=item.name, amount_usd=amount)])).insert().submit()
                reconcile_deposit(doc)
                return doc

            first = deposit(40, "-first")
            state(item, "Parcial")
            second = deposit(60, "-second")
            state(item, "Conciliado")
            reconcile_deposit(second, undo=True)
            state(item, "Parcial")
            first.reload().cancel()
            second.reload().cancel()
            state(item, "Pendiente")
            item.cancel()
            state(item, "Cancelada")

            def complement(amount, label):
                return frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, posting_date="2026-10-01",
                    reference=marker + label, category="Compensación entre partidas", description=marker,
                    currency="USD", amount=amount)).insert()

            left, right = complement(100, "-left"), complement(-100, "-right")
            confirm_compensation(left.name, right.name, 40, "2026-10-10", "Ensayo de estado", uuid4().hex)
            state(left, "Parcial"); state(right, "Parcial")
            left.reload()
            reverse_compensation(left.name, left.compensations[0].operation_id, "2026-10-11", "Revertir ensayo", uuid4().hex)
            state(left, "Pendiente"); state(right, "Pendiente")
            # Simulate old records without the derived field, then backfill twice.
            names = [item.name, left.name, right.name]
            fields = ["name", "modified", "modified_by", "docstatus", "amount", "amount_usd", "compensated_usd", "review_status"]
            financial = frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=fields, order_by="name asc")
            for name in names:
                frappe.db.set_value(service.DOCTYPE, name, service.FIELD, "", update_modified=False)
            migration.execute()
            assert frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=fields, order_by="name asc") == financial
            states = frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=["name", service.FIELD], order_by="name asc")
            migration.execute()
            assert states == frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=["name", service.FIELD], order_by="name asc")
            state(item, "Cancelada"); state(left, "Pendiente")
            return dict(initial_pending=True, partial_and_full_deposit=True, undo_and_cancel_updated=True,
                        compensation_and_reversal=True, native_filter_works=True, patch_preserves_financial_data=True,
                        patch_idempotent=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
