"""Real Frappe transaction test; restricted to test site and always rolled back."""
import frappe

from credinomina_reconciliation.complementary_compensation import (
    CATEGORY, confirm_compensation, get_compensation_balance, preview_compensation,
    get_reversible_compensations, reverse_compensation,
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "OFFSET-" + frappe.generate_hash(length=8)
    try:
        def item(amount, date, **kwargs):
            return frappe.get_doc({"doctype": "CN Complementary Item", "category": CATEGORY,
                "review_action": CATEGORY, "posting_date": date, "currency": "USD", "amount": amount,
                "description": marker, **kwargs}).insert()

        def rejects(action):
            frappe.db.savepoint("offset_reject")
            try:
                action()
            except frappe.ValidationError:
                frappe.db.rollback(save_point="offset_reject")
            else:
                raise AssertionError("Operation should have been blocked")

        left = item(100, "2025-04-01")
        right = item(-30, "2025-08-01")
        before = frappe.db.count("CN Remittance Allocation")
        preview = preview_compensation(left.name, right.name)
        assert preview["suggested_usd"] == 30
        args = (left.name, right.name, 30, "2025-08-01", "Reversión parcial", preview["request_key"])
        confirm_compensation(*args)
        confirm_compensation(*args)  # network retry must not consume another 30
        left.reload(); right.reload()
        assert left.docstatus == right.docstatus == 1
        assert len(left.compensations) == len(right.compensations) == 1
        assert left.compensation_pending_usd == 70 and right.compensation_pending_usd == 0
        assert left.amount == 100 and right.amount == -30
        assert left.compensation_status == "Compensada parcialmente"
        assert right.compensation_status == "Compensada totalmente"
        import json
        from_browser = frappe.get_doc(json.loads(frappe.as_json(left)))
        from_browser.voucher = marker + "-V"
        from_browser.save()  # completing accounting evidence remains possible
        left.reload()
        assert get_compensation_balance(left.name, "2025-04-30")["pending_usd"] == 100
        assert get_compensation_balance(left.name, "2025-08-01")["pending_usd"] == 70
        rejects(lambda: confirm_compensation(left.name, right.name, 30, "2025-08-01", "Otro intento", "c" * 32))
        final = item(-100, "2025-09-01")
        request = preview_compensation(left.name, final.name)["request_key"]
        rejects(lambda: confirm_compensation(left.name, final.name, 71, "2025-09-01", "Exceso", request))
        rejects(lambda: confirm_compensation(left.name, final.name, 70, "2025-08-01", "Antes de la reversión", request))
        confirm_compensation(left.name, final.name, 70, "2025-09-01", "Reversión final", request)
        left.reload(); final.reload()
        assert left.compensation_status == "Compensada totalmente" and final.compensation_pending_usd == 30
        assert len(left.compensations) == 2
        assert get_compensation_balance(left.name, "2025-08-31")["pending_usd"] == 70
        assert get_compensation_balance(left.name, "2025-09-01")["pending_usd"] == 0
        rejects(lambda: left.cancel())
        left.reload()
        left.compensations[0].amount_usd = 1
        rejects(lambda: left.save())
        left.reload()
        left.amount = 1000
        rejects(lambda: left.save())
        left.reload()
        rejects(lambda: frappe.delete_doc("CN Complementary Item", left.name))
        fresh = item(10, "2025-09-01")
        rejects(lambda: fresh.submit())  # direct submit cannot create a fake offset
        # Imported magnitude positive on both sides; direction comes from original debit/credit.
        debit = item(20, "2025-04-01", category="Por clasificar", review_action="Pendiente de revisión",
                     accounting_source_key=marker + "D", source_debit=20, source_credit=0, source_account="123")
        credit = item(20, "2025-08-01", category="Por clasificar", review_action="Pendiente de revisión",
                      accounting_source_key=marker + "C", source_debit=0, source_credit=20, source_account="123")
        token = preview_compensation(debit.name, credit.name)["request_key"]
        confirm_compensation(debit.name, credit.name, 20, "2025-08-01", "Error reclasificado a gasto", token)
        debit.reload(); credit.reload()
        assert debit.source_debit == 20 and credit.source_credit == 20 and credit.amount == 20
        # Regular reconciliation cannot treat confirmed offsets as cash.
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import reconcile_all_sources
        reconcile_all_sources()
        left.reload(); credit.reload()
        assert left.compensation_pending_usd == credit.compensation_pending_usd == 0
        assert frappe.db.count("CN Remittance Allocation") == before
        choices = get_reversible_compensations(left.name)
        assert len(choices["rows"]) == 2
        original_operation = preview["request_key"]
        reversal = (left.name, original_operation, "2025-10-01", "Corrección de compensación equivocada", choices["request_key"])
        reverse_compensation(*reversal)
        reverse_compensation(*reversal)  # retry does not restore twice
        left.reload(); right.reload(); final.reload()
        assert left.compensation_pending_usd == 30 and right.compensation_pending_usd == 30
        assert final.compensation_pending_usd == 30  # unrelated offset unchanged
        assert left.amount == 100 and right.amount == -30
        assert len(left.compensations) == 3 and len(right.compensations) == 2
        assert left.compensations[-1].reverses_operation_id == original_operation
        assert left.compensations[-1].amount_usd == right.compensations[-1].amount_usd == -30
        assert get_compensation_balance(left.name, "2025-09-30")["pending_usd"] == 0
        assert get_compensation_balance(left.name, "2025-10-01")["pending_usd"] == 30
        assert get_compensation_balance(right.name, "2025-09-30")["pending_usd"] == 0
        assert get_compensation_balance(right.name, "2025-10-01")["pending_usd"] == 30
        rejects(lambda: reverse_compensation(left.name, original_operation, "2025-10-01", "Repetición", "d" * 32))
        rejects(lambda: left.cancel())
        left.reload()
        assert len(get_reversible_compensations(left.name)["rows"]) == 1
        assert frappe.db.count("CN Remittance Allocation") == before
        return {"partial_full_multiple": "OK", "dated_balances": "OK", "idempotence": "OK",
                "evidence_and_edit_guards": "OK", "paired_reversal_and_retry": "OK", "historical_balance_preserved": "OK", "not_a_deposit": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
