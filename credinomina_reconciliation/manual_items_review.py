"""Read-only inventory. Exact links and possible core matches stay distinct."""
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.report_records import records, child_records

ITEM = "CN Complementary Item"
DEPOSIT = "CN Remittance Allocation"
IMPORT = "CN Accounting Import"
PERIOD = "CN Reconciliation Period"
EXCEPTION = "CN Reconciliation Exception"
PARENTS = (ITEM, DEPOSIT, IMPORT, PERIOD, EXCEPTION)
CHILDREN = {
    ITEM: {"compensations": "CN Complementary Offset"},
    DEPOSIT: {"targets": "CN Remittance Target", "accounting_sources": "CN Deposit Accounting Source",
              "detail_periods": "CN Remittance Period", "detail_rows": "CN Remittance Detail"},
    IMPORT: {"rows": "CN Source Row"},
    PERIOD: {"collection_rows": "CN Collection Row"},
}
# Data fields and JSON journals also contain authoritative links.
REFERENCE_TYPES = {
    "related_application": "CN Source Row", "application_id": "CN Source Row",
    "application_source_row": "CN Source Row", "historical_application": "CN Source Row",
    "aplicacion_id": "CN Source Row", "collection_row_id": "CN Collection Row",
    "fila_id": "CN Collection Row", "partida": ITEM, "complementary_item": ITEM,
    "counterpart": ITEM, "credit_item": ITEM, "item": ITEM,
    "deposit_source_row": DEPOSIT, "deposit_id": DEPOSIT, "deposit": DEPOSIT,
    "periodo": PERIOD, "period": PERIOD, "related_import": IMPORT,
    "adjustment_periods": PERIOD, "adjustment_collection_rows": "CN Collection Row",
    "credit_detail_row": "CN Remittance Detail", "detail_row": "CN Remittance Detail",
    "movimiento": ITEM,
    "saldo_a_favor": ITEM, "deposito_origen": DEPOSIT, "origin_deposit": DEPOSIT,
    "aplicacion_directa": "CN Source Row", "direct_application": "CN Source Row",
    "core_document": None,  # resolved only by the verified exception type below
}
JOURNALS = ("allocation_detail", "historical_detail", "remittance_detail", "application_allocation_detail",
            "credit_history", "application_adjustment_history", "adjustment_periods", "adjustment_collection_rows",
            "rounding_movement_detail")


def json_value(raw):
    if not raw:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


def load_evidence():
    """Read parent permissions first, then only their children; no privileged joins."""
    nodes, owners, metadata = {}, {}, {}
    for doctype in PARENTS:
        if not frappe.has_permission(doctype, "read"):
            continue
        metadata[doctype] = frappe.get_meta(doctype)
        for row in records(doctype, fields=["*"]):
            key = (doctype, row.name)
            nodes[key], owners[key] = row, key
        for field, childtype in CHILDREN.get(doctype, {}).items():
            metadata[childtype] = frappe.get_meta(childtype)
            names = [name for kind, name in nodes if kind == doctype]
            for row in child_records(childtype, names, doctype, field, fields=["*"]):
                key = (childtype, row.name)
                nodes[key], owners[key] = row, (doctype, row.parent)
    return nodes, owners, metadata


def build_edges(nodes, metadata):
    edges, issues = defaultdict(list), defaultdict(list)

    def add(origin, kind, value, label, amount=None):
        if not kind or not value or not isinstance(value, str):
            return
        destination = (kind, value)
        if destination not in nodes:
            issues[origin].append("Vínculo no disponible o restringido: " + label)
            return
        edge = (origin, destination, label, amount)
        edges[origin].append(edge)
        edges[destination].append(edge)

    def walk(origin, value, label, field=""):
        if isinstance(value, list):
            for index, entry in enumerate(value):
                walk(origin, entry, f"{label}[{index + 1}]", field)
        elif isinstance(value, dict):
            amount = next((value[key] for key in ("importe_usd", "amount_usd", "application_adjustment_usd", "importe") if key in value), None)
            for key, entry in value.items():
                if isinstance(entry, (list, dict)):
                    walk(origin, entry, label + "." + key, key)
                elif key in REFERENCE_TYPES:
                    add(origin, REFERENCE_TYPES[key], entry, label + "." + key, amount)
        elif field in REFERENCE_TYPES:
            add(origin, REFERENCE_TYPES[field], value, label)

    for key, row in nodes.items():
        linked = set()
        relation_amount = row.get("amount_usd") if key[0] in ("CN Remittance Target", "CN Complementary Offset") else None
        for field in metadata[key[0]].fields:
            if field.fieldtype == "Link" and field.options in PARENTS:
                add(key, field.options, row.get(field.fieldname), field.fieldname, relation_amount)
                linked.add(field.fieldname)
        for field, kind in REFERENCE_TYPES.items():
            if field not in linked and field not in JOURNALS:
                add(key, kind, row.get(field), field, relation_amount)
        if key[0] == EXCEPTION:
            add(key, row.get("core_evidence_type"), row.get("core_evidence_name"), "Evidencia verificada del core")
        for field in JOURNALS:
            if not row.get(field):
                continue
            try:
                walk(key, json_value(row[field]), field, field)
            except (ValueError, TypeError):
                issues[key].append("Historial inválido: " + field)
    return edges, issues


def review_rows(nodes, owners, metadata, filters=None):
    filters = filters or {}
    edges, issues = build_edges(nodes, metadata)
    output = []
    children, candidates = defaultdict(list), defaultdict(list)
    for node, owner in owners.items():
        if node != owner:
            children[owner].append(node)
    for node, source in nodes.items():
        if source.get("accounting_source_key"):
            company = source.get("employer") or nodes[owners[node]].get("employer")
            for field, aliases in (("voucher", ("voucher", "source_voucher", "accounting_entry", "deposit_voucher")),
                                   ("reference", ("reference", "accounting_reference", "deposit_reference"))):
                for value in {source.get(alias) for alias in aliases} - {None, ""}:
                    candidates[(company, field, value)].append(node)
    for key, item in nodes.items():
        if key[0] != ITEM or item.get("accounting_source_key"):
            continue
        if filters.get("employer") and item.get("employer") != filters["employer"]:
            continue
        if filters.get("item") and item.name != filters["item"]:
            continue
        if filters.get("from_date") and str(item.get("posting_date") or "") < filters["from_date"]:
            continue
        if filters.get("to_date") and str(item.get("posting_date") or "") > filters["to_date"]:
            continue
        automatic = item.get("category") == "Diferencia por tolerancia" or bool(item.get("movement_key"))
        if automatic and not cint(filters.get("include_tolerance")):
            continue
        base = dict(item=item.name, employer=item.get("employer"), posting_date=item.get("posting_date"),
            category=item.get("category"), origin="Tolerancia histórica" if automatic else "Manual",
            docstatus=item.get("docstatus"), reconciliation_status=item.get("reconciliation_status"),
            amount_usd=item.get("amount_usd"), applied_usd=item.get("applied_usd"), pending_usd=item.get("pending_usd"),
            client_number=item.get("client_number"), loan_number=item.get("loan_number"),
            reference=item.get("reference"), voucher=item.get("voucher"), description=item.get("description"),
            difference_reason=item.get("difference_reason"), difference_notes=item.get("difference_notes"),
            tolerance_status=item.get("status") if automatic else "",
            observation="; ".join(sorted(set(issues[key]))))
        if item.get("reconciliation_status") == "Saldo no disponible":
            base["applied_usd"] = base["pending_usd"] = None
        related = list(edges[key])
        # Compensation child rows belong to the item but are independent evidence.
        for childkey in children[key]:
            related.extend(edges[childkey])
        seen, linked_deposits = set(), set()

        def emit(destination, relation, amount=None, evidence="Vínculo registrado"):
            if (destination, relation, str(amount)) in seen:
                return
            seen.add((destination, relation, str(amount)))
            row = nodes[destination]
            owner = owners[destination]
            document = nodes[owner]
            if owner[0] == DEPOSIT:
                linked_deposits.add(owner)
            output.append({**base, "relation": relation, "evidence": evidence,
                "related_doctype": owner[0], "related_document": owner[1],
                "related_row": destination[1] if destination != owner else "",
                "related_row_status": row.get("result") or row.get("state") or row.get("deposit_match_status") or row.get("match_status"),
                "relation_usd": amount, "related_status": document.get("status") or document.get("result"),
                "related_amount_usd": row.get("amount_usd") if row.get("amount_usd") is not None else document.get("amount_usd"),
                "related_date": row.get("event_date") or row.get("compensation_date") or document.get("posting_date") or document.get("deposit_date") or document.get("payroll_month"),
                "related_docstatus": document.get("docstatus"),
                "source_key": row.get("accounting_source_key") or document.get("accounting_source_key"),
                "source_voucher": row.get("source_voucher") or row.get("voucher") or document.get("deposit_voucher"),
                "source_file": row.get("source_file") or document.get("source_file"),
                "source_row": row.get("source_row"),
                "source_currency": row.get("source_currency"), "source_debit": row.get("source_debit"),
                "source_credit": row.get("source_credit"),
                "observation": "; ".join(filter(None, [base["observation"], *issues[destination]]))})

        for origin, destination, label, amount in related:
            other = destination if owners.get(origin) == key else origin
            if other == key:
                continue
            emit(other, label, amount)
            # Follow the exact related movement to its periods and core evidence.
            for a, b, nested_label, nested_amount in edges[other]:
                # Shared parents are not evidence that their other movements
                # belong to this item. Reverse traversal is valid only for an
                # exact application/collection row (e.g. its cash allocation).
                if a != other and other[0] not in ("CN Source Row", "CN Collection Row"):
                    continue
                nested = b if a == other else a
                if nested != key and nested[0] in (PERIOD, IMPORT, DEPOSIT, "CN Source Row", "CN Collection Row"):
                    emit(nested, label + " → " + nested_label, nested_amount)
        for owner in sorted(linked_deposits):
            for childkey in children[owner]:
                if childkey[0] == "CN Deposit Accounting Source":
                    emit(childkey, "Depósito relacionado → accounting_sources")
                elif childkey[0] in ("CN Remittance Period", "CN Remittance Target"):
                    for a, b, nested_label, nested_amount in edges[childkey]:
                        nested = b if a == childkey else a
                        if nested[0] == PERIOD:
                            emit(nested, "Período del depósito relacionado → " + nested_label, nested_amount)
        # Same voucher/reference is only a review hint, never an automatic link.
        for field in ("voucher", "reference"):
            for other in candidates[(item.get("employer"), field, item.get(field))]:
                emit(other, "Coincidencia de " + field, evidence="Candidato; requiere revisión")
        if not seen:
            output.append({**base, "relation": "Sin vínculos visibles", "evidence": "Revisión pendiente"})
    return output


SUMMARY_FIELDS = (
    "item", "employer", "posting_date", "origin", "category", "docstatus",
    "tolerance_status", "reconciliation_status", "amount_usd", "applied_usd", "pending_usd",
    "client_number", "loan_number", "reference", "voucher", "description",
    "difference_reason", "difference_notes",
)


def summarize_rows(rows):
    """One saved balance per item; count unique evidence, never sum relation amounts."""
    grouped = {}
    for row in rows:
        if row["item"] not in grouped:
            grouped[row["item"]] = (
                {field: row.get(field) for field in SUMMARY_FIELDS}, set(), set(), set())
        summary, links, candidates, observations = grouped[row["item"]]
        if row.get("related_document"):
            target = (row.get("related_doctype"), row["related_document"], row.get("related_row") or "")
            bucket = candidates if row.get("evidence") == "Candidato; requiere revisión" else links
            bucket.add(target)
        if row.get("observation"):
            observations.update(row["observation"].split("; "))
    result = []
    for summary, links, candidates, observations in grouped.values():
        result.append({**summary, "link_count": len(links), "candidate_count": len(candidates - links),
                       "details": _("Ver vínculos"), "observation": "; ".join(sorted(observations))})
    return result


@frappe.whitelist()
def get_item_details(item):
    """Read a single item's evidence without changing the report's filters."""
    frappe.get_doc(ITEM, item).check_permission("read")
    columns, rows, message = execute({"item": item, "include_tolerance": 1, "show_details": 1})
    summaries = summarize_rows(rows)
    return {
        "summary": summaries[0] if summaries else None,
        "summary_columns": [column for column in columns if column["fieldname"] in SUMMARY_FIELDS],
        "columns": [column for column in columns if column["fieldname"] not in SUMMARY_FIELDS],
        "rows": rows,
        "message": message,
    }


def execute(filters=None):
    if not frappe.has_permission(ITEM, "read"):
        frappe.throw(_("No tiene permiso para consultar partidas."), frappe.PermissionError)
    nodes, owners, metadata = load_evidence()
    rows = review_rows(nodes, owners, metadata, filters)
    detailed = cint((filters or {}).get("show_details"))
    if not detailed:
        rows = summarize_rows(rows)
    columns = []
    definitions = [
        ("item", "Partida", "Link", ITEM), ("employer", "Empresa", "Link", "CN Employer"),
        ("posting_date", "Fecha", "Date", None), ("origin", "Origen", "Data", None),
        ("category", "Concepto", "Data", None), ("docstatus", "Docstatus", "Int", None),
        ("tolerance_status", "Estado de tolerancia histórica", "Data", None),
        ("reconciliation_status", "Conciliación guardada", "Data", None),
        ("amount_usd", "Importe USD (partida)", "Currency", "USD"),
        ("applied_usd", "Aplicado USD guardado", "Currency", "USD"),
        ("pending_usd", "Pendiente USD guardado", "Currency", "USD"),
        ("client_number", "Nro. cliente", "Data", None), ("loan_number", "Crédito", "Data", None),
        ("reference", "Referencia", "Data", None), ("voucher", "Comprobante", "Data", None),
        ("description", "Descripción", "Data", None), ("relation", "Relación / origen del vínculo", "Data", None),
        ("evidence", "Evidencia", "Data", None), ("related_doctype", "Tipo relacionado", "Data", None),
        ("related_document", "Documento relacionado", "Dynamic Link", "related_doctype"),
        ("related_row", "Fila relacionada", "Data", None),
        ("related_row_status", "Resultado de la fila", "Data", None),
        ("relation_usd", "Importe del vínculo USD", "Currency", "USD"),
        ("related_amount_usd", "Importe relacionado USD", "Currency", "USD"),
        ("related_date", "Fecha / mes relacionado", "Date", None),
        ("related_status", "Estado relacionado", "Data", None), ("related_docstatus", "Docstatus relacionado", "Int", None),
        ("source_key", "Clave contable", "Data", None), ("source_voucher", "Comprobante origen", "Data", None),
        ("source_file", "Archivo origen", "Data", None), ("source_row", "Fila de archivo", "Data", None),
        ("source_currency", "Moneda origen", "Data", None),
        ("source_debit", "Débito origen", "Float", None), ("source_credit", "Crédito origen", "Float", None),
        ("difference_reason", "Razón de la diferencia", "Link", "CN Difference Reason"),
        ("difference_notes", "Documentación", "Data", None), ("observation", "Observaciones", "Data", None),
    ]
    if not detailed:
        definitions = [definition for definition in definitions
                       if definition[0] in SUMMARY_FIELDS or definition[0] == "observation"]
        definitions[2:2] = [
            ("details", "Detalle", "Data", None),
            ("link_count", "Vínculos", "Int", None),
            ("candidate_count", "Candidatos adicionales", "Int", None),
        ]
    for field, label, kind, options in definitions:
        columns.append(dict(fieldname=field, label=_(label), fieldtype=kind, options=options, width=180))
    message = (_("Detalle: una fila por relación; los importes de la partida se repiten y no deben sumarse.")
               if detailed else _("Una fila por partida. Ver vínculos abre su detalle. Los candidatos adicionales son coincidencias sin vínculo registrado."))
    return columns, rows, message + " " + _("Solo evidencia visible según permisos. Las coincidencias no confirman respaldo contable. Consultar no modifica vínculos, saldos ni conciliaciones.")
