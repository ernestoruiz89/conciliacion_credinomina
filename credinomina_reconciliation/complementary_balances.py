"""Read-only financial position of complementary items; accounting is separate."""
from collections import defaultdict
import json
import frappe
from credinomina_reconciliation.rounding import money, money_float

CREDIT_CATEGORIES = {"Saldo a favor del cliente", "Saldo a favor de la empresa"}
CASH_CATEGORIES = {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"}
FIELDS = ["name", "category", "docstatus", "employer", "period", "posting_date", "amount_usd", "description",
          "accounting_status", "review_status", "review_action", "related_application", "application_adjustment_usd",
          "compensated_usd", "compensation_pending_usd", "compensation_status", "status", "result",
          "credit_pending_usd", "credit_management_status", "credit_assigned_to", "credit_commitment_date",
          "registration_exception"]


def financial_balance(item, distributions=()):
    original = money(item.get("amount_usd"))
    used, pending = money(0), abs(original)
    state, kind = "Pendiente", "Por clasificar"
    management = None
    confirmed = item.get("docstatus") == 1
    category = item.get("category")
    if item.get("docstatus") == 2:
        state, pending = "Cancelada", money(0)
    elif item.get("registration_exception"):
        state, pending = "Registro contable verificado", money(0)
    elif category in CREDIT_CATEGORIES:
        kind = "Saldo a favor documentado"
        if confirmed and item.get("result") == kind:
            used, pending, state = abs(original), money(0), "Documentado"
        management = money(item.get("credit_pending_usd") if item.get("credit_management_status") else original)
    elif category == "Compensación entre partidas":
        kind = "Compensado"
        used = money(item.get("compensated_usd"))
        pending = abs(original) - used
        state = "Conciliada" if not pending else "Parcial" if used else "Pendiente"
    elif category == "Ajuste de aplicación":
        kind = "Ajuste aplicado"
        used = money(item.get("application_adjustment_usd")) if confirmed and item.get("related_application") else money(0)
        pending = abs(original) - used
        state = "Conciliada" if not pending else "Parcial" if used else "Pendiente de confirmar"
    elif category == "Diferencia por tolerancia":
        kind = "Ajuste interno"
        used, pending = (abs(original), money(0)) if confirmed else (money(0), abs(original))
        state = "Revertida" if item.get("status") == "Revertido" else "Vigente" if confirmed else "Pendiente"
    elif item.get("review_action") == "No conciliatoria":
        state, pending = "No conciliatoria", money(0)
    elif category in CASH_CATEGORIES:
        kind = "Distribuido en depósitos"
        assigned = sum((money(row.get("amount_usd")) for row in distributions), money(0))
        used = assigned if original >= 0 else -assigned
        pending = abs(original) - used
        state = "Conciliada" if not pending and confirmed else "Parcial" if used else "Pendiente" if confirmed else "Pendiente de confirmar"
    if pending < 0 or used < 0:
        state = "Revisar distribución"
    return {"name": item.get("name"), "original_usd": money_float(original), "used_usd": money_float(used),
            "pending_usd": money_float(pending), "financial_status": state, "used_label": kind,
            "accounting_status": item.get("accounting_status") or "Pendiente de registro",
            "management_pending_usd": money_float(management) if management is not None else None,
            "management_status": item.get("credit_management_status") or "Pendiente" if management is not None else "",
            "distributions": list(distributions)}


def load_balances(items):
    names = sorted({item.get("name") for item in items if item.get("category") in CASH_CATEGORIES})
    distributions = defaultdict(list)
    seen = set()
    for offset in range(0, len(names), 100):
        wanted = set(names[offset:offset + 100])
        deposits = frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1},
            or_filters=[["allocation_detail", "like", "%" + name + "%"] for name in wanted],
            fields=["name", "allocation_detail", "deposit_date"], limit_page_length=0)
        for deposit in deposits:
            try:
                entries = json.loads(deposit.allocation_detail or "[]")
            except (ValueError, TypeError):
                frappe.throw("La distribución del depósito {0} no es legible; revise su evidencia.".format(deposit.name))
            for index, entry in enumerate(entries if isinstance(entries, list) else []):
                if not isinstance(entry, dict) or entry.get("partida") not in wanted or entry.get("tipo") != "Partida complementaria":
                    continue
                key = (deposit.name, index)
                if key not in seen:
                    seen.add(key)
                    distributions[entry["partida"]].append({"deposit": deposit.name, "date": str(deposit.deposit_date),
                        "amount_usd": money_float(entry.get("importe_usd")), "period": entry.get("periodo") or "",
                        "client": entry.get("cliente") or "", "employer": entry.get("empresa") or ""})
    return {item.get("name"): financial_balance(item, distributions.get(item.get("name"), [])) for item in items}


@frappe.whitelist()
def get_balance(item_name):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    balances = load_balances([item])
    from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables
    receivables = build_receivables([item], balances)
    value = balances[item.name]
    value['company_receivable_usd'] = money_float(sum((money(row['receivable_usd']) for row in receivables), money(0)))
    return value
