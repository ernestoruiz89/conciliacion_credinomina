"""The collection stores one client link; interchange records derive its number."""
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def complete_collection_client_number(row, client, employer):
    """Complete a temporary parsed record before storing only its client link."""
    import frappe
    from frappe import _

    position = row.get("source_row") or row.get("idx") or row.get("name") or "—"
    if client.get("employer") != employer:
        frappe.throw(_("Fila {0}: el cliente vinculado {1} pertenece a otra empresa.").format(
            position, client.get("name")))
    number = clean_text(client.get("client_number"))
    recorded = clean_text(row.get("client_number"))
    if recorded and number and canonical_identifier(recorded) != canonical_identifier(number):
        frappe.throw(_(
            "Fila {0}: el número de cliente registrado ({1}) no coincide con el del cliente vinculado {2} ({3}). "
            "Revise la identidad; no se reemplazó el número."
        ).format(position, recorded, client.get("name"), number))
    if not recorded and number:
        row.update({"client_number": number})


def collection_client_number(row):
    # The fallback is for parsed files and shared non-document matching inputs.
    # Persisted collection rows always resolve to a CN Client named by its number.
    if row.get("doctype") == "CN Collection Row":
        return clean_text(row.get("client"))
    return clean_text(row.get("client") or row.get("client_number"))


def collection_record(row):
    values = row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row)
    return {**values, "client_number": collection_client_number(values)}


def validate_collection_clients(period):
    import frappe
    from frappe import _

    rows = period.get("collection_rows") or []
    names = sorted({row.get("client") for row in rows if row.get("client")})
    if not rows or period.get("status") == "Cerrado":
        return
    clients = {row.name: row for row in frappe.get_all("CN Client",
        filters={"name": ["in", names]}, fields=["name", "client_number", "employer"], limit_page_length=0)}
    for row in rows:
        client = clients.get(row.get("client"))
        if not client:
            frappe.throw(_("Fila {0}: identifique el cliente por crédito en la cartera antes de guardar la cobranza.").format(
                row.get("source_row") or row.get("idx")))
        if client.employer != period.employer:
            frappe.throw(_("Fila {0}: el cliente {1} pertenece a otra empresa.").format(
                row.get("source_row") or row.get("idx"), client.name))
