"""Standalone financial/accounting work, including records without a period."""
from datetime import date
import frappe
from credinomina_reconciliation.complementary_balances import FIELDS, CREDIT_CATEGORIES, load_balances


def build_follow_up(items, balances, exceptions, *, as_of=None, credit_periods=None, adjustment_receivables=()):
    today = str(as_of or date.today())[:10]
    tasks = []
    def add(item, kind, summary, action, amount=None, *, exception=False):
        related = (credit_periods or {}).get(item["name"], []) if not item.get("period") else []
        due = item.get("commitment_date") if exception else item.get("credit_commitment_date")
        responsible = item.get("assigned_to") if exception else item.get("credit_assigned_to")
        due = str(due)[:10] if due else None
        tasks.append({"priority": 0 if due and due < today else 1 if kind == "accounting_registration" else 2,
            "kind": kind, "employer": item.get("employer"), "employer_name": item.get("employer") or "Sin empresa confirmada",
            "period": item.get("period"), "period_label": item.get("period") or " · ".join(related) or "Sin período",
            "related_periods": related,
            "period_context": ("Períodos del depósito vinculados al cliente" if item.get("category") == "Saldo a favor del cliente"
                               else "Períodos del depósito") if related else "",
            "summary": summary, "next_action": action, "amount_usd": amount, "due_date": due,
            "category": item.get("category") if not exception else None,
            "client_name": item.get("client_name") or "",
            "client_number": item.get("client_number") or "",
            "responsible": responsible or "", "target_doctype": "CN Reconciliation Exception" if exception else "CN Complementary Item",
            "target_name": item["name"], "action_label": {
                'company_receivable': 'Aplicar cobro / Compensar CxC', 'credit_management': 'Gestionar saldo',
                'accounting_registration': 'Verificar asiento', 'complementary_balance': 'Revisar partida',
                'adjustment_classification': 'Clasificar ajuste', 'open_exception': 'Gestionar excepción',
            }.get(kind, 'Revisar caso')})
    for item in items:
        balance = balances[item["name"]]
        if item.get("docstatus") == 2:
            continue
        if item.get('category') == 'Ajuste de conciliación' and item.get('subcategory_effect') in (None, '', 'Por clasificar'):
            add(item, 'adjustment_classification', 'Ajuste de conciliación por clasificar',
                'Seleccione una subcategoría. Distribuir el depósito no determina si el faltante sigue siendo una CxC.', abs(balance['original_usd']))
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
    for item in adjustment_receivables:
        add(item, 'company_receivable', 'CxC a la empresa por ajuste pendiente de cobro',
            'Gestionar depósito o compensación vinculada. Registrar el asiento en el core no liquida esta deuda.', item['receivable_usd'])
    for item in exceptions:
        if item.get("status") not in {"Abierta", "En revision"}:
            continue
        add(item, "open_exception", "Excepción · " + (item.get("exception_type") or item.get("status")),
            item.get("next_action") or "Registrar la gestión, responsable y fecha compromiso.", item.get("amount_usd"), exception=True)
    return tasks


def load_credit_periods(items):
    """Display actual deposit destinations, never infer a period from its date.

    This is context only: a credit awaiting management is not paid to a loan.
    Child identities are resolved through their permission-checked parents.
    """
    from credinomina_reconciliation.control_deposits import _entries, _credit_key, _load_credit_people
    from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier

    credits = [item for item in items if item.get("category") in CREDIT_CATEGORIES
               and item.get("docstatus") == 1 and not item.get("period") and item.get("registered_deposit")
               and (not item.get("credit_management_status") or item.get("credit_pending_usd"))]
    if not credits or not frappe.has_permission("CN Remittance Allocation", "read") or not frappe.has_permission("CN Reconciliation Period", "read"):
        return {}
    deposits = []
    names = sorted({item["registered_deposit"] for item in credits})
    for offset in range(0, len(names), 500):
        deposits.extend(frappe.get_list("CN Remittance Allocation", filters={
            "name": ["in", names[offset:offset + 500]], "docstatus": 1,
        }, fields=["name", "allocation_detail"], limit_page_length=0))
    entries = {deposit["name"]: [entry for entry in _entries(deposit.get("allocation_detail"))
               if entry.get("tipo") in {"Cobranza", "Aplicacion historica"} and entry.get("importe_usd")]
               for deposit in deposits}
    names = sorted({entry["periodo"] for rows in entries.values() for entry in rows if entry.get("periodo")})
    periods = {}
    for offset in range(0, len(names), 500):
        periods.update({period["name"]: period for period in frappe.get_list("CN Reconciliation Period",
            filters={"name": ["in", names[offset:offset + 500]]}, fields=["name", "employer"], limit_page_length=0)})
    people = _load_credit_people(deposits, periods) if any(item["category"] == "Saldo a favor del cliente" for item in credits) else {}
    result = {}
    for item in credits:
        found = set()
        for entry in entries.get(item["registered_deposit"], []):
            period = periods.get(entry.get("periodo"))
            if not period or period.get("employer") != item.get("employer"):
                continue
            if item["category"] == "Saldo a favor del cliente":
                person = people.get(_credit_key(entry), {})
                client = canonical_identifier(item.get("client_number"))
                if not client or canonical_identifier(person.get("client_number")) != client:
                    continue
                if item.get("loan_number") and canonical_credit_number(item["loan_number"]) != canonical_credit_number(person.get("loan_number")):
                    continue
            found.add(period["name"])
        if found:
            result[item["name"]] = sorted(found)
    return result


def load_follow_up(year=None, employer=None):
    scope = {"employer": employer} if employer else {}
    items, exceptions = [], []
    if frappe.has_permission("CN Complementary Item", "read"):
        filters = {**scope, "docstatus": ["!=", 2]}
        if year:
            filters["posting_date"] = ["between", [f"{year}-01-01", f"{year}-12-31"]]
        items = frappe.get_list("CN Complementary Item", filters=filters,
            fields=[*FIELDS, "subcategory_effect", "accounting_exception", "client_name", "client_number", "loan_number", "registered_deposit"], limit_page_length=0)
    if frappe.has_permission("CN Reconciliation Exception", "read"):
        exceptions = frappe.get_list("CN Reconciliation Exception", filters={**scope, "status": ["in", ["Abierta", "En revision"]]},
            fields=["name", "employer", "period", "status", "exception_type", "amount_usd", "assigned_to", "commitment_date", "next_action", "creation"],
            limit_page_length=0)
        if year:
            exceptions = [item for item in exceptions if str(item.get("commitment_date") or item.get("creation") or "")[:4] == str(year)]
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    receivables = load_receivables(employer=employer, year=year) if frappe.has_permission('CN Complementary Item', 'read') and frappe.has_permission('CN Remittance Allocation', 'read') else []
    if not items and not exceptions and not receivables:
        return []
    from frappe.utils import nowdate
    return build_follow_up(items, load_balances(items), exceptions, as_of=nowdate(), credit_periods=load_credit_periods(items), adjustment_receivables=receivables)


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
    grouped = any('actions' in item for item in items)
    if grouped:
        items = [action for item in items for action in item.get('actions', [item])]
    today = str(as_of or date.today())[:10]
    groups = {"credits": {"credit_management"}, "complements": {"complementary_balance", "company_receivable", "adjustment_classification"},
              "accounting": {"accounting_registration"}, "exceptions": {"open_exception", "overdue_exception", "company_receivable", "adjustment_classification"},
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
    return group_work_cases(result) if grouped else result


def group_work_cases(tasks):
    """Count documents once while retaining independent financial/accounting work."""
    groups = {}
    for task in tasks:
        key = (task.get('target_doctype'), task.get('target_name'))
        if not all(key):
            key = (*key, task.get('kind'), task.get('employer'), task.get('period'))
        groups.setdefault(key, []).extend(task.get('actions', [task]))
    result = []
    for actions in groups.values():
        actions = sorted(actions, key=lambda item: (item.get('priority', 3), item.get('due_date') or '9999', item.get('kind') or ''))
        first = actions[0]
        group = dict(first, actions=actions, action_count=len(actions),
            amount_usd=first.get('amount_usd') if len(actions) == 1 else None)
        if len(actions) > 1:
            group['summary'] = first.get('category') or first.get('target_name') or 'Caso pendiente'
            group['next_action'] = 'Revise las acciones del caso; sus importes no se suman.'
        related = sorted({period for task in actions for period in task.get('related_periods') or []})
        if related and not group.get('period'):
            group['period_label'] = ' · '.join(related)
        result.append(group)
    return sorted(result, key=lambda row: (row.get('priority', 3), row.get('due_date') or '9999', row.get('target_name') or ''))
