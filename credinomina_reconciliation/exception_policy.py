"""Separate informative quality/accounting follow-up from financial closure."""
import frappe
from frappe import _

LEGACY_TYPES = {
    "No deducido", "Deduccion parcial", "Deduccion en exceso",
    "Pendiente de detalle de empresa", "Detalle de empresa sin coincidencia",
}
AREAS = {"Financiera", "Calidad de cobranza", "Registro contable", "Legado por revisar"}


def blocks_closure(document):
    value = document.get("blocks_closure")
    return value is None or str(value) not in {"0", "False", ""}


def validate_policy(document, previous=None):
    area = document.get("control_area") or "Financiera"
    if document.get("complementary_item"):
        if previous and not previous.get("complementary_item"):
            frappe.throw(_("Conserve la excepción original. Cree el seguimiento contable desde la partida complementaria."))
        area = "Registro contable"
        document.blocks_closure = 0  # Its origin_period is informational, not a cash claim.
    if area not in AREAS:
        frappe.throw(_("Seleccione el ámbito de la excepción."))
    document.control_area = area
    relaxed = not blocks_closure(document) and (not previous or blocks_closure(previous))
    changed = previous and (area != (previous.get("control_area") or "Financiera")
                           or blocks_closure(document) != blocks_closure(previous))
    if not document.get("complementary_item") and (relaxed or changed):
        if not set(frappe.get_roles()) & {"System Manager", "Supervisor Credinomina"}:
            frappe.throw(_("Un supervisor debe autorizar los cambios de ámbito o bloqueo de cierre."), frappe.PermissionError)
        if not (document.get("classification_reason") or "").strip():
            frappe.throw(_("Explique por qué esta clasificación permite o bloquea el cierre. No modifica saldos ni resuelve la excepción."))
    if area == "Legado por revisar" and not blocks_closure(document):
        frappe.throw(_("Clasifique primero el caso heredado antes de quitar su bloqueo de cierre."))
    if changed:
        document.append("follow_up_actions", {
            "action_type": "Ajuste",
            "details": _("Clasificación de control: {0} → {1}; bloquea cierre: {2} → {3}. Motivo: {4}").format(
                previous.get("control_area") or "Financiera", area,
                _("Sí") if blocks_closure(previous) else _("No"),
                _("Sí") if blocks_closure(document) else _("No"), document.get("classification_reason") or ""),
        })


def can_follow_up_closed(document, previous):
    """Only management of an already nonblocking case may continue after closing."""
    if not previous or blocks_closure(document) or blocks_closure(previous):
        return False
    frozen = ("period", "employer", "collection_row_id", "source_import", "source_row",
              "related_case_type", "related_case_id", "complementary_item", "origin_period",
              "control_area", "exception_type", "amount_usd", "amount_nio", "client_number", "loan_number")
    from credinomina_reconciliation.rounding import money
    return all(money(document.get(field)) == money(previous.get(field)) if field in {'amount_usd', 'amount_nio'}
               else str(document.get(field) or '') == str(previous.get(field) or '') for field in frozen)
