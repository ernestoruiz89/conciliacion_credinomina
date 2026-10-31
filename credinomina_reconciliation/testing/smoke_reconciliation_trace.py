"""Two operators, two deposits, one application; run only on the disposable site."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import reconciliation_trace as trace
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report
from credinomina_reconciliation.patches.v1_0 import backfill_reconciliation_trace as migration


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "TRACE-" + frappe.generate_hash(length=8)
            users = []
            for label in ("ana", "luis"):
                user = frappe.get_doc(dict(doctype="User", email=f"{label}-{marker.lower()}@example.invalid",
                    first_name=label, enabled=1, send_welcome_email=0,
                    roles=[dict(role="System Manager"), dict(role="Supervisor Credinomina")])).insert()
                users.append(user.name)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker,
                payroll_frequency="Mensual", rounding_tolerance_usd=0)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
            imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD", historical_backfill=1,
                rows=[dict(event_type="Aplicacion", event_date="2025-04-30", currency="USD", amount=100,
                    amount_usd=100, effective=1, historical_period=period.name, source_key=marker)])).insert()
            engine._reconcile_sources(employer.name, preserve_deposits=True)
            imported.reload()
            application = imported.rows[0].name
            deposits = []
            for index, (user, amount) in enumerate(zip(users, (30, 20))):
                frappe.set_user(user)
                deposit = frappe.get_doc(dict(doctype=trace.DEPOSIT, employer=employer.name,
                    deposit_date="2025-04-30", deposit_reference=f"{marker}-{index}", deposit_currency="USD",
                    deposit_amount=amount, targets=[dict(historical_application=application, amount_usd=amount)])).insert()
                deposit.submit()
                reconcile_deposit(deposit)
                deposits.append(deposit)
            frappe.set_user("Administrator")
            detail = report.get_month_detail(2025, 4, employer.name)
            history = detail["rows"][0]["reconciliation_history"]
            assert [(row["performed_by"], row["delta_usd"]) for row in history] == list(zip(users, (30, 20))), history
            assert detail["rows"][0]["state"] == "Parcial"
            for user in users:
                matrix = report.execute(dict(year=2025, reconciled_by=user, employer=employer.name))[1]
                assert matrix[0]["m04"] == 1 and matrix[0]["m04_total_usd"] == 100
                assert report.get_month_detail(2025, 4, employer.name, reconciled_by=user)["filtered_count"] == 1
            # An unrelated edit must not take ownership of earlier allocations.
            imported.reload()
            imported.add_comment("Comment", "Solo observación")
            count = frappe.db.count(trace.DOCTYPE)
            reconcile_deposit(deposits[0].reload())
            assert frappe.db.count(trace.DOCTYPE) == count
            # Rollback removes the new trace together with the financial operation.
            frappe.db.savepoint("trace_rollback")
            frappe.set_user(users[1])
            reconcile_deposit(deposits[1].reload(), undo=True)
            reversal = report.get_month_detail(2025, 4, employer.name)["rows"][0]["reconciliation_history"][-1]
            assert reversal["performed_by"] == users[1] and reversal["delta_usd"] == -20
            frappe.db.rollback(save_point="trace_rollback")
            frappe.set_user("Administrator")
            assert frappe.db.count(trace.DOCTYPE) == count
            frappe.db.savepoint("trace_cancel")
            frappe.set_user(users[0])
            deposits[1].reload().cancel()
            cancelled = report.get_month_detail(2025, 4, employer.name)["rows"][0]["reconciliation_history"]
            assert len(cancelled) == 3 and cancelled[-1]["delta_usd"] == -20, cancelled
            assert cancelled[-1]["performed_by"] == users[0]
            frappe.db.rollback(save_point="trace_cancel")
            frappe.set_user("Administrator")
            # Backfill is scoped to this fixture; the patch changes no financial data.
            imported.reload(); period.reload()
            before = {"import": imported.as_dict(), "period": period.as_dict(),
                      "deposits": [doc.reload().as_dict() for doc in deposits]}
            original_load = report.load_transactions
            def scoped_load(kind, drafts, scope, dates):
                return original_load(kind, drafts, {"employer": employer.name}, dates)
            with patch.object(migration.frappe, "reload_doc"), patch.object(report, "load_transactions", side_effect=scoped_load):
                migration.execute()
                migrated = frappe.db.count(trace.DOCTYPE)
                migration.execute()
                assert frappe.db.count(trace.DOCTYPE) == migrated
            assert before == {"import": imported.reload().as_dict(), "period": period.reload().as_dict(),
                              "deposits": [doc.reload().as_dict() for doc in deposits]}
            detail = report.get_month_detail(2025, 4, employer.name)
            initial = [row for row in detail["rows"][0]["reconciliation_history"] if row["origin"] == trace.INITIAL]
            assert len(initial) == 1 and initial[0]["performed_by"] == imported.modified_by
            from credinomina_reconciliation.application_adjustments import confirm_adjustment
            from credinomina_reconciliation.application_adjustment_links import distribute
            from credinomina_reconciliation.manual_complementary_policy import imported_item
            frappe.set_user(users[0])
            with imported_item(marker):
                adjustment = frappe.get_doc(dict(doctype=trace.ITEM, employer=employer.name, period=period.name,
                    category="Ajuste de aplicación", review_action="Ajuste de aplicación", posting_date="2025-04-30",
                    currency="USD", amount=15, application_adjustment_usd=10, related_application=application,
                    accounting_source_key=marker, source_currency="USD", source_credit=15, source_date="2025-04-30",
                    description="Prueba trazabilidad", review_notes="Prueba trazabilidad")).insert()
            confirm_adjustment(adjustment.name)
            frappe.set_user(users[1])
            distribute(adjustment.name, [dict(application=application, amount_usd=5)], str(adjustment.reload().modified))
            item_events = frappe.get_all(trace.DOCTYPE, filters={"source_doctype": trace.ITEM, "source_name": adjustment.name},
                fields=["performed_by", "delta_usd"], order_by="performed_on asc")
            assert [(row.performed_by, row.delta_usd) for row in item_events] == list(zip(users, (10, 5))), item_events
            with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=True):
                adjustment.reload().cancel()
            item_events = frappe.get_all(trace.DOCTYPE, filters={"source_doctype": trace.ITEM, "source_name": adjustment.name},
                fields=["performed_by", "delta_usd"], order_by="performed_on asc")
            assert len(item_events) == 4 and sum(row.delta_usd for row in item_events) == 0, item_events
            return dict(ok=True, operations=2, reversal=True, cancellation=True, adjustment_portions=True,
                        rollback=True, migration_idempotent=True, financial_data_unchanged=True)
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
