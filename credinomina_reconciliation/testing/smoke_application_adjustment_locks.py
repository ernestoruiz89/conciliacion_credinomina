"""Compare real InnoDB locks in a disposable probe table, never business data."""
import json
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_adjustment_indexes import ensure_indexes
from credinomina_reconciliation.application_adjustment_links import confirmed
from credinomina_reconciliation.reclassification_distribution import APPLICATION


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    # Schema installation is idempotent and precedes all synthetic writes.
    ensure_indexes()
    ensure_indexes()
    table = "cn_adjustment_lock_probe_" + frappe.generate_hash(length=10)
    sql = frappe.db.sql
    other = None
    created = False
    try:
        sql(f"CREATE TABLE `{table}` LIKE `tabCN Complementary Item`")
        created = True
        history = json.dumps([dict(related_application="A", application_adjustment_usd=2)])
        journal = json.dumps([dict(operation_id="OP", kind=APPLICATION, target="A",
            parent_document="IMPORT", employer="EMP", effective_date="2025-07-16", amount_usd=3)])
        rows = [(f"PROBE-{n:05}", 1, "Ajuste de aplicación", "EMP", f"APP-{n:05}", 1, "[]", "[]")
                for n in range(2500)]
        rows += [("DIRECT", 1, "Ajuste de aplicación", "EMP", "A", 10, "[]", "[]"),
                 ("HISTORY", 1, "Ajuste de aplicación", "EMP", "Z", 1, history, "[]"),
                 ("GLOBAL", 1, "Ajuste de conciliación", "OTHER", "", 0, "[]", journal)]
        for offset in range(0, len(rows), 500):
            batch = rows[offset:offset + 500]
            sql(f"INSERT INTO `{table}` (name,docstatus,category,employer,related_application,"
                "application_adjustment_usd,application_adjustment_history,reclassification_allocations) VALUES " +
                ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s)"] * len(batch)), tuple(v for row in batch for v in row))
        frappe.db.commit()  # Only this new, randomly named probe table has data.
        other = frappe.db.get_connection()
        cursor = other.cursor()

        def blocked(name):
            try:
                cursor.execute(f"SELECT name FROM `{table}` WHERE name=%s FOR UPDATE NOWAIT", (name,))
                cursor.fetchall()
                return False
            except Exception as error:
                if not error.args or error.args[0] not in (1205, 3572):
                    raise
                return True
            finally:
                other.rollback()

        old_query = f"""SELECT name, reclassification_allocations FROM `{table}`
            WHERE docstatus=1 AND name!='' AND COALESCE(reclassification_allocations, '') NOT IN ('', '[]')
            AND reclassification_allocations LIKE %s FOR UPDATE"""
        old_plan = sql("EXPLAIN " + old_query, ('%"A"%',), as_dict=True)
        sql(old_query, ('%"A"%',))
        assert blocked("PROBE-00001"), "The original table-wide locking scan was not reproduced"
        frappe.db.rollback()

        plans = []

        def scoped_sql(query, *args, **kwargs):
            rewritten = str(query).replace("`tabCN Complementary Item`", f"`{table}`")
            plans.extend(sql("EXPLAIN " + rewritten, args[0], as_dict=True))
            return sql(rewritten, *args, **kwargs)

        started = perf_counter()
        with patch.object(frappe.db, "sql", new=scoped_sql):
            result = confirmed(["A"], lock=True, employer="EMP")
        elapsed = perf_counter() - started
        assert sorted(row.application_adjustment_usd for row in result) == [2, 3, 10], result
        assert not blocked("PROBE-00001"), "An unrelated ordinary item is still locked"
        assert all(blocked(name) for name in ("DIRECT", "HISTORY", "GLOBAL")), "Relevant evidence lost its locks"
        assert len(plans) == 3 and all(row["type"] != "ALL" and row["key"] for row in plans), plans
        frappe.db.rollback()

        # Start an older consistent-read snapshot, then commit an additional
        # link from another connection. Locking discovery must still see it.
        sql(f"SELECT name FROM `{table}` WHERE name='PROBE-00001'")
        cursor.execute(f"UPDATE `{table}` SET application_adjustment_history=%s WHERE name='PROBE-00001'", (history,))
        other.commit()
        with patch.object(frappe.db, "sql", new=scoped_sql):
            current = confirmed(["A"], lock=True, employer="EMP")
        assert sorted(row.application_adjustment_usd for row in current) == [2, 2, 3, 10], current
        # A direct SQL update changed the generated flag automatically.
        flags = sql(f"SELECT cn_has_application_history FROM `{table}` WHERE name='PROBE-00001' FOR UPDATE")
        assert flags[0][0] == 1
        return dict(rows=len(rows), original_unrelated_lock_reproduced=True,
            unrelated_items_no_longer_blocked=True, relevant_evidence_still_locked=True,
            concurrent_committed_links_visible=True, indexed_plans=[dict(type=p["type"], key=p["key"], rows=p["rows"])
                for p in plans[:3]], seconds=round(elapsed, 4), financial_documents_unchanged=True)
    finally:
        if other is not None:
            other.rollback()
            other.close()
        frappe.db.rollback()
        if created:
            assert table.startswith("cn_adjustment_lock_probe_") and table.replace("_", "").isalnum()
            sql(f"DROP TABLE `{table}`")
        frappe.db.rollback()
