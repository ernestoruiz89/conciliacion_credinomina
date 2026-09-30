"""Client enrichment and permission-checked portfolio choices for deposit detail."""

from credinomina_reconciliation.client_identity import choose_client
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def complete_detail_clients(rows, clients, employer):
    for row in rows:
        client, reason = choose_client(row, clients, employer)
        if not client:
            continue
        row.client = client["name"]
        row.identity_reason = reason
        if not row.get("client_number"):
            row.client_number = client.get("client_number") or ""


def portfolio_credit_choices(rows, snapshots, client, employer):
    """One choice per credit/cut; preserve historical states, never match by name."""
    choices = []
    seen = set()
    for row in rows:
        snapshot = snapshots.get(row.get("parent"))
        if not snapshot or clean_text(row.get("employer")) != clean_text(employer):
            continue
        linked = row.get("matched_client")
        if linked:
            if linked != client["name"]:
                continue
        elif not (canonical_identifier(row.get("client_number_core")) and
                  canonical_identifier(row.get("client_number_core")) == canonical_identifier(client.get("client_number"))):
            continue
        if (row.get("national_id") and client.get("national_id") and
                canonical_identifier(row["national_id"]) != canonical_identifier(client["national_id"])):
            continue
        credit = clean_text(row.get("credit_number"))
        if not credit:
            continue
        # Ambiguous duplicate credit rows in the same cut are not selectable.
        key = (row["parent"], canonical_identifier(credit))
        if key in seen:
            choices = [item for item in choices if item["key"] != key]
            continue
        seen.add(key)
        choices.append({"key": key, "row_name": row["name"], "credit_number": credit,
                        "snapshot": row["parent"], "report_date": str(snapshot.get("report_date") or ""),
                        "credit_status": row.get("credit_lifecycle") or row.get("credit_status") or "Por revisar"})
    return sorted(choices, key=lambda item: (item["report_date"], item["snapshot"], item["credit_number"]), reverse=True)


def load_detail_context(remittance_name, detail_row_name):
    import frappe
    from frappe import _
    from credinomina_reconciliation.client_registry import load_client_index

    doc = frappe.get_doc("CN Remittance Allocation", remittance_name)
    doc.check_permission("write")
    if doc.docstatus == 2:
        frappe.throw(_("No se puede modificar un depósito cancelado."))
    doc._assert_open_related_periods()
    row = next((item for item in doc.detail_rows if item.name == detail_row_name), None)
    if not row:
        frappe.throw(_("La fila no pertenece al detalle de este depósito."))
    clients = load_client_index()
    complete_detail_clients([row], clients, doc.employer)
    client, reason = choose_client(row, clients, doc.employer)
    if not client:
        frappe.throw(_("Identifique primero al cliente de la fila: {0}.").format(reason))
    frappe.get_doc("CN Client", client["name"]).check_permission("read")
    snapshots = frappe.get_list("CN Credit Portfolio Snapshot",
        filters={"status": ["in", ["Importado", "Importado con alertas"]]},
        fields=["name", "report_date"], limit_page_length=0)
    by_name = {item.name: item for item in snapshots}
    rows = frappe.get_all("CN Credit Portfolio Row",
        filters={"parent": ["in", list(by_name)], "parenttype": "CN Credit Portfolio Snapshot",
                 "employer": doc.employer},
        fields=["name", "parent", "credit_number", "matched_client", "client_number_core",
                "national_id", "employer", "credit_lifecycle", "credit_status"],
        limit_page_length=0) if by_name else []
    choices = portfolio_credit_choices(rows, by_name, client, doc.employer)
    return doc, row, client, choices


def get_detail_credits(remittance_name, detail_row_name):
    doc, row, client, choices = load_detail_context(remittance_name, detail_row_name)
    return {"client_name": client["client_name"], "client_number": client.get("client_number"),
            "current_credit": row.loan_number, "modified": str(doc.modified), "credits": choices}


def set_detail_credit(remittance_name, detail_row_name, portfolio_row_name, modified):
    import frappe
    from frappe import _
    from frappe.utils import now_datetime

    doc, row, client, choices = load_detail_context(remittance_name, detail_row_name)
    if str(doc.modified) != str(modified):
        frappe.throw(_("El depósito cambió. Recargue el formulario y vuelva a seleccionar el crédito."))
    selected = next((item for item in choices if item["row_name"] == portfolio_row_name), None)
    if not selected:
        frappe.throw(_("El crédito no pertenece al cliente y empresa en un corte de cartera permitido."))
    previous_credit = row.loan_number or "Sin crédito"
    row.loan_number = selected["credit_number"]
    row.loan_selection_snapshot = selected["snapshot"]
    row.loan_selection_note = _("{0} — {1}: {2} → {3}. Fila de cartera: {4}.").format(
        now_datetime(), frappe.session.user, previous_credit, row.loan_number, portfolio_row_name,
    )
    row.match_status = "Pendiente"
    row.match_reason = _("Crédito seleccionado de cartera; pendiente de conciliación.")
    row.matched_targets = "[]"
    row.matched_targets_summary = "Crédito seleccionado de cartera; use Conciliar para identificar sus destinos."
    doc.detail_status = "Cargado; pendiente de conciliación"
    doc.result = "Pendiente"
    doc.flags.portfolio_selected_detail = row.name
    doc.save()
    return {"client_number": client.get("client_number"), "loan_number": row.loan_number}
