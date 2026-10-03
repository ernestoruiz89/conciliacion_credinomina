"""Permission-aware, read-only view of the complete recorded cash distribution."""
import frappe

from credinomina_reconciliation.client_credit import CATEGORY as CLIENT_CREDIT
from credinomina_reconciliation.company_credit import CATEGORY as COMPANY_CREDIT
from credinomina_reconciliation.control_deposits import _credit_key, _load_credit_people
from credinomina_reconciliation.remittance_target_summary import read_targets
from credinomina_reconciliation.rounding import money, money_float


@frappe.whitelist()
def get_distribution(remittance_name):
    deposit = frappe.get_doc("CN Remittance Allocation", remittance_name)
    deposit.check_permission("read")
    data = deposit.as_dict()
    if deposit.docstatus != 1:
        return build_distribution(data)

    entries = read_targets(data.get("allocation_detail")) or []
    item_ids = {e.get("partida") or e.get("movimiento") for e in entries} - {None, ""}
    items, balances = {}, []
    if frappe.has_permission("CN Complementary Item", "read"):
        fields = ["name", "category", "employer", "period", "client_name", "source_client_name",
                  "client_number", "loan_number", "description", "amount_usd", "result",
                  "credit_pending_usd", "credit_management_status", "registered_deposit"]
        names = sorted(item_ids)
        for offset in range(0, len(names), 500):
            for item in frappe.get_list("CN Complementary Item", filters={
                "name": ["in", names[offset:offset + 500]], "docstatus": 1,
            }, fields=fields, limit_page_length=0):
                items[item["name"]] = item
        balances = frappe.get_list("CN Complementary Item", filters={
            "registered_deposit": deposit.name, "docstatus": 1,
            "category": ["in", [CLIENT_CREDIT, COMPANY_CREDIT]],
            "result": "Saldo a favor documentado",
        }, fields=fields, order_by="creation asc, name asc", limit_page_length=0)

    period_ids = {e.get("periodo") for e in entries} | {
        item.get("period") for item in [*items.values(), *balances]
    }
    periods = {}
    if frappe.has_permission("CN Reconciliation Period", "read"):
        names = sorted(period_ids - {None, ""})
        for offset in range(0, len(names), 500):
            for period in frappe.get_list("CN Reconciliation Period", filters={
                "name": ["in", names[offset:offset + 500]],
            }, fields=["name", "employer"], limit_page_length=0):
                periods[period["name"]] = period
    people = _load_credit_people([data], periods)
    return build_distribution(data, items, balances, periods, people)


def build_distribution(deposit, items=None, balances=(), periods=None, people=None):
    """Never use planned targets or turn credit balances into loan payments."""
    total = money(deposit.get("amount_usd"))
    base = {"deposit": deposit["name"], "docstatus": deposit.get("docstatus", 0),
            "result": deposit.get("result") or "Pendiente", "total_usd": money_float(total)}
    if deposit.get("docstatus") != 1:
        return {**base, "rows": [], "distributed_usd": 0, "detailed_usd": 0,
                "pending_usd": money_float(total), "consistent": True}

    items, periods, people = items or {}, periods or {}, people or {}
    entries = read_targets(deposit.get("allocation_detail"))
    rows = {}

    def add(category, amount, *, person=None, employer="", period="", item=None,
            state="Distribuido", description="", difference=None):
        person, item = person or {}, item or {}
        visible_period = periods.get(period, {})
        record_type = "CN Complementary Item" if item else "CN Reconciliation Period" if visible_period else ""
        record = item.get("name") or visible_period.get("name") or ""
        client_name = person.get("client_name") or item.get("client_name") or item.get("source_client_name") or ""
        client_number = person.get("client_number") or item.get("client_number") or ""
        loan = person.get("loan_number") or item.get("loan_number") or ""
        employer = employer or item.get("employer") or visible_period.get("employer") or ""
        # Do not combine unknown credit customers into an invented identity.
        identity = person.get("identity") if not client_name and not client_number else ""
        key = (category, record_type, record, employer, client_name, client_number, loan, identity, state)
        row = rows.setdefault(key, {"category": category, "client_name": client_name,
            "client_number": client_number, "loan_number": loan, "employer": employer,
            "period": visible_period.get("name") or "", "record_doctype": record_type,
            "record_name": record, "state": state, "description": description,
            "amount_usd": money(0), "management_pending_usd": (
                money_float(item.get("credit_pending_usd")) if category == CLIENT_CREDIT else None),
            "management_status": (item.get("credit_management_status") or "Pendiente") if category == CLIENT_CREDIT else "",
            "difference_usd": difference})
        row["amount_usd"] += money(amount)

    logged = money(0)
    for entry in entries or []:
        amount = money(entry.get("importe_usd"))
        logged += amount
        period = entry.get("periodo")
        if entry.get("tipo") in {"Cobranza", "Aplicacion historica"}:
            person = {**people.get(_credit_key(entry), {}), "identity": _credit_key(entry)}
            add("Pago a crédito", amount, person=person, period=period)
        elif entry.get("tipo") == "Partida complementaria":
            item = items.get(entry.get("partida"), {})
            # Allocation JSON is not authority to reveal another document's identity.
            person = ({"client_name": entry.get("cliente"), "client_number": entry.get("nro_cliente"),
                       "loan_number": entry.get("credito")} if item and entry.get("fila_detalle") else {})
            add(item.get("category") or "Partida complementaria (detalle no disponible)", amount,
                person=person, employer=entry.get("empresa") if item else "", period=period,
                item=item, description=item.get("description") or "")
        elif entry.get("tipo") == "Movimiento de conciliación":
            item = items.get(entry.get("movimiento"), {})
            add("Ajuste de conciliación", amount, period=period, item=item,
                description=item.get("description") or entry.get("origen") or "",
                difference=money_float(entry.get("diferencia_usd")))
        else:
            add("Distribución por revisar", amount, state="Revisar")

    allocated = money(deposit.get("allocated_usd"))
    if allocated != logged:
        add("Distribución sin detalle disponible", allocated - logged, state="Revisar")

    credit_total = money(0)
    used_balances = set()
    for item in balances:
        if (item.get("name") in used_balances or item.get("docstatus", 1) != 1
                or item.get("registered_deposit") != deposit["name"]
                or item.get("category") not in {CLIENT_CREDIT, COMPANY_CREDIT}
                or item.get("result") != "Saldo a favor documentado"):
            continue
        used_balances.add(item.get("name"))
        credit_total += money(item.get("amount_usd"))
        add(item["category"], item.get("amount_usd"), item=item, period=item.get("period"),
            state="Documentado",
            description=item.get("description") or "")

    recorded_credit = money(deposit.get("justified_surplus_usd"))
    if recorded_credit > credit_total:
        add("Saldo a favor (detalle no disponible)", recorded_credit - credit_total, state="Documentado")
    detailed = sum((row["amount_usd"] for row in rows.values()), money(0))
    distributed = allocated + recorded_credit
    return {**base, "rows": [{**row, "amount_usd": money_float(row["amount_usd"])} for row in rows.values()],
            "distributed_usd": money_float(distributed), "detailed_usd": money_float(detailed),
            "pending_usd": money_float(total - distributed),
            "consistent": entries is not None and detailed == distributed}
