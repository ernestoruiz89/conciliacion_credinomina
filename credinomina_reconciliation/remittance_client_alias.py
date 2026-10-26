"""Register a detail's unknown name only against independently verified identity."""
import frappe
from frappe import _

from credinomina_reconciliation.client_identity import matching_name
from credinomina_reconciliation.client_registry import load_client_index
from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.paying_employers import allowed_employers, choose_detail_client

NAME_ISSUE = "Conflicto: no se pudo verificar Nombre y apellidos en los clientes de la empresa"


def _deposit(name, lock=False):
    doc = frappe.get_doc("CN Remittance Allocation", name, **({"for_update": True} if lock else {}))
    doc.check_permission("write")
    if doc.docstatus == 2 or doc.get("consolidated_deposit") or doc.get("cash_identity_status") == "Origen vinculado":
        frappe.throw(_("El depósito no está disponible para registrar alias."))
    doc._assert_open_related_periods()
    return doc


def _candidate(doc, detail_row_name):
    row = next((item for item in doc.detail_rows if item.name == detail_row_name), None)
    if not row:
        frappe.throw(_("La fila no pertenece al detalle de este depósito."))
    if not clean_text(row.client_name) or not any(clean_text(row.get(field)) for field in
                                                ("loan_number", "client_number", "employee_number")):
        frappe.throw(_("Indique crédito, número de cliente o empleado para verificar el alias."))
    clients = load_client_index()
    allowed = allowed_employers(doc.employer)
    # Load evidence using the numeric identifiers, independently of the unknown
    # name. All supplied identifiers, including ID card and company, still apply.
    identity = row.as_dict()
    identity["client_name"] = ""
    loans = load_detail_loan_clients([identity], clients, allowed)
    _client, issue = choose_detail_client(row, clients, doc.employer, allowed, loans)
    if issue != NAME_ISSUE:
        frappe.throw(_("Crear alias solo aplica cuando el nombre no se puede verificar. Revise la identificación actual de la fila."))
    candidate, reason = choose_detail_client(identity, clients, doc.employer, allowed, loans)
    if not candidate or (row.get("client") and row.client != candidate["name"]):
        frappe.throw(_("Los identificadores no permiten verificar un único cliente: {0}.").format(reason))
    profiles = [profile for records in getattr(loans, "company_clients", {}).values() for profile in records.values()]
    if any(person["name"] != candidate["name"] and person.get("employer") == candidate["employer"]
           and matching_name(row.client_name, person) for person in [*clients, *profiles]):
        frappe.throw(_("El nombre ya corresponde a otro cliente de esta empresa. Revise la fila antes de registrar un alias."))
    return row, candidate


@frappe.whitelist()
def preview_alias(remittance_name, detail_row_name):
    doc = _deposit(remittance_name)
    row, candidate = _candidate(doc, detail_row_name)
    client = frappe.get_doc("CN Client", candidate["name"])
    client.check_permission("read")
    client.check_permission("write")
    return dict(client=client.name, client_name=client.client_name, client_number=client.client_number,
        employee_number=candidate.get("employee_number") or "", employer=candidate["employer"],
        alias_name=clean_text(row.client_name), loan_number=row.loan_number or "",
        modified=str(doc.modified), client_modified=str(client.modified))


@frappe.whitelist(methods=["POST"])
def create_alias(remittance_name, detail_row_name, client_name, modified, client_modified):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies

    initial = _deposit(remittance_name)
    lock_cash_pool(reconciliation_companies(initial.employer))
    doc = _deposit(remittance_name, lock=True)
    if str(doc.modified) != str(modified):
        frappe.throw(_("El depósito cambió. Vuelva a abrir Crear alias."))
    row, candidate = _candidate(doc, detail_row_name)
    if candidate["name"] != client_name:
        frappe.throw(_("El cliente seleccionado no corresponde a los identificadores de esta fila."))
    client = frappe.get_doc("CN Client", client_name, for_update=True)
    client.check_permission("read")
    client.check_permission("write")
    if str(client.modified) != str(client_modified):
        frappe.throw(_("El cliente cambió. Vuelva a abrir Crear alias."))
    alias = clean_text(row.client_name)
    # Normal validations protect duplicate aliases and deposit evidence. The
    # complete identity is checked again on save, within the same transaction.
    client.append("aliases", {"alias_name": alias})
    client.save()
    client.add_comment("Comment", _("Alias {0} registrado desde el depósito {1}, fila {2}.").format(
        alias, doc.name, row.source_row or row.idx))
    row.match_status = "Pendiente"
    row.match_reason = _("Alias registrado; use Conciliar para actualizar los destinos.")
    doc.detail_status = "Cargado; pendiente de conciliación"
    doc.result = "Pendiente"
    doc.save()  # Re-identifies the detail with normal cash/closed-period guards.
    if row.client != client.name:
        frappe.throw(_("El alias no resolvió la identidad de la fila. Revise sus identificadores."))
    doc.add_comment("Comment", _("Alias {0} agregado al cliente {1} desde la fila {2} del detalle.").format(
        alias, client.name, row.source_row or row.idx))
    return dict(client=client.name, alias_name=alias)
