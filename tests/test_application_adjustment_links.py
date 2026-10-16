import json
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import application_adjustment_links as links
from credinomina_reconciliation import application_adjustments as adjustments
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.period_totals import PeriodTotalsContext


def document(**changes):
    result = frappe._dict({**dict(name="ITEM", docstatus=1, modified="VERSION", employer="EMP", category=links.CATEGORY,
        review_action=links.CATEGORY, amount_usd=.64, period="P", related_application="A", related_import="IMPORT",
        application_adjustment_usd=.17, flags=frappe._dict(), check_permission=Mock(), db_set=Mock(),
        add_comment=Mock(), save=Mock(), get_doc_before_save=lambda: None), **changes})
    result.set = lambda key, value: result.update({key: value})
    return result


def source(name, amount):
    result = frappe._dict(name=name, parent="IMPORT", parenttype="CN Accounting Import", event_type="Aplicacion",
        effective=1, currency="USD", amount=amount, historical_period="P", application_allocation_detail="[]")
    result.as_dict = lambda: dict(result)
    return result


def setup(doc, values=None):
    from contextlib import ExitStack
    stack = ExitStack()
    amounts = dict(A=.17, B=.08, C=.10, D=.13, E=.16)
    sources = {name: source(name, amount) for name, amount in amounts.items()}
    parent = frappe._dict(name="IMPORT", employer="EMP", status="Importado", docstatus=0, check_permission=Mock())
    stack.enter_context(patch.object(frappe, "get_doc", side_effect=lambda dt, name, **kw:
        doc if dt == "CN Complementary Item" else sources[name] if dt == "CN Source Row" else parent))
    stack.enter_context(patch.object(frappe, "db", Mock(sql=Mock(return_value=[]), get_value=Mock(return_value="EMP"))))
    stack.enter_context(patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool"))
    stack.enter_context(patch.object(frappe, "throw", side_effect=lambda message, *args: (_ for _ in ()).throw(ValueError(message))))
    stack.enter_context(patch.object(adjustments, "assert_adjustable"))
    stack.enter_context(patch.object(adjustments, "cash_coverage", return_value={"protected_usd": 0, "snapshots": {"CASH": "unchanged"}}))
    return stack


def test_append_four_links_to_confirmed_part_preserves_legacy_amount_and_covers_64_cents():
    doc = document()
    selected = [dict(application=name, amount_usd=amount) for name, amount in dict(B=.08, C=.1, D=.13, E=.16).items()]
    with setup(doc), patch.object(adjustments, "reconcile_adjustment") as reconcile:
        result = links.distribute("ITEM", selected, "VERSION")
    assert result["used_usd"] == .64 and result["pending_usd"] == 0
    assert doc.related_application == "A" and doc.application_adjustment_usd == .17
    assert [row.related_application for row in links.links(doc)] == list("ABCDE")
    assert financial_balance(doc)["used_usd"] == .64
    assert financial_balance(doc)["pending_usd"] == 0
    reconcile.assert_called_once_with(doc)
    doc.check_permission.assert_any_call("submit")
    assert all(row["adjustment_periods"] == '[\n "P"\n]' for row in links.additional(doc))


@pytest.mark.parametrize("selected,version", [([dict(application="B", amount_usd=.48)], "VERSION"),
    ([dict(application="B", amount_usd=-.01)], "VERSION"), ([dict(application="B", amount_usd=.09)], "VERSION"),
    ([dict(application="B", amount_usd=.01)] * 2, "VERSION"), ([dict(application="B", amount_usd=.01)], "STALE")])
def test_invalid_overamount_duplicate_and_stale_requests_write_nothing(selected, version):
    doc = document()
    with setup(doc), patch.object(adjustments, "reconcile_adjustment") as reconcile:
        with pytest.raises(ValueError):
            links.distribute("ITEM", selected, version)
    doc.db_set.assert_not_called()
    reconcile.assert_not_called()


def test_submitted_distribution_requires_confirmation_permission():
    doc = document()
    doc.check_permission.side_effect = lambda action: (_ for _ in ()).throw(frappe.PermissionError) if action == "submit" else None
    with setup(doc), pytest.raises(frappe.PermissionError):
        links.distribute("ITEM", [dict(application="B", amount_usd=.08)], "VERSION")
    doc.db_set.assert_not_called()


def test_history_cannot_be_forged_or_changed_on_ordinary_save():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    with setup(doc), pytest.raises(ValueError, match="historial"):
        links.guard(doc, document())


def test_refresh_uses_every_link_once_and_ignores_unrelated_applications():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    rows = [source("A", .17), source("B", .08), source("OTHER", 20)]
    with patch.object(frappe, "db", Mock(sql=Mock(return_value=[doc]))):
        adjustments.refresh_rows(rows)
    assert [row.application_adjustment_usd for row in rows] == [.17, .08, 0]
    assert [row.net_applied_usd for row in rows] == [0, 0, 20]


def test_period_totals_attribute_each_portion_once():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08,"period":"Q"}]')
    periods = [frappe._dict(name=name, employer="EMP", reconciliation_mode="Historica", applied_usd=0) for name in ("P", "Q")]
    rows = [source("A", .17), source("B", .08)]
    for row, period, amount in zip(rows, ("P", "Q"), (.17, .08)):
        row.update(historical_period=period, match_status="Conciliado", application_adjustment_usd=amount)
    with patch.object(frappe, "get_all", return_value=[doc]):
        context = PeriodTotalsContext(periods, rows)
        assert context.values(periods[0])["remitted_total_usd"] == .17
        assert context.values(periods[1])["remitted_total_usd"] == .08


def test_cancellation_reconciliation_scope_contains_every_link():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    with setup(doc), patch("credinomina_reconciliation.complementary_cancellation.reconcile_scoped") as reconcile:
        adjustments.reconcile_adjustment(doc)
    assert reconcile.call_args.args[1]["applications"] == ["A", "B"]


def test_draft_can_replace_its_pending_distribution_before_confirmation():
    doc = document(docstatus=0, application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    selected = [dict(application="C", amount_usd=.10), dict(application="D", amount_usd=.13)]
    with setup(doc), patch.object(adjustments, "reconcile_adjustment") as reconcile:
        result = links.distribute("ITEM", selected, "VERSION")
    assert doc.related_application == "C" and doc.application_adjustment_usd == .10
    assert [row.related_application for row in links.links(doc)] == ["C", "D"]
    assert result["used_usd"] == .23
    doc.save.assert_called_once()
    reconcile.assert_not_called()


def test_additional_link_also_protects_source_identity_from_deletion():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    previous = frappe._dict(employer="EMP", rows=[source("B", .08)])
    changed = frappe._dict(employer="EMP", rows=[], get_doc_before_save=lambda: previous)
    with patch.object(frappe, "get_all", side_effect=lambda _, **kw: [] if kw.get("pluck") else [doc]), \
         patch.object(frappe, "throw", side_effect=ValueError), pytest.raises(ValueError):
        adjustments.guard_source_changes(changed)


def test_read_detail_includes_legacy_and_additional_applications_and_parent_permissions():
    doc = document(application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.08}]')
    rows = [frappe._dict(name=name, parent="IMPORT", client_name=f"Cliente {name}", loan_number=name + "-1", event_date="2025-05-31") for name in ("A", "B")]
    with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "get_all", return_value=rows), \
         patch.object(frappe, "get_list", return_value=["IMPORT"]):
        result = links.get_detail("ITEM")
    assert [row["client_name"] for row in result] == ["Cliente A", "Cliente B"]
    assert [row["application_adjustment_usd"] for row in result] == [.17, .08]
    assert all(row["related_import"] == "IMPORT" and row["state"] == "Confirmado" for row in result)
    doc.check_permission.assert_called_once_with("read")
    with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "get_all", return_value=rows), \
         patch.object(frappe, "get_list", return_value=[]), patch.object(frappe, "throw", side_effect=frappe.PermissionError):
        with pytest.raises(frappe.PermissionError):
            links.get_detail("ITEM")
