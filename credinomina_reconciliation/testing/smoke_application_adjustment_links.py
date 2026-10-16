"""One 64-cent core adjustment, five applications; disposable site, rollback."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.application_adjustment_links import distribute, links, get_detail, FIELD
from credinomina_reconciliation.complementary_balances import get_balance
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "MULTI-ADJ-" + frappe.generate_hash(length=8)
    tables = ["CN Employer", "CN Accounting Import", "CN Source Row", "CN Reconciliation Period", "CN Complementary Item"]
    before = {name: frappe.db.count(name) for name in tables}
    try:
        company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
        period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
            payroll_month="2025-05-01", reconciliation_mode="Historica")).insert()
        amounts = [.17, .08, .10, .13, .16]
        imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, historical_backfill=1,
            source_file=f"/private/files/{marker}.csv", currency="USD", status="Importado",
            rows=[dict(event_type="Aplicacion", event_date="2025-05-31", currency="USD", amount=amount,
                amount_usd=amount, effective=1, historical_period=period.name, source_key=f"{marker}-{index}",
                client_name=f"Cliente {index}", loan_number=f"{marker}-{index}-1") for index, amount in enumerate(amounts)])).insert()
        engine._reconcile_sources(company.name, preserve_deposits=True)
        imported.reload()
        item = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, period=period.name,
            posting_date="2025-05-31", category="Ajuste de aplicación", review_action="Ajuste de aplicación",
            currency="USD", amount=.64, application_adjustment_usd=.17,
            related_application=imported.rows[0].name, description="Ajuste global del core",
            accounting_source_key=marker, accounting_classification="Movimiento interno", tmov="01", tdoc="01",
            source_currency="NIO", source_credit=23.44, source_fx_rate=36.6243, source_date="2025-05-31")).insert()
        confirm_adjustment(item.name)
        item.reload()
        original = {field: item.get(field) for field in ("amount", "amount_usd", "related_application", "application_adjustment_usd",
            "accounting_source_key", "source_credit", "source_currency", "source_fx_rate")}
        assert get_balance(item.name)["pending_usd"] == .47
        selected = [dict(application=row.name, amount_usd=amount) for row, amount in zip(imported.rows[1:], amounts[1:])]
        with patch.object(engine, "_reconcile_sources", side_effect=AssertionError("Company-wide reconciliation")):
            distribute(item.name, selected, str(item.modified))
        item.reload(); imported.reload(); period.reload()
        assert original == {field: item.get(field) for field in original}
        assert len(links(item)) == 5 and item.docstatus == 1
        assert [row.amount for row in imported.rows] == amounts
        assert all(row.net_applied_usd == 0 and row.historical_balance_usd == 0 for row in imported.rows)
        assert period.status == "Conciliado"
        assert (period.applied_total_usd, period.remitted_total_usd, period.pending_usd) == (.64, .64, 0)
        assert get_balance(item.name)["used_usd"] == .64 and get_balance(item.name)["pending_usd"] == 0
        detail = get_detail(item.name)
        assert len(detail) == 5 and all(row["related_import"] == imported.name for row in detail)
        assert [row["client_name"] for row in detail] == [f"Cliente {index}" for index in range(5)]
        for action in (
            lambda: distribute(item.name, selected, str(item.modified)),
            lambda: frappe.get_doc(item.doctype, item.name).update({FIELD: "[]"}).save(),
        ):
            frappe.db.savepoint("invalid_multi_adjustment")
            try:
                action()
            except frappe.ValidationError:
                frappe.db.rollback(save_point="invalid_multi_adjustment")
            else:
                raise AssertionError("Invalid additional adjustment accepted")
        # A legitimate cancellation restores all five net applications together.
        with patch("credinomina_reconciliation.accounting_evidence.has_core_removal_role", return_value=True):
            item.reload(); item.cancel()
        imported.reload(); period.reload()
        assert [row.net_applied_usd for row in imported.rows] == amounts
        assert period.pending_usd == .64 and period.remitted_total_usd == 0
        assert len(get_detail(item.name)) == 5 and all(row["state"] == "Cancelado" for row in get_detail(item.name))
        draft = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, period=period.name,
            posting_date="2025-05-31", category="Ajuste de aplicación", review_action="Ajuste de aplicación",
            currency="USD", amount=.64, description="Nueva distribución en borrador")).insert()
        distribute(draft.name, [dict(application=row.name, amount_usd=amount) for row, amount in zip(imported.rows, amounts)], str(draft.modified))
        draft.reload(); imported.reload()
        assert draft.docstatus == 0 and len(links(draft)) == 5
        assert [row.net_applied_usd for row in imported.rows] == amounts
        confirm_adjustment(draft.name)
        imported.reload(); period.reload()
        assert all(row.net_applied_usd == 0 for row in imported.rows)
        assert period.status == "Conciliado" and get_balance(draft.name)["used_usd"] == .64
        return dict(original_confirmed_17_cents_preserved=True, remaining_47_cents_to_four_applications=True,
            period_and_item_fully_settled=True, cash_unchanged=True, duplicate_and_forged_history_rejected=True,
            cancellation_restores_all_five=True, draft_waits_for_confirmation=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
