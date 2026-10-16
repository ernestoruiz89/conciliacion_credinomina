"""Fully compensated history can also contain lines rounded to zero USD."""
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.period_totals import update_period_totals
from credinomina_reconciliation.rounding import money_float, sum_money


def application(name, amount, adjustment=0, **changes):
    return frappe._dict(name=name, event_type="Aplicacion", effective=1,
        historical_period="P", match_status="Conciliado", currency="USD", amount=amount,
        application_adjustment_usd=adjustment, **changes)


def rebuild(rows, status="Pendiente", allocations=(), deposits=None):
    period = frappe._dict(name="P", doctype="CN Reconciliation Period", employer="EMP",
        reconciliation_mode="Historica", status=status, collection_rows=[], applied_usd=0,
        remitted_usd=0, rounding_adjustment_usd=0)
    period.set = lambda key, value: period.update({key: value})
    adjustments = [frappe._dict(period="P", related_application=row.name,
        application_adjustment_usd=row.application_adjustment_usd) for row in rows if row.application_adjustment_usd]
    allocation = dict(allocations=list(allocations), deposit_meta=deposits or {}, rounding_movements=[])
    with patch("credinomina_reconciliation.credit_application.load_allocations", return_value=[]), \
         patch.object(frappe, "get_all", return_value=adjustments), \
         patch.object(engine, "_save_reconciled_document", side_effect=update_period_totals):
        engine._rebuild_historical_balances([period], rows, allocation)
    return period


@pytest.mark.parametrize("with_zero", [False, True])
def test_compensated_period_remains_settled_with_a_native_line_rounded_to_zero(with_zero):
    amounts = [46.50, 38.27, 56.74, 37.69, 40.85, 45.33, 0.40, 61.44]
    rows = [application(str(index), amount, amount) for index, amount in enumerate(amounts)]
    if with_zero:
        rows.insert(1, application("ZERO", 0, amount_nio=.11, source_debit=.11, manual_fx_rate=36.6243))
    period = rebuild(rows)
    assert period.status == "Conciliado"
    assert period.applied_usd == period.remitted_usd == period.pending_usd == 0
    assert period.applied_total_usd == period.remitted_total_usd == 327.22
    assert period.exception_count == 0
    assert all(row.historical_remitted_usd == row.historical_balance_usd == 0 for row in rows)
    assert money_float(sum_money(row.amount for row in rows)) == 327.22


@pytest.mark.parametrize("rows", [[], [application("ZERO", 0, amount_nio=.11)]])
def test_no_positive_application_or_adjustment_stays_pending(rows):
    period = rebuild(rows)
    assert period.status == "Pendiente"
    assert period.applied_total_usd == period.remitted_total_usd == 0


def test_positive_cent_without_cash_still_prevents_settlement():
    rows = [application("ADJUSTED", 100, 100), application("ZERO", 0), application("CENT", .01)]
    period = rebuild(rows)
    assert period.status == "Pendiente"
    assert period.pending_usd == period.applied_usd == .01
    assert period.applied_total_usd == 100.01 and period.remitted_total_usd == 100


def test_partly_covered_net_application_still_uses_cash_status():
    rows = [application("ADJUSTED", 100, 100), application("ZERO", 0), application("NET", 20)]
    account = frappe._dict(reference="REF", voucher="V", event_date="2025-04-15", unclassified_usd=0)
    period = rebuild(rows, allocations=[dict(claim_id="H:NET", deposit_id="D", amount_usd=10, origin="Manual")],
                     deposits={"D": {"account": account}})
    assert period.status == "Parcial" and period.pending_usd == 10
    assert period.applied_total_usd == 120 and period.remitted_total_usd == 110
    assert period.remitted_usd == 10


def test_closed_compensated_period_keeps_its_closure():
    period = rebuild([application("ADJUSTED", 100, 100), application("ZERO", 0)], status="Cerrado")
    assert period.status == "Cerrado"
    assert period.applied_usd == period.remitted_usd == period.pending_usd == 0
