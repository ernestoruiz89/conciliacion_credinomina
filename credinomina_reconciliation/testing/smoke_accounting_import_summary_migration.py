"""Repair stale headers on real reconciled, closed fixtures; rollback only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.patches.v1_0 import refresh_accounting_import_summaries as migration
from credinomina_reconciliation.testing import smoke_independent_reconciliations as scenario


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    close_period = scenario.close_period
    get_all = frappe.get_all
    checked = []

    def close_and_verify(name):
        result = close_period(name)
        period_before = frappe.get_doc("CN Reconciliation Period", name).as_dict()
        assert period_before.status == "Cerrado"
        imports = get_all("CN Accounting Import", filters={"historical_period": name}, pluck="name")
        assert imports
        for import_name in imports:
            before = frappe.get_doc("CN Accounting Import", import_name).as_dict()
            assert before.status == "Importado" and before.exception_count == 0
            frappe.db.set_value("CN Accounting Import", import_name,
                {"status": "Importado con excepciones", "exception_count": 1, "matched_count": 0},
                update_modified=False)

            def scoped(doctype, *args, **kwargs):
                if doctype == "CN Accounting Import":
                    kwargs["filters"] = {**kwargs.get("filters", {}), "name": import_name}
                return get_all(doctype, *args, **kwargs)

            with patch.object(migration.frappe, "get_all", side_effect=scoped):
                migration.execute()
                after = frappe.get_doc("CN Accounting Import", import_name).as_dict()
                assert after == before, (before, after)
                migration.execute()
                assert frappe.get_doc("CN Accounting Import", import_name).as_dict() == after
            assert frappe.get_doc("CN Reconciliation Period", name).as_dict() == period_before
            checked.append(import_name)
        return result

    # The scenario builds actual applications/deposits, reconciles in both
    # orders and closes each period. It rolls back all fixtures in finally.
    with patch.object(scenario, "close_period", side_effect=close_and_verify):
        result = scenario.run()
    return {**result, "repaired_closed_imports": len(checked)}
