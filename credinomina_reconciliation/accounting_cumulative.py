"""Compare cumulative exports without rewriting their live accounting origins.

The previous export defines the scope of absence warnings. Exact ledger evidence
and occurrence counts define overlap; a repeated line inside an export is still
an independent movement. Manual cash without ledger evidence is never an origin.
"""
from collections import defaultdict, deque

import frappe

from credinomina_reconciliation.accounting_evidence import evidence_signature
from credinomina_reconciliation.parsers import SourceFileError, source_key, canonical_credit_number
from credinomina_reconciliation.rounding import decimal_value

IMPORT = "CN Accounting Import"
COMPLEMENT = "CN Complementary Item"
DEPOSIT = "CN Remittance Allocation"
COMMON = ["accounting_source_key", "source_row", "source_account", "source_currency",
          "source_debit", "source_credit", "source_description", "accounting_reference", "tmov", "tdoc", "source_fx_rate"]
ORIGIN = [*COMMON, "source_date", "source_voucher", "source_file_hash"]


def fingerprint(row):
    return source_key(*evidence_signature(row), canonical_credit_number(row.get("loan_number") or row.get("source_loan_number")))


def _parents(doctype, names, fields):
    result = {}
    names = sorted(set(names))
    for offset in range(0, len(names), 500):
        result.update({row.name: row for row in frappe.get_all(doctype,
            filters={"name": ["in", names[offset:offset + 500]]}, fields=["name", *fields], limit_page_length=0)})
    return result


def live_origins(records):
    """Read all three origin types; collapse mirrors, never physical repetitions.

    Permission filtering cannot silently turn hidden existing origins into new
    movements. Check access to every matching parent before exposing the result.
    """
    dates = [str(row.get("event_date") or row.get("source_date"))[:10] for row in records
             if row.get("event_date") or row.get("source_date")]
    if not dates:
        return []
    wanted = {fingerprint(row) for row in records}
    period = ["between", [min(dates), max(dates)]]
    candidates = []
    rows = frappe.get_all("CN Source Row", filters={"parenttype": IMPORT,
        "event_date": period, "accounting_source_key": ["!=", ""]},
        fields=[*COMMON, "name", "parent", "event_date", "voucher", "client_name", "loan_number"], limit_page_length=0)
    rows = [row for row in rows if fingerprint(row) in wanted]
    parents = _parents(IMPORT, [row.parent for row in rows], ["employer", "file_hash", "bulk_source_hash"])
    for row in rows:
        parent = parents.get(row.parent)
        if parent:
            candidates.append(dict(row, doctype=IMPORT, document=row.parent, employer=parent.employer,
                                   source_file_hash=parent.bulk_source_hash or parent.file_hash))
    for row in frappe.get_all(COMPLEMENT, filters={"source_date": period, "accounting_source_key": ["!=", ""]},
            fields=[*ORIGIN, "name", "employer", "client_name", "loan_number"], limit_page_length=0):
        candidates.append(dict(row, doctype=COMPLEMENT, document=row.name))
    deposits = frappe.get_all(DEPOSIT, filters={"source_date": period, "accounting_source_key": ["!=", ""]},
        fields=[*ORIGIN, "name", "employer", "source_client_name", "source_loan_number"], limit_page_length=0)
    sources = frappe.get_all("CN Deposit Accounting Source", filters={"source_date": period,
        "accounting_source_key": ["!=", ""]}, fields=[*ORIGIN, "name", "parent", "source_client_name", "source_loan_number"], limit_page_length=0)
    parents = _parents(DEPOSIT, [row.parent for row in sources if fingerprint(row) in wanted], ["employer"])
    for row in sources:
        parent = parents.get(row.parent)
        if parent:
            candidates.append(dict(row, doctype=DEPOSIT, document=row.parent, employer=parent.employer))
    for row in deposits:
        candidates.append(dict(row, doctype=DEPOSIT, document=row.name))
    visible = defaultdict(set)
    for row in candidates:
        if row.get("accounting_source_key") and fingerprint(row) in wanted:
            visible[row["doctype"]].add(row["document"])
    for doctype, names in visible.items():
        names = sorted(names)
        for offset in range(0, len(names), 500):
            batch = names[offset:offset + 500]
            readable = set(frappe.get_list(doctype, filters={"name": ["in", batch]}, pluck="name", limit_page_length=0))
            if readable != set(batch):
                frappe.throw("No tiene acceso a todos los orígenes contables de esta comparación. Solicite la revisión a un usuario con acceso completo.", frappe.PermissionError)
    result, identities, positions = [], {}, {}
    for row in candidates:
        key = row.get("accounting_source_key")
        signature = fingerprint(row)
        if not key or signature not in wanted:
            continue
        position = (row.get("source_file_hash"), row.get("source_row"))
        physical = position if all(position) else None
        previous = identities.get(key) or (positions.get(physical) if physical else None)
        if previous:
            if fingerprint(previous) != signature:
                raise SourceFileError("Hay evidencia contable contradictoria para un mismo origen. Revise los documentos antes de actualizar el acumulado.")
            continue
        identities[key] = row
        if physical:
            positions[physical] = row
        result.append(row)
    return sorted(result, key=lambda row: (str(row.get("source_file_hash") or ""), int(row.get("source_row") or 0), row["document"]))


def _digest(origins, file_hash):
    # Own committed blocks are intentionally excluded. Exact-file retries have
    # their existing idempotency gates; any other concurrent load must reanalyse.
    return source_key(*sorted(f"{row['accounting_source_key']}:{row['doctype']}:{row['document']}:{row.get('source_fx_rate')}"
        for row in origins if row.get("source_file_hash") != file_hash))


def reference(row):
    return {"doctype": row["doctype"], "document": row["document"], "row": row.get("source_row"),
            "event_date": str(row.get("event_date") or row.get("source_date") or "")[:10],
            "employer": row.get("employer"), "voucher": row.get("voucher") or row.get("source_voucher"),
            "account": row.get("source_account"), "currency": row.get("source_currency"),
            "debit": row.get("source_debit"), "credit": row.get("source_credit"),
            "description": row.get("source_description"),
            "client_name": row.get("client_name") or row.get("source_client_name"),
            "loan_number": row.get("loan_number") or row.get("source_loan_number")}


def compare(records, previous_records, origins, file_hash):
    existing, incoming, previous = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in origins:
        existing[fingerprint(row)].append(row)
    for row in records:
        incoming[fingerprint(row)].append(row)
    for row in previous_records:
        previous[fingerprint(row)].append(row)
    kept, skipped, missing, issues = [], [], [], []
    for signature, rows in incoming.items():
        pool = existing[signature]
        # Match already saved rows of this exact file first, so a resumed batch
        # cannot consume a different occurrence when group ordering changes.
        exact = {item.get("source_row"): item for item in pool if item.get("source_file_hash") == file_hash}
        matched = {row["source_row"]: exact[row["source_row"]] for row in rows if row["source_row"] in exact}
        reserved = {item["accounting_source_key"] for item in matched.values()}
        remaining = deque(item for item in pool if item["accounting_source_key"] not in reserved)
        baseline = _digest(existing[signature], file_hash)
        for row in rows:
            origin = matched.get(row["source_row"])
            if origin is None and remaining:
                origin = remaining.popleft()
            if origin is None:
                kept.append(dict(row, _cumulative_baseline=baseline))
                continue
            skipped.append({**reference(origin), "row": row["source_row"],
                "reason": f"Ya registrado en {origin['document']}; se conserva su conciliación"})
            if decimal_value(row.get("source_fx_rate")) != decimal_value(origin.get("source_fx_rate")):
                issues.append({"row": row["source_row"], "reason": "El tipo de cambio difiere del movimiento ya registrado. Use la tasa original; revise las correcciones por separado."})
    for signature, rows in previous.items():
        # Only saved origins from the scope of the selected previous export are
        # candidates for manual review. Never report manual cash without sources.
        count = max(min(len(rows), len(existing[signature])) - len(incoming[signature]), 0)
        for origin in existing[signature][-count:] if count else []:
            reason = "No aparece en el nuevo acumulado. Posible anulación o cambio en el core; revisar manualmente. No se modificó el registro."
            if len(existing[signature]) > 1:
                reason += " Hay líneas idénticas: disminuyó su cantidad; este documento es un candidato, verifique cuál corresponde."
            missing.append({**reference(origin), "reason": reason})
    return {"records": sorted(kept, key=lambda row: row["source_row"]), "already_imported": skipped,
            "missing_origins": missing, "issues": issues}


def guard_block(plan):
    rows = [row for group in plan["groups"] for row in group["rows"]]
    rows += plan.get("complementary", []) + plan.get("deposits", [])
    rows = [row for row in rows if "_cumulative_baseline" in row]
    if not rows:
        return
    existing = defaultdict(list)
    for origin in live_origins(rows):
        existing[fingerprint(origin)].append(origin)
    digests = {key: _digest(origins, plan["file_hash"]) for key, origins in existing.items()}
    empty = _digest([], plan["file_hash"])
    for row in rows:
        if digests.get(fingerprint(row), empty) != row["_cumulative_baseline"]:
            raise SourceFileError("Otra carga modificó los movimientos de este acumulado. Los bloques guardados se conservan; genere un nuevo análisis para evitar duplicados.")
