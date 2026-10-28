"""Read-only financial position of complementary items; accounting is separate."""
from collections import defaultdict
from contextvars import ContextVar
from functools import wraps
import json
import frappe
from credinomina_reconciliation.complementary_subcategories import EMPLOYEE_RECEIVABLE
from credinomina_reconciliation.rounding import money, money_float

CREDIT_CATEGORIES = {"Saldo a favor del cliente", "Saldo a favor de la empresa"}
CASH_CATEGORIES = {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Cobro de CxC"}
FIELDS = ["name", "category", "docstatus", "employer", "period", "posting_date", "amount_usd", "description",
          "accounting_status", "review_status", "review_action", "related_application", "application_adjustment_usd",
          "application_adjustment_history",
          "compensated_usd", "compensation_pending_usd", "compensation_status", "status", "result",
          "credit_pending_usd", "credit_management_status", "credit_usage_issue", "credit_assigned_to", "credit_commitment_date", "credit_origin_date", "reclassification_date",
          "registration_exception", "subcategory_effect", "receivable_origin", "receivable_due_date", "receivable_due_origin", "receivable_setup_pending",
          "accounting_source_key", "source_debit", "source_credit", "source_currency", "source_fx_rate", "origin_deposit"]
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
    from credinomina_reconciliation.imported_complement_amount import ledger_magnitude
    ledger = ledger_magnitude(item)
    amount_issue = ledger is not None and abs(original) != ledger
    if amount_issue:
        original = ledger * (-1 if original < 0 else 1)
    used, pending = money(0), abs(original)
    state, kind = "Pendiente", "Por clasificar"
    management = None
    confirmed = item.get("docstatus") == 1
    category = item.get("category")
    if item.get("docstatus") == 2:
        state, pending = "Cancelada", money(0)
    elif item.get('origin_deposit'):
        state, kind, pending = 'Origen contable vinculado', 'Respaldo del depósito', money(0)
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
        from credinomina_reconciliation.application_adjustment_links import total
        used = total(item) if confirmed else money(0)
        pending = abs(original) - used
        state = "Conciliada" if not pending else "Parcial" if used else "Pendiente de confirmar"
    elif category == "Diferencia por tolerancia":
        kind = "Ajuste interno"
        used, pending = (abs(original), money(0)) if confirmed else (money(0), abs(original))
        state = "Revertida" if item.get("status") == "Revertido" else "Vigente" if confirmed else "Pendiente"
    elif item.get("review_action") == "No conciliatoria":
        # A descriptive classification is not a payment, offset or waiver.
        # Keep the original ledger balance visible until a real link settles it.
        state = "Por aclarar (no conciliatoria)"
    elif category in CASH_CATEGORIES:
        kind = "Distribuido en depósitos"
        assigned = sum((money(row.get("amount_usd")) for row in distributions), money(0))
        used = assigned if original >= 0 else -assigned
        pending = abs(original) - used
        state = "Conciliada" if not pending and confirmed else "Parcial" if used else "Pendiente" if confirmed else "Pendiente de confirmar"
    if pending < 0 or used < 0 or (amount_issue and item.get('docstatus') != 2):
        state = "Revisar distribución"
    credit_issue = item.get('credit_usage_issue') if category in CREDIT_CATEGORIES and confirmed else ''
    if credit_issue:
        state, management = 'Revisar distribución', None
    return {"name": item.get("name"), "original_usd": money_float(original), "used_usd": money_float(used),
            "pending_usd": money_float(pending), "financial_status": state, "used_label": kind,
            "accounting_status": item.get("accounting_status") or "Pendiente de registro",
            "management_pending_usd": money_float(management) if management is not None else None,
            "management_status": item.get("credit_management_status") or "Pendiente" if management is not None else "",
            "amount_issue": credit_issue or ("El importe difiere de la evidencia contable original" if amount_issue else ""),
            "distributions": list(distributions)}


def reconciliation_state(balance):
    """Shared financial state for forms, report rows and transaction counters."""
    status = balance.get("financial_status")
    pending, used = balance.get("pending_usd"), balance.get("used_usd")
    if status in {"Cancelada", "Revertida", "Parcialmente revertida"}:
        return status
    if status == "Revisar distribución" or (pending is not None and money(pending) < 0):
        return "Revisar distribución"
    if status == 'Origen contable vinculado':
        return 'Vinculado a depósito'
    if pending is None:
        return "Pendiente"
    if money(pending) == 0 and status in {
        "Conciliada", "Documentado", "Vigente", "Registro contable verificado",
    }:
        return "Conciliado"
    return "Parcial" if money(used) > 0 and money(pending) > 0 else "Pendiente"


def _distribution_index(wanted=None, *, authoritative=False):
    from credinomina_reconciliation.report_records import records, require_complete_evidence, _pages
    # Internal status maintenance reads complete journals. Ordinary financial
    # views retain their permission checks and never expose hidden evidence.
    read = (lambda doctype, **kwargs: _pages(frappe.get_all, doctype, **kwargs)) if authoritative else records
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
        filters = {"docstatus": 1, "allocation_detail": ["like", '%"Partida complementaria"%']}
        readable = set()
        for deposit in read("CN Remittance Allocation", filters=filters,
                fields=["name", "allocation_detail", "deposit_date"], **kwargs):
            readable.add(deposit.name)
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
        def relevant(deposit):
            if wanted is None:
                return True
            try:
                entries = json.loads(deposit.allocation_detail or "[]")
                if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                    raise ValueError("Invalid journal")
                return any(entry.get("tipo") == "Partida complementaria" and entry.get("partida") in wanted
                           for entry in entries)
            except (ValueError, TypeError):
                return any(json.dumps(name, ensure_ascii=ascii_only) in (deposit.allocation_detail or "")
                           for name in wanted for ascii_only in (True, False))
        if not authoritative:
            require_complete_evidence("CN Remittance Allocation", readable, filters=filters,
                                      fields=["allocation_detail"], relevant=relevant, **kwargs)
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
def get_list_balances(names):
    """Batch the visible list only; never trust client-supplied document data."""
    from credinomina_reconciliation.report_records import records
    names = frappe.parse_json(names) if isinstance(names, str) else names
    if not isinstance(names, list) or len(names) > 500 or any(not isinstance(name, str) for name in names):
        frappe.throw("Indique hasta 500 partidas válidas.")
    if not names or not frappe.has_permission("CN Complementary Item", "read"):
        return {}
    items = list(records("CN Complementary Item", filters={"name": ["in", names]}, fields=FIELDS))
    # Missing evidence permission must not look like an unpaid balance.
    can_read_cash = frappe.has_permission("CN Remittance Allocation", "read")
    available = [item for item in items if item.get('category') not in CASH_CATEGORIES or can_read_cash]
    balances = load_balances(available)
    receivables = {}
    if can_read_cash:
        from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
        from credinomina_reconciliation.receivable_recovery import is_receivable
        origins = [item['name'] for item in available if is_receivable(item)]
        if origins:
            for row in load_receivables(item_names=origins):
                previous = receivables.get(row['name'], money(0))
                receivables[row['name']] = None if previous is None or row['receivable_usd'] is None else previous + money(row['receivable_usd'])
    return {item['name']: {
        'state': balances[item['name']]['financial_status'] if item.get('docstatus') == 2 else reconciliation_state(balances[item['name']]),
        'original_usd': balances[item['name']]['original_usd'],
        'used_usd': balances[item['name']]['used_usd'],
        'pending_usd': balances[item['name']]['pending_usd'],
        'management_status': balances[item['name']]['management_status'],
        'management_pending_usd': balances[item['name']]['management_pending_usd'],
        'receivable_usd': None if receivables.get(item['name'], 0) is None else money_float(receivables.get(item['name'], 0)),
        'company_receivable_usd': 0 if item.get('subcategory_effect') == EMPLOYEE_RECEIVABLE else (
            None if receivables.get(item['name'], 0) is None else money_float(receivables.get(item['name'], 0))),
        'employee_receivable_usd': 0 if item.get('subcategory_effect') != EMPLOYEE_RECEIVABLE else (
            None if receivables.get(item['name'], 0) is None else money_float(receivables.get(item['name'], 0))),
        'amount_issue': balances[item['name']]['amount_issue'],
    } for item in available}


@frappe.whitelist()
@cached_balance_reads
def get_balance(item_name):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    balances = load_balances([item])
    from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables, receivable_total
    from credinomina_reconciliation.receivable_recovery import is_receivable
    receipts = []
    if is_receivable(item):
        from credinomina_reconciliation.deposit_adjustment_receivables import load_settlements
        receipts = load_settlements([item], include_cancelled=True)
        balances.update(load_balances(receipts))
    receivables = build_receivables([item], balances, settlements=receipts, settlement_balances=balances, include_settled=True)
    value = balances[item.name]
    value['reconciliation_state'] = reconciliation_state(value)
    value['receivable_usd'] = receivable_total(receivables)
    value['company_receivable_usd'] = receivable_total(row for row in receivables if row.get('subcategory_effect') != EMPLOYEE_RECEIVABLE)
    value['employee_receivable_usd'] = receivable_total(row for row in receivables if row.get('subcategory_effect') == EMPLOYEE_RECEIVABLE)
    value['receivable_issue_count'] = sum(row['receivable_usd'] is None for row in receivables)
    value['receivable_companies'] = receivables
    value['recoveries'] = [dict(receipt,
        effective_usd=money_float(money(receipt.get('compensated_usd')) if receipt.get('docstatus') == 1
            and receipt.get('category') == 'Compensación entre partidas'
            else money(balances[receipt['name']]['used_usd']) if receipt.get('docstatus') == 1 else money(0)),
        financial_status=balances[receipt['name']]['financial_status']) for receipt in receipts]
    return value
