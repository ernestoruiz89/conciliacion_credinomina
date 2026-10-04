"""Standalone financial/accounting work, including records without a period."""
from datetime import date
import frappe
from credinomina_reconciliation.complementary_balances import FIELDS, CREDIT_CATEGORIES, load_balances


def build_follow_up(items, balances, exceptions, *, as_of=None):
    today = str(as_of or date.today())[:10]
    tasks = []
    def add(item, kind, summary, action, amount=None, *, exception=False):
        due = item.get("commitment_date") if exception else item.get("credit_commitment_date")
        responsible = item.get("assigned_to") if exception else item.get("credit_assigned_to")
        due = str(due)[:10] if due else None
        tasks.append({"priority": 0 if due and due < today else 1 if kind == "accounting_registration" else 2,
            "kind": kind, "employer": item.get("employer"), "employer_name": item.get("employer") or "Sin empresa confirmada",
            "period": item.get("period"), "period_label": item.get("period") or "Sin período",
            "summary": summary, "next_action": action, "amount_usd": amount, "due_date": due,
            "responsible": responsible or "", "target_doctype": "CN Reconciliation Exception" if exception else "CN Complementary Item",
            "target_name": item["name"]})
    for item in items:
        balance = balances[item["name"]]
        if item.get("docstatus") == 2:
            continue
        if balance["financial_status"] == "Revisar distribución" or balance["pending_usd"]:
            add(item, "complementary_balance", "Partida complementaria · " + balance["financial_status"],
                "Revisar el concepto y distribuir, ajustar o compensar el importe pendiente.", balance["pending_usd"])
        if item.get("category") in CREDIT_CATEGORIES and item.get("docstatus") == 1 and balance["management_pending_usd"]:
            add(item, "credit_management", item["category"] + " por gestionar",
                "Documentar devolución o aplicación externa con referencia y soporte; no vuelve a liberar el depósito original.", balance["management_pending_usd"])
        if (not item.get("registration_exception") and not item.get("accounting_exception")
                and balance["accounting_status"] in {"Pendiente de registro", "Asiento informado"}
                and balance["financial_status"] not in {"Cancelada", "Revertida", "No conciliatoria"}):
            add(item, "accounting_registration", "Partida pendiente de registro o verificación en el core",
                "Crear / Ver excepción de registro contable y verificar el asiento contra la importación.", abs(balance["original_usd"]))
    for item in exceptions:
        if item.get("status") not in {"Abierta", "En revision"}:
            continue
        add(item, "open_exception", "Excepción · " + (item.get("exception_type") or item.get("status")),
            item.get("next_action") or "Registrar la gestión, responsable y fecha compromiso.", item.get("amount_usd"), exception=True)
    return tasks


def load_follow_up(year=None, employer=None):
    scope = {"employer": employer} if employer else {}
    items, exceptions = [], []
    if frappe.has_permission("CN Complementary Item", "read"):
        filters = {**scope, "docstatus": ["!=", 2]}
        if year:
            filters["posting_date"] = ["between", [f"{year}-01-01", f"{year}-12-31"]]
        items = frappe.get_list("CN Complementary Item", filters=filters, fields=[*FIELDS, "accounting_exception"], limit_page_length=0)
    if frappe.has_permission("CN Reconciliation Exception", "read"):
        exceptions = frappe.get_list("CN Reconciliation Exception", filters={**scope, "status": ["in", ["Abierta", "En revision"]]},
            fields=["name", "employer", "period", "status", "exception_type", "amount_usd", "assigned_to", "commitment_date", "next_action", "creation"],
            limit_page_length=0)
        if year:
            exceptions = [item for item in exceptions if str(item.get("commitment_date") or item.get("creation") or "")[:4] == str(year)]
    if not items and not exceptions:
        return []
    from frappe.utils import nowdate
    return build_follow_up(items, load_balances(items), exceptions, as_of=nowdate())


def merge_follow_up(existing, additional):
    # Replace the period-only overdue view with the complete actionable case.
    identities = {(item["target_doctype"], item["target_name"]) for item in additional}
    result = [item for item in existing if (item.get("target_doctype"), item.get("target_name")) not in identities]
    return sorted([*result, *additional], key=lambda item: (
        item["priority"], item.get("due_date") or "9999-12-31", item.get("employer_name") or "",
        item.get("period_label") or "", item.get("summary") or ""))


def filter_work(items, kind="", responsible="", due="", as_of=None):
    if not (kind or responsible or due):
        return items
    today = str(as_of or date.today())[:10]
    groups = {"credits": {"credit_management"}, "complements": {"complementary_balance"},
              "accounting": {"accounting_registration"}, "exceptions": {"open_exception", "overdue_exception"},
              "deposits": {"deposit_detail", "review_targets", "review_deposit_detail", "unassigned_deposit", "classify_bank"}}
    result = []
    for item in items:
        if kind in groups and item.get("kind") not in groups[kind]:
            continue
        if kind == "periods" and item.get("kind") in set().union(*groups.values()):
            continue
        if responsible and responsible.casefold() not in str(item.get("responsible") or "").casefold():
            continue
        date_value = item.get("due_date")
        if due == "overdue" and not (date_value and date_value < today):
            continue
        if due == "undated" and date_value:
            continue
        if due == "upcoming" and not (date_value and date_value >= today):
            continue
        result.append(item)
    return result
