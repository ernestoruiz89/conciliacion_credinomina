"""Human-readable descriptions without changing the technical allocation IDs."""

import json

from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.date_display import display_date


def read_targets(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        return None
    return raw


def describe_targets(raw, descriptions, *, date_formatter=display_date):
    targets = read_targets(raw)
    if targets is None:
        return "No se pudo interpretar el detalle. Revise los identificadores técnicos."
    if not targets:
        return "Sin destinos identificados. Consulte Estado y Motivo de esta fila."
    kinds = {"H": "Aplicación del core", "C": "Cuota de cobranza", "X": "Partida complementaria"}
    blocks = []
    for target in targets:
        claim_id = clean_text(target.get("claim_id"))
        kind = kinds.get(claim_id.partition(":")[0], "Destino")
        try:
            amount = f"US$ {money(target.get('amount_usd')):,.2f}"
        except (ValueError, ArithmeticError):
            amount = "Importe no válido; revisar"
        item = descriptions.get(claim_id)
        lines = [f"{kind} — {amount}"]
        if item is None:
            lines.append(f"Registro no disponible. Referencia técnica: {claim_id or 'sin identificador'}")
        else:
            for pairs in (
                (("Cliente", "client_name"), ("Nro. Cliente", "client_number")),
                (("Crédito", "loan_number"), ("Cuota", "installment_number")),
                (("Período", "period"), ("Mes de cobranza", "payroll_month")),
                (("Fecha", "event_date"), ("Asiento", "accounting_entry"), ("Recibo", "receipt")),
                (("Referencia", "reference"), ("Concepto", "description")),
            ):
                text = " · ".join(
                    f"{label}: {date_formatter(item[key]) if key == 'event_date' else clean_text(item[key])}"
                    for label, key in pairs if item.get(key)
                )
                if text:
                    lines.append(text)
        for application in target.get("fifo_applications") or []:
            lines.append("FIFO · {0} · Aplicación {1} · US$ {2:,.2f}".format(
                date_formatter(application.get("event_date")), application.get("application_id"),
                money(application.get("amount_usd"))))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def load_target_descriptions(targets, claims=()):
    """Batch-resolve known records for trusted reconciliation and migration jobs."""
    import frappe

    ids = {kind: set() for kind in ("H", "C", "X")}
    for target in targets:
        kind, _, name = clean_text(target.get("claim_id")).partition(":")
        if kind in ids and name:
            ids[kind].add(name)
    result = {}
    definitions = {
        "H": ("CN Source Row", ["name", "client_name", "client_number", "loan_number", "installment_number",
                                "historical_period", "event_date", "accounting_entry", "voucher", "receipt", "reference"]),
        "C": ("CN Collection Row", ["name", "parent", "client_name", "client_number", "loan_number", "installment_number", "application_reference"]),
        "X": ("CN Complementary Item", ["name", "period", "client_number", "loan_number", "installment_number", "posting_date", "voucher", "reference", "description"]),
    }
    for kind, (doctype, fields) in definitions.items():
        if not ids[kind]:
            continue
        for row in frappe.get_all(doctype, filters={"name": ["in", sorted(ids[kind])]}, fields=fields, limit_page_length=0):
            result[kind + ":" + row.name] = {
                **row, "period": row.get("historical_period") or row.get("period") or (row.get("parent") if kind == "C" else ""),
                "accounting_entry": row.get("accounting_entry") or row.get("voucher"),
                "event_date": row.get("event_date") or row.get("posting_date"),
                "reference": row.get("reference") or row.get("application_reference"),
            }
    # Historical assignment and identity enrichment may not have been flushed yet.
    for claim in claims:
        if claim["id"] in result:
            for field in ("period", "client_name", "client_number", "loan_number", "installment_number"):
                if claim.get(field):
                    result[claim["id"]][field] = claim[field]
    periods = {item["period"] for item in result.values() if item.get("period")}
    months = {row.name: row.payroll_month for row in frappe.get_all(
        "CN Reconciliation Period", filters={"name": ["in", sorted(periods)]},
        fields=["name", "payroll_month"], limit_page_length=0,
    )} if periods else {}
    for item in result.values():
        item["payroll_month"] = str(months.get(item.get("period")) or "")[:7]
    return result
