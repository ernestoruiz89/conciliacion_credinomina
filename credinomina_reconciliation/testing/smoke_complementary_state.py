"""Real lifecycle, filter and migration checks on a disposable site; rollback."""
from unittest.mock import patch
from uuid import uuid4

import frappe

from credinomina_reconciliation import complementary_state as service
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.complementary_compensation import confirm_compensation, reverse_compensation
from credinomina_reconciliation.patches.v1_0 import backfill_complementary_list_amounts as migration


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

            def state(doc, expected, applied, pending):
                # Execute the exact pre-commit callbacks without committing the
                # disposable financial fixtures to the local database.
                frappe.db.before_commit.run()
                doc.reload()
                assert doc.reconciliation_status == expected, (doc.name, doc.reconciliation_status, expected)
                visible = frappe.get_list(service.DOCTYPE, filters={"name": doc.name, service.FIELD: expected}, pluck="name")
                assert visible == [doc.name]
                actual = frappe.get_list(service.DOCTYPE,
                    filters={"name": doc.name, service.APPLIED: applied, service.PENDING: pending},
                    fields=[service.APPLIED, service.PENDING], order_by="pending_usd desc")
                assert len(actual) == 1 and actual[0].applied_usd == applied and actual[0].pending_usd == pending, actual

            state(item, "Pendiente", 0, 100)

            def deposit(amount, label):
                doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date="2026-10-10", deposit_reference=marker + label, deposit_currency="USD", deposit_amount=amount,
                    targets=[dict(complementary_item=item.name, amount_usd=amount)])).insert().submit()
                reconcile_deposit(doc)
                return doc

            first = deposit(40, "-first")
            state(item, "Parcial", 40, 60)
            second = deposit(60, "-second")
            state(item, "Conciliado", 100, 0)
            reconcile_deposit(second, undo=True)
            state(item, "Parcial", 40, 60)
            first.reload().cancel()
            second.reload().cancel()
            state(item, "Pendiente", 0, 100)
            item.cancel()
            state(item, "Cancelada", 0, 0)

            def complement(amount, label):
                return frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, posting_date="2026-10-01",
                    reference=marker + label, category="Compensación entre partidas", description=marker,
                    currency="USD", amount=amount)).insert()

            left, right = complement(100, "-left"), complement(-100, "-right")
            confirm_compensation(left.name, right.name, 40, "2026-10-10", "Ensayo de estado", uuid4().hex)
            state(left, "Parcial", 40, 60); state(right, "Parcial", 40, 60)
            confirm_compensation(left.name, right.name, 10, "2026-10-10", "Segundo vínculo", uuid4().hex)
            state(left, "Parcial", 50, 50); state(right, "Parcial", 50, 50)
            left.reload()
            for operation in {row.operation_id for row in left.compensations}:
                reverse_compensation(left.name, operation, "2026-10-11", "Revertir ensayo", uuid4().hex)
            state(left, "Pendiente", 0, 100); state(right, "Pendiente", 0, 100)
            # Simulate old records without the derived field, then backfill twice.
            names = [item.name, left.name, right.name]
            fields = ["name", "modified", "modified_by", "docstatus", "amount", "amount_usd", "compensated_usd", "review_status"]
            financial = frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=fields, order_by="name asc")
            for name in names:
                frappe.db.set_value(service.DOCTYPE, name,
                    {service.FIELD: "", service.APPLIED: 999, service.PENDING: 999}, update_modified=False)
            migration.execute()
            assert frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=fields, order_by="name asc") == financial
            derived_fields = ["name", *service.CACHE_FIELDS]
            states = frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=derived_fields, order_by="name asc")
            migration.execute()
            assert states == frappe.get_all(service.DOCTYPE, filters={"name": ["in", names]}, fields=derived_fields, order_by="name asc")
            state(item, "Cancelada", 0, 0); state(left, "Pendiente", 0, 100)
            assert set(frappe.get_list(service.DOCTYPE,
                filters={"name": ["in", names], service.PENDING: [">", 0], service.APPLIED: 0}, pluck="name")) == {left.name, right.name}

            # Currency fields use zero placeholders when evidence is unavailable;
            # the filterable status distinguishes them from a known zero.
            unknown = complement(100, "-unknown")
            frappe.db.set_value(service.DOCTYPE, unknown.name,
                {"docstatus": 1, "category": "Ajuste de aplicación", "application_adjustment_history": "{broken"}, update_modified=False)
            service.mark(unknown.name)
            frappe.db.before_commit.run()
            values = frappe.db.get_value(service.DOCTYPE, unknown.name, service.CACHE_FIELDS, as_dict=True)
            assert values.reconciliation_status == "Saldo no disponible", values
            assert values.applied_usd == 0 and values.pending_usd == 0, values
            assert not frappe.get_list(service.DOCTYPE, filters={"name": unknown.name, service.PENDING: 0,
                service.FIELD: ["!=", "Saldo no disponible"]}, pluck="name")
            return dict(initial_pending=True, partial_and_full_deposit=True, undo_and_cancel_updated=True,
                        compensation_and_reversal=True, amount_change_same_state=True, native_filter_works=True,
                        unknown_has_explicit_status=True, patch_preserves_financial_data=True,
                        patch_idempotent=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
