"""Standalone financial/accounting work, including records without a period."""
from datetime import date
import frappe
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.complementary_balances import FIELDS, CREDIT_CATEGORIES, load_balances


TASK_AMOUNT_LABELS = {
    'application_cash': 'Pendiente de cubrir',
    'employee_shortfall': 'Diferencia de control (informativa)',
    'credit_management': 'Pendiente de devolver/aplicar',
    'company_receivable': 'Pendiente de cobrar',
    'employee_receivable': 'Pendiente de cobrar',
    'accounting_registration': 'Importe por registrar/verificar',
    'complementary_balance': 'Pendiente de distribuir/compensar',
    'adjustment_classification': 'Importe por clasificar',
    'deposit_detail': 'Depósito por detallar',
    'review_deposit_detail': 'Importe del depósito por revisar',
    'unassigned_deposit': 'Dinero sin distribuir',
    'classify_bank': 'Importe por identificar',
    'pending_application': 'Depósito pendiente de aplicar',
    'historical_application': 'Aplicado por vincular',
    'operational_application': 'Aplicado por vincular',
}


def build_follow_up(items, balances, exceptions, *, as_of=None, credit_periods=None, adjustment_receivables=(), identity_deposits=()):
    today = str(as_of or date.today())[:10]
    tasks = []
    def add(item, kind, summary, action, amount=None, *, exception=False, amount_kind=None):
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
            "summary": summary, "next_action": action, "amount_usd": amount,
            "amount_kind": amount_kind or TASK_AMOUNT_LABELS.get(kind, 'Importe del caso'), "due_date": due,
            "event_date": str(item.get('origin_date') or item.get('posting_date') or item.get('creation') or '')[:10],
            "category": item.get("category") if not exception else None,
            "client_name": item.get("client_name") or "",
            "client_number": item.get("client_number") or "",
            "loan_number": item.get("loan_number") or "",
            "cause_category": (item.get("cause_category") or "") if exception else "",
            "reason_type": item.get("reason_type") or "",
            "description": item.get("description") or "",
            "responsible": responsible or "", "target_doctype": "CN Reconciliation Exception" if exception else "CN Complementary Item",
            "target_name": item["name"], "action_label": {
                'company_receivable': 'Aplicar cobro / Compensar CxC', 'employee_receivable': 'Aplicar cobro / Compensar CxC', 'credit_management': 'Gestionar saldo',
                'accounting_registration': 'Verificar asiento', 'complementary_balance': 'Revisar partida',
                'adjustment_classification': 'Clasificar ajuste', 'open_exception': 'Gestionar excepción',
            }.get(kind, 'Revisar caso')})
    for item in items:
        balance = balances[item["name"]]
        if item.get("docstatus") == 2 or item.get('origin_deposit'):
            continue
        if item.get('category') == 'Ajuste de conciliación' and item.get('subcategory_effect') in (None, '', 'Por clasificar'):
            add(item, 'adjustment_classification', 'Ajuste de conciliación por clasificar',
                'Seleccione una subcategoría. Distribuir el depósito no determina si el faltante sigue siendo una CxC.', abs(balance['original_usd']))
        if balance["financial_status"] == "Revisar distribución":
            add(item, "complementary_balance", "Partida complementaria · Revisar distribución",
                (balance.get('amount_issue') or "La distribución es inconsistente") +
                ". Compare la evidencia original y los vínculos; el importe observado no es una nueva CxC ni dinero disponible.",
                abs(balance['original_usd']), amount_kind='Importe observado')
        elif balance["pending_usd"]:
            add(item, "complementary_balance", "Partida complementaria · " + balance["financial_status"],
                "Revisar el concepto y distribuir, ajustar o compensar el importe pendiente.", balance["pending_usd"])
        if item.get("category") in CREDIT_CATEGORIES and item.get("docstatus") == 1 and balance["management_pending_usd"]:
            add(item, "credit_management", item["category"] + " por gestionar",
                "Documentar devolución o aplicación externa con referencia y soporte opcional; no vuelve a liberar el depósito original.", balance["management_pending_usd"],
                amount_kind='Pendiente de devolver/aplicar')
        if (not item.get("registration_exception") and not item.get("accounting_exception")
                and balance["accounting_status"] in {"Pendiente de registro", "Asiento informado"}
                and balance["financial_status"] not in {"Cancelada", "Revertida", "No conciliatoria"}):
            add(item, "accounting_registration", "Partida pendiente de registro o verificación en el core",
                "Crear / Ver excepción de registro contable y verificar el asiento contra la importación.", abs(balance["original_usd"]),
                amount_kind='Importe por registrar/verificar')
    for item in adjustment_receivables:
        reviewing = item.get('receivable_usd') is None
        employee = item.get('subcategory_effect') == 'CxC al Empleado'
        add(item, 'employee_receivable' if employee else 'company_receivable',
            'CxC por ajuste · Revisar cobros' if reviewing else ('CxC al empleado' if employee else 'CxC a la empresa') + ' por ajuste pendiente de cobro',
            item.get('observation') if reviewing else 'Gestionar depósito o compensación vinculada. Registrar el asiento en el core no liquida esta deuda.',
            item.get('observed_usd') if reviewing else item['receivable_usd'],
            amount_kind='Importe observado' if reviewing else 'Pendiente de cobrar')
        if reviewing:
            tasks[-1]['action_label'] = 'Revisar cobros vinculados'
        if not all(item.get(field) for field in ('credit_assigned_to', 'credit_commitment_date', 'receivable_due_date')):
            add(item, 'receivable_setup', 'CxC · Seguimiento por completar',
                'Verificar vencimiento financiero, responsable y compromiso de gestión. No cambia ni liquida la deuda.',
                item.get('observed_usd') if reviewing else item.get('receivable_usd'),
                amount_kind='Importe observado' if reviewing else 'CxC pendiente de gestionar')
            tasks[-1]['action_label'] = 'Completar seguimiento de CxC'
            tasks[-1]['priority'] = 1
    for item in exceptions:
        if item.get("status") not in {"Abierta", "En revision"}:
            continue
        legacy = item.get("control_area") == "Legado por revisar"
        from credinomina_reconciliation.exception_policy import blocks_closure
        label = "Bloquea cierre" if blocks_closure(item) else "Seguimiento sin bloqueo de cierre"
        add(item, "open_exception", "Excepción · " + (item.get("exception_type") or item.get("status")) + " · " + label,
            "Supervisor: revisar clasificación del flujo anterior, responsable y fecha compromiso. Conservar evidencia."
            if legacy else item.get("next_action") or "Registrar la gestión, responsable y fecha compromiso.",
            item.get("amount_usd"), exception=True)
    for deposit in identity_deposits:
        if deposit.get('cash_identity_status') != 'Revisar identidad':
            continue
        add(deposit, 'deposit_identity', 'Depósito · Identidad del efectivo por revisar',
            deposit.get('cash_identity_reason') or 'Verifique si existe un solo depósito con varias líneas contables. No confirme ni concilie efectivo duplicado.',
            deposit.get('amount_usd'), amount_kind='Importe observado; no sumar dos veces')
        tasks[-1].update(target_doctype='CN Remittance Allocation', action_label='Revisar origen del depósito',
                         event_date=str(deposit.get('deposit_date') or '')[:10], priority=1)
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
            fields=[*FIELDS, "subcategory_effect", "accounting_exception", "client_name", "client_number", "loan_number", "registered_deposit", "reason_type"], limit_page_length=0)
    if frappe.has_permission("CN Reconciliation Exception", "read"):
        exceptions = frappe.get_list("CN Reconciliation Exception", filters={**scope, "status": ["in", ["Abierta", "En revision"]]},
            fields=["name", "employer", "period", "status", "exception_type", "control_area", "blocks_closure", "amount_usd", "assigned_to", "commitment_date", "next_action", "creation",
                    "client_name", "client_number", "loan_number", "cause_category", "description", "complementary_item"],
            limit_page_length=0)
        origins = {item['name']: item.get('posting_date') for item in items}
        missing = sorted({item.get('complementary_item') for item in exceptions if item.get('complementary_item')} - set(origins))
        if missing and frappe.has_permission('CN Complementary Item', 'read'):
            for offset in range(0, len(missing), 500):
                origins.update({item['name']: item.get('posting_date') for item in frappe.get_list(
                    'CN Complementary Item', filters={'name': ['in', missing[offset:offset + 500]]},
                    fields=['name', 'posting_date'], limit_page_length=0)})
        period_dates = {}
        period_names = sorted({item.get('period') for item in exceptions if item.get('period')})
        if period_names and frappe.has_permission('CN Reconciliation Period', 'read'):
            for offset in range(0, len(period_names), 500):
                period_dates.update({item['name']: item.get('payroll_month') for item in frappe.get_list(
                    'CN Reconciliation Period', filters={'name': ['in', period_names[offset:offset + 500]]},
                    fields=['name', 'payroll_month'], limit_page_length=0)})
        for item in exceptions:
            item['origin_date'] = origins.get(item.get('complementary_item')) or period_dates.get(item.get('period')) or item.get('creation')
        if year:
            exceptions = [item for item in exceptions if str(item.get('origin_date') or '')[:4] == str(year)]
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    receivables = load_receivables(employer=employer, year=year) if frappe.has_permission('CN Complementary Item', 'read') and frappe.has_permission('CN Remittance Allocation', 'read') else []
    identity_deposits = frappe.get_list('CN Remittance Allocation', filters={**scope, 'docstatus': 0,
        'cash_identity_status': 'Revisar identidad',
        **({'deposit_date': ['between', [f'{year}-01-01', f'{year}-12-31']]} if year else {})},
        fields=['name', 'employer', 'amount_usd', 'deposit_date', 'cash_identity_status', 'cash_identity_reason'],
        limit_page_length=0) if frappe.has_permission('CN Remittance Allocation', 'read') else []
    if not items and not exceptions and not receivables and not identity_deposits:
        return []
    from frappe.utils import nowdate
    return build_follow_up(items, load_balances(items), exceptions, as_of=nowdate(), credit_periods=load_credit_periods(items), adjustment_receivables=receivables, identity_deposits=identity_deposits)


def merge_follow_up(existing, additional):
    # Replace the period-only overdue view with the complete actionable case.
    identities = {(item["target_doctype"], item["target_name"]) for item in additional}
    result = [item for item in existing if (item.get("target_doctype"), item.get("target_name")) not in identities]
    return sorted([*result, *additional], key=lambda item: (
        item["priority"], item.get("due_date") or "9999-12-31", item.get("employer_name") or "",
        item.get("period_label") or "", item.get("summary") or ""))


def filter_work(items, kind="", responsible="", due="", as_of=None, search="", min_amount=None, sort_by=""):
    if not (kind or responsible or due or search or min_amount not in (None, '') or sort_by):
        return items
    grouped = any('actions' in item for item in items)
    if grouped:
        items = [action for item in items for action in item.get('actions', [item])]
    today = str(as_of or date.today())[:10]
    groups = {"payments": {"pending_application"}, "credits": {"credit_management"}, "complements": {"complementary_balance", "company_receivable", "employee_receivable", "adjustment_classification", "receivable_setup"},
              "accounting": {"accounting_registration"}, "exceptions": {"open_exception", "overdue_exception", "company_receivable", "employee_receivable", "adjustment_classification", "receivable_setup"},
              "deposits": {"deposit_detail", "review_targets", "review_deposit_detail", "unassigned_deposit", "classify_bank", "pending_application", "deposit_identity"}}
    result = []
    for item in items:
        if kind in groups and item.get("kind") not in groups[kind]:
            continue
        if kind == "periods" and item.get("kind") in set().union(*groups.values()):
            continue
        if responsible and responsible.casefold() not in str(item.get("responsible") or "").casefold():
            continue
        if search and search.strip().casefold() not in ' '.join(str(item.get(field) or '') for field in (
            'client_name', 'client_number', 'loan_number', 'employer_name', 'target_name', 'period_label', 'summary', 'next_action', 'category', 'cause_category', 'reason_type', 'description')).casefold():
            continue
        date_value = item.get("due_date")
        if due == "overdue" and not (date_value and date_value < today):
            continue
        if due == "undated" and date_value:
            continue
        if due == "upcoming" and not (date_value and date_value >= today):
            continue
        result.append(item)
    result = group_work_cases(result) if grouped else result
    if min_amount not in (None, ''):
        minimum = money(min_amount)
        result = [item for item in result if item.get('amount_usd') is not None and abs(money(item['amount_usd'])) >= minimum]
    if sort_by == 'amount':
        result.sort(key=lambda item: (item.get('amount_usd') is None, -abs(money(item.get('amount_usd'))), item.get('target_name') or ''))
    elif sort_by == 'oldest':
        result.sort(key=lambda item: (item.get('event_date') or item.get('due_date') or '9999', item.get('target_name') or ''))
    return result


def group_work_cases(tasks):
    """One case per document/company; never hide independent shared obligations."""
    groups = {}
    for task in tasks:
        key = (task.get('target_doctype'), task.get('target_name'), task.get('employer'))
        if not all(key[:2]):
            key = (*key, task.get('kind'), task.get('employer'), task.get('period'))
        groups.setdefault(key, []).extend(task.get('actions', [task]))
    result = []
    for actions in groups.values():
        actions = sorted(actions, key=lambda item: (item.get('priority', 3), item.get('due_date') or '9999', item.get('kind') or ''))
        first = actions[0]
        # One principal pending amount, not a sum of overlapping financial and
        # accounting tasks. Every secondary amount remains in the expandable detail.
        ranking = {'company_receivable': 0, 'employee_receivable': 0, 'application_cash': 0, 'credit_management': 1, 'complementary_balance': 2,
                   'accounting_registration': 9, 'adjustment_classification': 8}
        primary = min(actions, key=lambda action: (action.get('amount_usd') is None, ranking.get(action.get('kind'), 3)))
        group = dict(first, actions=actions, action_count=len(actions),
            amount_usd=primary.get('amount_usd'), amount_label=primary.get('summary') or '',
            amount_kind=primary.get('amount_kind') or TASK_AMOUNT_LABELS.get(primary.get('kind'), 'Importe del caso'),
            event_date=min((action['event_date'] for action in actions if action.get('event_date')), default=''))
        if len(actions) > 1:
            group['summary'] = first.get('category') or first.get('target_name') or 'Caso pendiente'
            group['next_action'] = 'Revise las acciones del caso; sus importes no se suman.'
        related = sorted({period for task in actions for period in task.get('related_periods') or []})
        if related and not group.get('period'):
            group['period_label'] = ' · '.join(related)
        result.append(group)
    return sorted(result, key=lambda row: (row.get('priority', 3), row.get('due_date') or '9999', row.get('target_name') or ''))
