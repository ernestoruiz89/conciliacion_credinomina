"""Read-only financial position of complementary items; accounting is separate."""
from collections import defaultdict
from contextvars import ContextVar
from functools import wraps
import json
import frappe
from credinomina_reconciliation.rounding import money, money_float

CREDIT_CATEGORIES = {"Saldo a favor del cliente", "Saldo a favor de la empresa"}
CASH_CATEGORIES = {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Cobro de CxC"}
FIELDS = ["name", "category", "docstatus", "employer", "period", "posting_date", "amount_usd", "description",
          "accounting_status", "review_status", "review_action", "related_application", "application_adjustment_usd",
          "compensated_usd", "compensation_pending_usd", "compensation_status", "status", "result",
          "credit_pending_usd", "credit_management_status", "credit_assigned_to", "credit_commitment_date",
          "registration_exception", "subcategory_effect", "receivable_origin"]
_read_scope = ContextVar('cn_complementary_balance_read_scope', default=None)


def cached_balance_reads(function):
    """Reuse journals only during an explicitly read-only report operation.

    Never cache across requests or reconciliation commands: writes in those
    commands must see their updated distributions immediately.
    """
    @wraps(function)
    def read(*args, **kwargs):
        if _read_scope.get() is not None:
            return function(*args, **kwargs)
        token = _read_scope.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _read_scope.reset(token)
    return read


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
        if item.get('receivable_origin'):
            # A reversed recovery reopens the ORIGINAL receivable; its receipt
            # is historical evidence, not another obligation of the same amount.
            pending = money(0)
            state = 'Conciliada' if used == abs(original) else 'Parcialmente revertida' if used else 'Revertida'
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


def _distribution_index(wanted=None):
    from credinomina_reconciliation.report_records import records
    distributions = defaultdict(list)
    if wanted is not None and not wanted:
        return distributions
    names = sorted(wanted) if wanted is not None else []
    # Narrow individual/form reads without a company or date restriction:
    # shared deposits and later payments must still be included. Large report
    # populations use one paged scan instead of hundreds of repeated LIKE scans.
    batches = [None] if wanted is None or len(names) > 500 else [names[i:i + 100] for i in range(0, len(names), 100)]
    visited = set()
    for batch in batches:
        kwargs = {}
        if batch is not None:
            tokens = {json.dumps(name, ensure_ascii=ascii_only) for name in batch for ascii_only in (True, False)}
            patterns = sorted(token.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') for token in tokens)
            kwargs['or_filters'] = [['allocation_detail', 'like', '%' + token + '%'] for token in patterns]
        for deposit in records("CN Remittance Allocation", filters={"docstatus": 1,
                "allocation_detail": ["like", '%"Partida complementaria"%']},
                fields=["name", "allocation_detail", "deposit_date"], **kwargs):
            if deposit.name in visited:
                continue
            visited.add(deposit.name)
            try:
                entries = json.loads(deposit.allocation_detail or "[]")
            except (ValueError, TypeError):
                frappe.throw("La distribución del depósito {0} no es legible; revise su evidencia.".format(deposit.name))
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                frappe.throw("La distribución del depósito {0} no es legible; revise su evidencia.".format(deposit.name))
            for entry in entries:
                if (not entry.get('partida') or entry.get("tipo") != "Partida complementaria"
                        or (wanted is not None and entry['partida'] not in wanted)):
                    continue
                distributions[entry["partida"]].append({"deposit": deposit.name, "date": str(deposit.deposit_date),
                    "amount_usd": money_float(entry.get("importe_usd")), "period": entry.get("periodo") or "",
                    "client": entry.get("cliente") or "", "employer": entry.get("empresa") or ""})
    return distributions


def load_balances(items):
    items = list(items)
    wanted = {item.get('name') for item in items if item.get('category') in CASH_CATEGORIES}
    distributions = {}
    if wanted:
        scope = _read_scope.get()
        if scope is None:
            distributions = _distribution_index(wanted)
        else:
            distributions = scope.setdefault('distributions', {})
            completed = scope.setdefault('distribution_names', set())
            missing = wanted - completed
            if missing:
                distributions.update(_distribution_index(missing))
                completed.update(missing)
    # Return separate dictionaries; a consumer cannot mutate the cached index.
    return {item.get('name'): financial_balance(item,
        [dict(entry) for entry in distributions.get(item.get('name'), [])]) for item in items}


@frappe.whitelist()
@cached_balance_reads
def get_balance(item_name):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    balances = load_balances([item])
    from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables
    from credinomina_reconciliation.receivable_recovery import is_receivable
    receipts = []
    if is_receivable(item):
        from credinomina_reconciliation.deposit_adjustment_receivables import load_settlements
        receipts = load_settlements([item], include_cancelled=True)
        balances.update(load_balances(receipts))
    receivables = build_receivables([item], balances, settlements=receipts, settlement_balances=balances, include_settled=True)
    value = balances[item.name]
    value['company_receivable_usd'] = money_float(sum((money(row['receivable_usd']) for row in receivables), money(0)))
    value['receivable_companies'] = receivables
    value['recoveries'] = [dict(receipt,
        effective_usd=money_float(money(receipt.get('compensated_usd')) if receipt.get('docstatus') == 1
            and receipt.get('category') == 'Compensación entre partidas'
            else money(balances[receipt['name']]['used_usd']) if receipt.get('docstatus') == 1 else money(0)),
        financial_status=balances[receipt['name']]['financial_status']) for receipt in receipts]
    return value
