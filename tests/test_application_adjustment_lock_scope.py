import json
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import application_adjustment_indexes as indexes
from credinomina_reconciliation import application_adjustment_links as links
from credinomina_reconciliation.reclassification_distribution import APPLICATION


def item(name="ITEM", **values):
    return frappe._dict(name=name, related_application="A", application_adjustment_usd=10,
                        application_adjustment_history=json.dumps([
                            dict(related_application="B", application_adjustment_usd=20)]), **values)


def test_locking_reads_keep_direct_history_and_cross_company_portions_once():
    parent = item()
    global_item = frappe._dict(name="GLOBAL", reclassification_allocations=json.dumps([
        dict(operation_id="OP", kind=APPLICATION, target="A", parent_document="IMPORT",
             employer="EMP", effective_date="2025-07-16", amount_usd=5),
        dict(operation_id="OTHER", kind=APPLICATION, target="A", parent_document="IMPORT",
             employer="OTHER", effective_date="2025-07-16", amount_usd=99)]))
    database = Mock(sql=Mock(side_effect=[[parent], [parent], [global_item]]))
    with patch.object(frappe, "db", database):
        result = links.confirmed(["B", "A", "A"], lock=True, employer="EMP", exclude="EXCLUDED")
    assert [(row.related_application, row.application_adjustment_usd) for row in result] == [
        ("A", 10), ("B", 20), ("A", 5)]
    direct, history, global_query = database.sql.call_args_list
    assert indexes.DIRECT_INDEX in direct.args[0] and " like " not in direct.args[0]
    assert direct.args[1] == (("A", "B"), links.CATEGORY, "EXCLUDED", "EMP")
    assert indexes.HISTORY_INDEX in history.args[0]
    assert indexes.HISTORY_FLAG + "=1" in history.args[0]
    assert "and employer=%s" in history.args[0]
    assert history.args[1][:3] == ("EMP", links.CATEGORY, "EXCLUDED")
    assert indexes.RECLASS_INDEX in global_query.args[0]
    assert indexes.RECLASS_FLAG + "=1" in global_query.args[0]
    assert global_query.args[1][0] == "EXCLUDED"
    assert all("for update" in call.args[0] for call in database.sql.call_args_list)


def test_large_batches_scan_only_nonempty_journals_and_keep_exact_target_filter():
    parent = item()
    database = Mock(sql=Mock(side_effect=[[parent], [parent], []]))
    names = ["A"] + [f"TARGET-{number}" for number in range(101)]
    with patch.object(frappe, "db", database):
        result = links.confirmed(names, lock=True)
    assert [(row.related_application, row.application_adjustment_usd) for row in result] == [("A", 10)]
    for call in database.sql.call_args_list:
        assert " like " not in call.args[0]
        assert "and employer=%s" not in call.args[0]
    assert indexes.HISTORY_FLAG + "=1" in database.sql.call_args_list[1].args[0]
    assert indexes.RECLASS_FLAG + "=1" in database.sql.call_args_list[2].args[0]


def test_no_targets_make_no_database_calls_and_wildcards_are_escaped():
    database = Mock(sql=Mock(side_effect=[[], [], []]))
    with patch.object(frappe, "db", database):
        assert links.confirmed([None, ""], lock=True) == []
        database.sql.assert_not_called()
        assert links.confirmed(["A_B%"], lock=True) == []
    pattern = '%"A\\_B\\%"%'
    assert pattern in database.sql.call_args_list[1].args[1]
    assert pattern in database.sql.call_args_list[2].args[1]


def test_installation_indexes_presence_without_updating_financial_documents():
    database = Mock(sql=Mock(return_value=[]))
    with patch.object(frappe, "db", database):
        indexes.ensure_indexes()
    ddl = database.sql_ddl.call_args.args[0]
    assert ddl.count("GENERATED ALWAYS AS") == 2
    assert "VIRTUAL" in ddl
    assert "UPDATE " not in ddl and "DELETE " not in ddl
    assert database.add_index.call_count == 3
    database.sql.return_value = [frappe._dict(Field=flag) for flag, _field in indexes.FLAGS]
    database.sql_ddl.reset_mock()
    with patch.object(frappe, "db", database):
        indexes.ensure_indexes()
    database.sql_ddl.assert_not_called()
