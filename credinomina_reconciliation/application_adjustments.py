"""Confirmed application reductions, distinct from cash and deposit complements."""

from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.reconciliation import net_application_amount as net_amount

CATEGORY = "Ajuste de aplicación"
MIXED_STATUS = "Conciliada: depósito + ajuste"


def mark_mixed_settlements(rows, collections):
    by_name = {row.name: row for row in collections}
    for row in rows:
        if (row.get("event_type") != "Aplicacion" or not row.get("effective")
                or row.get("match_status") != "Conciliado" or not money(row.get("application_adjustment_usd"))
                or not money(net_amount(row))):
            continue
        if row.get("historical_period"):
            complete = money(row.get("historical_remitted_usd")) > 0 and money(row.get("historical_balance_usd")) == 0
        else:
            links = frappe.parse_json(row.get("application_allocation_detail") or "[]")
            targets = [by_name.get(link.get("collection_row_id")) for link in links]
            complete = bool(targets)
            for target in targets:
                if not target:
                    complete = False
                    break
                cash = sum_money(entry.get("importe_usd") for entry in
                    frappe.parse_json(target.get("remittance_detail") or "[]")
                    if entry.get("destino") != "Partida complementaria")
                if cash <= 0 or cash + max(-money(target.get("rounding_adjustment_usd")), 0) < money(target.get("applied_usd")):
                    complete = False
        if complete:
            row.deposit_match_status = MIXED_STATUS
            row.deposit_match_reason = _("Aplicación original {0} US$; ajuste confirmado {1} US$; neto {2} US$ cubierto con depósitos. El ajuste no es efectivo recibido.").format(
                money_float(row.get("amount")), money_float(row.get("application_adjustment_usd")), net_amount(row))


def refresh_rows(rows):
    names = [row.name for row in rows if row.event_type == "Aplicacion" and row.name]
    totals = defaultdict(lambda: money(0))
    if names:
        for item in frappe.db.sql("""select related_application, application_adjustment_usd
            from `tabCN Complementary Item` where docstatus=1 and category=%s
            and related_application in %s for update""", (CATEGORY, tuple(names)), as_dict=True):
            totals[item.related_application] += money(item.application_adjustment_usd)
    for row in rows:
        row.application_adjustment_usd = money_float(totals[row.name]) if row.event_type == "Aplicacion" else 0
        row.net_applied_usd = net_amount(row) if row.event_type == "Aplicacion" else 0
        row.application_adjustment_status = (
            "Aplicación compensada totalmente" if row.application_adjustment_usd and row.net_applied_usd == 0
            else "Aplicación ajustada parcialmente" if row.application_adjustment_usd else "Sin ajuste"
        ) if row.event_type == "Aplicacion" else ""


def cash_coverage(row, saved_collections=(), lock=False):
    """Reserve cash once per deposit/claim, including unconfirmed manual targets.

    Operational cash belongs to a collection shared by applications. Only its
    uncovered capacity is adjustable; never attribute that cash to two rows.
    """
    links = frappe.parse_json(row.get("application_allocation_detail") or "[]")
    ids = {link.get("collection_row_id") for link in links} | set(saved_collections)
    if row.get("collection_row_id"):
        ids.add(row.collection_row_id)
    collections = {name: frappe.db.get_value("CN Collection Row", name,
        ["name", "parent", "row_key", "applied_usd"], as_dict=True, for_update=lock) for name in sorted(ids - {None, ""})}
    collections = {name: value for name, value in collections.items() if value}
    keys = {(value.parent, value.row_key): name for name, value in collections.items()}
    wanted = {("H", row.name)} | {("C", name) for name in collections}
    paid = defaultdict(lambda: money(0))
    planned = defaultdict(lambda: money(0))
    queries = [{"historical_application": row.name}] + [
        {"period": period, "row_key": key} for period, key in keys]
    for filters in queries:
        for target in frappe.get_all("CN Remittance Target", filters={**filters, "docstatus": ["<", 2]},
                fields=["parent", "historical_application", "period", "row_key", "amount_usd"], limit_page_length=0):
            claim = ("H", row.name) if target.historical_application == row.name else ("C", keys.get((target.period, target.row_key)))
            if claim in wanted:
                planned[(target.parent, claim)] += money(target.amount_usd)
    snapshots = {}
    search = [row.name] + sorted({value.parent for value in collections.values()})
    deposits = frappe.get_all("CN Remittance Allocation", filters={"docstatus": 1},
        or_filters=[["allocation_detail", "like", "%" + name + "%"] for name in search],
        fields=["name", "allocation_detail"], limit_page_length=0)
    for deposit in deposits:
        if lock:
            deposit.allocation_detail = frappe.db.get_value("CN Remittance Allocation", deposit.name,
                                                            "allocation_detail", for_update=True)
        entries = frappe.parse_json(deposit.allocation_detail or "[]")
        related = False
        for entry in entries:
            claim = (("H", entry.get("aplicacion_id")) if entry.get("tipo") == "Aplicacion historica"
                     else ("C", keys.get((entry.get("periodo"), entry.get("fila_id")))) if entry.get("tipo") == "Cobranza"
                     else None)
            if claim in wanted:
                paid[(deposit.name, claim)] += money(entry.get("importe_usd"))
                related = True
        if related:
            snapshots[deposit.name] = allocation_signature(entries)
    reserved = defaultdict(lambda: money(0))
    for deposit_claim in paid.keys() | planned.keys():
        reserved[deposit_claim[1]] += max(paid[deposit_claim], planned[deposit_claim])
    historical = max(reserved[("H", row.name)], money(row.get("historical_remitted_usd")))
    if not collections:
        available = max(money(net_amount(row)) - historical, money(0))
    else:
        link_amounts = defaultdict(lambda: money(0))
        for link in links:
            link_amounts[link.get("collection_row_id")] += money(link.get("amount_usd"))
        if not links and row.get("collection_row_id"):
            link_amounts[row.collection_row_id] = money(net_amount(row))
        unlinked = max(money(net_amount(row)) - sum_money(link_amounts.values()), money(0))
        available = unlinked + sum_money(
            min(link_amounts[name], max(money(collection.applied_usd) - reserved[("C", name)], money(0)))
            for name, collection in collections.items())
        available = min(available, max(money(net_amount(row)) - historical, money(0)))
    return {"adjustable_usd": money_float(available), "protected_usd": money_float(money(net_amount(row)) - available),
            "snapshots": snapshots}


def allocation_signature(entries):
    """Compare amounts/destinations, not JSON order or descriptive comments."""
    totals = defaultdict(lambda: money(0))
    for entry in entries:
        key = tuple(str(entry.get(field) or "") for field in
                    ("tipo", "periodo", "fila_id", "aplicacion_id", "partida", "movimiento", "empresa"))
        totals[key] += money(entry.get("importe_usd"))
    return dict(totals)


def assert_cash_preserved(snapshots):
    for name, expected in snapshots.items():
        current = frappe.db.get_value("CN Remittance Allocation", name, "allocation_detail")
        if allocation_signature(frappe.parse_json(current or "[]")) != expected:
            frappe.throw(_("El ajuste cambiaría la distribución existente del depósito {0}. No se guardó el cambio; revise los vínculos de la aplicación.").format(name))


def assert_adjustable(row, item_name=None, saved_periods=(), saved_collections=()):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _source_linked_periods

    periods = set(_source_linked_periods(row)) | set(saved_periods)
    for name in saved_collections:
        parent = frappe.db.get_value("CN Collection Row", name, "parent")
        if parent:
            periods.add(parent)
    for period in periods:
        if frappe.db.get_value("CN Reconciliation Period", period, "status") == "Cerrado":
            frappe.throw(_("Reabra el período {0} antes de confirmar o cancelar el ajuste.").format(period))
    if item_name and frappe.db.exists("CN Remittance Target", {"complementary_item": item_name, "docstatus": ["<", 2]}):
        frappe.throw(_("Una partida de ajuste no puede ser también destino de un depósito. Retire ese destino antes de confirmar o cancelar el ajuste."))


def validate_adjustment(doc):
    if doc.docstatus == 0 and (not doc.related_application or not doc.employer or not doc.application_adjustment_usd):
        doc.related_import = "" if not doc.related_application else doc.related_import
        doc.review_status = "Ajuste pendiente de completar"
        return
    if not doc.related_application or not doc.employer:
        frappe.throw(_("Seleccione empresa y use Vincular a aplicación."))
    # Serialize confirmations against the same application. Current reads below
    # see prior confirmations even under MariaDB repeatable-read transactions.
    frappe.db.sql("select name from `tabCN Source Row` where name=%s for update", doc.related_application)
    row = frappe.get_doc("CN Source Row", doc.related_application)
    parent = frappe.get_doc("CN Accounting Import", row.parent)
    parent.check_permission("read")
    if (row.parenttype != "CN Accounting Import" or row.event_type != "Aplicacion"
            or not row.effective or row.currency != "USD" or parent.employer != doc.employer
            or parent.status not in {"Importado", "Importado con excepciones"}):
        frappe.throw(_("Seleccione una aplicación vigente en US$ de la misma empresa."))
    known_periods = {row.historical_period, row.collection_period} | set(frappe.parse_json(doc.get("adjustment_periods") or "[]"))
    if doc.period and doc.period not in known_periods:
        frappe.throw(_("El período de la partida no corresponde a la aplicación seleccionada."))
    doc.related_import = parent.name
    amount = money(doc.application_adjustment_usd)
    if amount <= 0 or amount > abs(money(doc.amount_usd)):
        frappe.throw(_("El importe del ajuste debe ser positivo y no superar el importe US$ de la partida."))
    others = frappe.db.sql("""select application_adjustment_usd from `tabCN Complementary Item`
        where related_application=%s and category=%s and docstatus=1 and name!=%s for update""",
        (row.name, CATEGORY, doc.name or ""), as_dict=True)
    if sum_money(item.application_adjustment_usd for item in others) + amount > money(row.amount):
        frappe.throw(_("Los ajustes confirmados más este ajuste superan el importe original de la aplicación."))
    if doc.docstatus == 1 and not (doc.review_notes or "").strip():
        frappe.throw(_("Documente el motivo del ajuste en Observaciones de la revisión."))
    if doc.docstatus == 1:
        previous = doc.get_doc_before_save()
        if not previous or previous.docstatus != 1:
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _source_linked_periods
            doc.adjustment_periods = frappe.as_json(_source_linked_periods(row))
            links = frappe.parse_json(row.get("application_allocation_detail") or "[]")
            names = {link.get("collection_row_id") for link in links}
            if row.collection_row_id:
                names.add(row.collection_row_id)
            doc.adjustment_collection_rows = frappe.as_json(sorted(names - {None, ""}))
            if not doc.period:
                doc.period = row.historical_period or row.collection_period
        assert_adjustable(row, doc.name, frappe.parse_json(doc.get("adjustment_periods") or "[]"),
                          frappe.parse_json(doc.get("adjustment_collection_rows") or "[]"))
        coverage = cash_coverage(row, lock=True)
        remaining = max(money(row.amount) - sum_money(item.application_adjustment_usd for item in others)
                        - money(coverage["protected_usd"]), money(0))
        if amount > remaining:
            frappe.throw(_("El ajuste supera el saldo disponible de {0} US$. Los depósitos asignados o reservados se conservan.").format(money_float(remaining)))
        doc.flags.adjustment_cash_snapshot = coverage["snapshots"]
    doc.application_adjustment_usd = money_float(amount)
    doc.review_status = "Ajuste confirmado" if doc.docstatus == 1 else "Ajuste pendiente de confirmar"


def guard_source_changes(doc):
    previous = doc.get_doc_before_save()
    if not previous:
        return
    current = {row.name: row for row in doc.rows or []}
    names = [row.name for row in previous.rows or []]
    linked = set(frappe.get_all("CN Complementary Item", filters={"related_application": ["in", names]},
                                pluck="related_application", limit_page_length=0)) if names else set()
    for old in previous.rows or []:
        if old.name not in linked:
            continue
        row = current.get(old.name)
        fields = ("event_type", "currency", "source_key", "event_date", "client_number", "loan_number")
        if (not row or previous.employer != doc.employer
                or money(old.amount) != money(row.amount)
                or any(str(old.get(field) or "") != str(row.get(field) or "") for field in fields)):
            frappe.throw(_("La aplicación {0} tiene partidas vinculadas. No se puede borrar ni cambiar su identidad o importe original.").format(old.name))


@frappe.whitelist(methods=["POST"])
def confirm_adjustment(item_name):
    doc = frappe.get_doc("CN Complementary Item", item_name)
    doc.check_permission("submit")
    if doc.docstatus != 0 or doc.review_action != CATEGORY:
        frappe.throw(_("Guarde una partida en borrador con tratamiento Ajuste de aplicación."))
    doc.submit()
    return {"name": doc.name, "application": doc.related_application, "status": doc.review_status}
