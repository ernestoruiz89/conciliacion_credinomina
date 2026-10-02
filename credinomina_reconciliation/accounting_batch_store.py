"""Durable internal checkpoints. Each block and its results commit together.

No reconciliation is performed here. Only the Carga masiva API may mutate these
records; ordinary form/REST saves are rejected by their controllers.
"""

import json
from contextlib import contextmanager

import frappe
from frappe import _

BATCH = "CN Accounting Batch"
BLOCK = "CN Accounting Batch Block"
MUTEX = "accounting-creation-lock"
BLOCK_DOCUMENTS = 25
BLOCK_ROWS = 1000


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def insert_internal(doctype, name, **values):
    document = frappe.get_doc({"doctype": doctype, **values})
    document.flags.accounting_batch_internal = True
    return document.insert(ignore_permissions=True, set_name=name)


def save_state(token, state):
    configuration = dumps({key: state[key] for key in ("options", "summary", "draft_assignments") if key in state})
    runtime = dumps({key: value for key, value in state.items() if key not in {"options", "summary", "draft_assignments"}})
    if frappe.db.exists(BATCH, token):
        values = {"state_json": runtime}
        # Large employer-choice maps and previews are immutable during creation;
        # don't rewrite their blob for every small progress checkpoint.
        if frappe.db.get_value(BATCH, token, "configuration_json") != configuration:
            values["configuration_json"] = configuration
        frappe.db.set_value(BATCH, token, values)
    else:
        insert_internal(BATCH, token, state_json=runtime, configuration_json=configuration,
                        source_file=state.get("options", {}).get("source_file"))


def load_state(token, for_update=False):
    if for_update:
        rows = frappe.db.sql("select state_json, configuration_json from `tabCN Accounting Batch` where name=%s for update",
                             token, as_dict=True)
        value = rows[0] if rows else None
    else:
        value = frappe.db.get_value(BATCH, token, ["state_json", "configuration_json"], as_dict=True)
    return {**json.loads(value.state_json or "{}"), **json.loads(value.configuration_json or "{}")} if value else None


def ensure_mutex():
    if not frappe.db.exists(BATCH, MUTEX):
        try:
            insert_internal(BATCH, MUTEX, state_json="{}")
        except frappe.DuplicateEntryError:
            pass


def lock_batch(token):
    # Row locks are transaction scoped, and released by DB connection death,
    # including SIGKILL/OOM. Never expire a live worker's lock by elapsed time.
    frappe.db.sql("select name from `tabCN Accounting Batch` where name=%s for update", token)


def lock_creation():
    lock_batch(MUTEX)


@contextmanager
def creation_guard():
    ensure_mutex()
    lock_creation()
    yield  # Released by transaction end, NOT by context exit.


def split_plan(plan):
    """Keep a company/day document intact; bound the number of docs per commit.

    A single company/day exceeding BLOCK_ROWS occupies its own block (it is not
    silently split into several imports). Payloads include only their own CSV.
    """
    block = {"groups": [], "complementary": [], "deposits": [],
             "file_hash": plan["file_hash"], "file_name": plan["file_name"]}
    count = rows = 0
    for kind in ("groups", "complementary", "deposits"):
        for item in plan.get(kind, []):
            size = len(item["rows"]) if kind == "groups" else 1
            if count and (count >= BLOCK_DOCUMENTS or rows + size > BLOCK_ROWS):
                yield block
                block = {"groups": [], "complementary": [], "deposits": [],
                         "file_hash": plan["file_hash"], "file_name": plan["file_name"]}
                count = rows = 0
            block[kind].append(item)
            count += 1
            rows += size
    if count:
        yield block


def prepare_blocks(token, plan, source_records):
    from credinomina_reconciliation.accounting_batch import accounting_group_csv

    total = 0
    for total, block in enumerate(split_plan(plan), 1):
        for group in block["groups"]:
            group["csv_content"] = accounting_group_csv(source_records, group["rows"]).decode("utf-8")
        insert_internal(BLOCK, f"{token}-{total}", batch=token, block_index=total, payload=dumps(block))
        for group in block["groups"]:
            group.pop("csv_content", None)
    return total


def read_block(token, index):
    record = frappe.db.get_value(BLOCK, f"{token}-{index}", ["payload", "completed"], as_dict=True, for_update=True)
    if not record:
        frappe.throw(_("No se encontró el bloque guardado. No vuelva a cargar el archivo sin revisar el historial."))
    return json.loads(record.payload), record.completed


def finish_block(token, index, created):
    frappe.db.set_value(BLOCK, f"{token}-{index}", {"completed": 1, "result_json": dumps(created)})


def results(token, limit=200):
    result = []
    # Only fetch the first blocks needed for the visible result; the complete
    # audit remains in the block rows and the ordinary accounting documents.
    for name in frappe.get_all(BLOCK, filters={"batch": token, "completed": 1},
                               order_by="block_index", pluck="name", limit_page_length=0):
        result.extend(json.loads(frappe.db.get_value(BLOCK, name, "result_json") or "[]"))
        if len(result) >= limit:
            break
    return result[:limit]
