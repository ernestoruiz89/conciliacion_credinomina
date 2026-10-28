"""Additional application reductions retain the original single link and amount."""
from collections import defaultdict
from contextvars import ContextVar
import json

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money

FIELD = "application_adjustment_history"
CATEGORY = "Ajuste de aplicación"
_writer = ContextVar("cn_application_links_writer", default=False)


def additional(doc):
    value = doc.get(FIELD) or "[]"
    rows = json.loads(value) if isinstance(value, str) else value
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("related_application")
            or money(row.get("application_adjustment_usd")) <= 0 for row in rows):
        raise ValueError("Historial de aplicaciones vinculadas inválido; revise la partida.")
    return rows


def links(doc):
    base = [dict(related_application=doc.get("related_application"), related_import=doc.get("related_import"),
                 application_adjustment_usd=doc.get("application_adjustment_usd"), period=doc.get("period"),
                 adjustment_periods=doc.get("adjustment_periods"), adjustment_collection_rows=doc.get("adjustment_collection_rows"))]
    return [frappe._dict(row) for row in base + additional(doc) if row.get("related_application")]


def total(doc):
    return sum_money(row.application_adjustment_usd for row in links(doc))


def guard(doc, previous=None):
    if _writer.get():
        return
    if additional(doc) != additional(previous or {}):
        frappe.throw(_("Use Distribuir entre aplicaciones para agregar vínculos. El historial no se edita directamente."))
    if additional(doc) and (doc.category != CATEGORY or doc.review_action != CATEGORY):
        frappe.throw(_("La partida tiene ajustes distribuidos entre aplicaciones; conserve su tratamiento."))


@frappe.whitelist()
def get_detail(item_name):
    """Read the full distribution, including the original single application."""
    doc = frappe.get_doc("CN Complementary Item", item_name)
    doc.check_permission("read")
    entries = links(doc)
    names = sorted({entry.related_application for entry in entries})
    sources, readable = {}, set()
    for offset in range(0, len(names), 500):
        sources.update({row.name: row for row in frappe.get_all("CN Source Row", filters={"name": ["in", names[offset:offset + 500]],
            "parenttype": "CN Accounting Import"}, fields=["name", "parent", "client_name", "loan_number", "event_date"], limit_page_length=0)})
    parents = sorted({row.parent for row in sources.values()})
    for offset in range(0, len(parents), 500):
        readable.update(frappe.get_list("CN Accounting Import", filters={"name": ["in", parents[offset:offset + 500]]},
                                       pluck="name", limit_page_length=0))
    if set(sources) != set(names) or readable != set(parents):
        frappe.throw(_("No está disponible toda la evidencia de las aplicaciones vinculadas o no tiene permiso para consultarla."), frappe.PermissionError)
    state = "Confirmado" if doc.docstatus == 1 else "Cancelado" if doc.docstatus == 2 else "Pendiente de confirmar"
    return [dict(entry, related_import=sources[entry.related_application].parent,
        client_name=sources[entry.related_application].client_name, loan_number=sources[entry.related_application].loan_number,
        event_date=str(sources[entry.related_application].event_date or ""), state=state) for entry in entries]


def confirmed(names, exclude="", lock=False, employer=None):
    names = sorted(set(names) - {None, ""})
    if not names:
        return []
    fields = ["name", "related_application", "application_adjustment_usd", FIELD, "period", "adjustment_periods", "adjustment_collection_rows"]
    patterns = ["%" + json.dumps(name, ensure_ascii=False).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for name in names] if len(names) <= 100 else []
    if lock:
        history_condition = " or ".join(f"{FIELD} like %s" for _ in patterns) if patterns else f"coalesce({FIELD}, '') not in ('', '[]')"
        parents = frappe.db.sql(f"""select {', '.join(fields)} from `tabCN Complementary Item`
            where docstatus=1 and category=%s and name!=%s and
            (related_application in %s or ({history_condition})) {"and employer=%s" if employer else ""} for update""",
            (CATEGORY, exclude, tuple(names), *patterns, *([employer] if employer else [])), as_dict=True)
    else:
        parents = frappe.get_all("CN Complementary Item", filters={"category": CATEGORY, "docstatus": 1, "name": ["!=", exclude],
            **({"employer": employer} if employer else {})},
            or_filters=[["related_application", "in", names], *([[FIELD, "like", pattern] for pattern in patterns] if patterns
                else [[FIELD, "not in", ["", "[]"]]])], fields=fields, limit_page_length=0)
    wanted = set(names)
    result = [row for parent in parents for row in links(parent) if row.related_application in wanted]
    # Global core adjustments are scoped by each destination's company, not
    # by the originating item's company. The diary is server-controlled.
    from credinomina_reconciliation.reclassification_distribution import FIELD as RECLASS_FIELD, application_links
    if lock:
        condition = " or ".join(f"{RECLASS_FIELD} like %s" for _ in patterns) if patterns else "1=1"
        global_items = frappe.db.sql(f"""select name, {RECLASS_FIELD} from `tabCN Complementary Item`
            where docstatus=1 and name!=%s and coalesce({RECLASS_FIELD}, '') not in ('', '[]')
            and ({condition}) for update""", (exclude, *patterns), as_dict=True)
    else:
        global_items = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "name": ["!=", exclude],
            RECLASS_FIELD: ["not in", ["", "[]"]]},
            or_filters=[[RECLASS_FIELD, "like", pattern] for pattern in patterns] if patterns else None,
            fields=["name", RECLASS_FIELD], limit_page_length=0)
    result.extend(application_links(global_items, wanted, employer=employer))
    return result


def linked_names(names):
    wanted = set(names) - {None, ""}
    if not wanted:
        return set()
    result = set(frappe.get_all("CN Complementary Item", filters={"related_application": ["in", sorted(wanted)]},
                                pluck="related_application", limit_page_length=0))
    for item in frappe.get_all("CN Complementary Item", filters={FIELD: ["not in", ["", "[]"]]}, fields=[FIELD], limit_page_length=0):
        result.update(row["related_application"] for row in additional(item) if row["related_application"] in wanted)
    from credinomina_reconciliation.reclassification_distribution import FIELD as RECLASS_FIELD, application_links
    global_items = frappe.get_all("CN Complementary Item", filters={RECLASS_FIELD: ["not in", ["", "[]"]]},
                                  fields=["name", RECLASS_FIELD], limit_page_length=0)
    result.update(row.related_application for row in application_links(global_items, wanted))
    return result


def validate_additional(doc):
    """Called during both draft save and submit, using current locked evidence."""
    rows = additional(doc)
    if not rows:
        return
    from credinomina_reconciliation import application_adjustments as adjustments
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _source_linked_periods
    if total(doc) > abs(money(doc.amount_usd)):
        frappe.throw(_("La suma distribuida supera el importe US$ de la partida."))
    grouped = defaultdict(lambda: money(0))
    for entry in links(doc):
        grouped[entry.related_application] += money(entry.application_adjustment_usd)
    # Stable locking order also applies to the legacy first application.
    frappe.db.sql("select name from `tabCN Source Row` where name in %s order by name for update", (tuple(sorted(grouped)),))
    others = defaultdict(lambda: money(0))
    for entry in confirmed(grouped, exclude=doc.name or "", lock=True, employer=doc.employer):
        others[entry.related_application] += money(entry.application_adjustment_usd)
    snapshots = dict(doc.flags.get("adjustment_cash_snapshot") or {})
    for name, amount in sorted(grouped.items()):
        source = frappe.get_doc("CN Source Row", name)
        parent = frappe.get_doc("CN Accounting Import", source.parent)
        parent.check_permission("read")
        reason = adjustments.application_link_error(doc, source, parent)
        if reason:
            frappe.throw(reason)
        if amount <= 0 or amount + others[name] > money(source.amount):
            frappe.throw(_("Los ajustes superan el importe original de la aplicación {0}.").format(name))
        if doc.docstatus == 1:
            adjustments.assert_adjustable(source, doc.name)
            # Current row summaries can lag another committed adjustment.
            current = frappe._dict(source.as_dict())
            current.application_adjustment_usd = money_float(others[name])
            coverage = adjustments.cash_coverage(current, lock=True)
            if amount > money(source.amount) - others[name] - money(coverage["protected_usd"]):
                frappe.throw(_("El ajuste supera el saldo disponible de la aplicación {0}. Se conservan sus depósitos y reservas.").format(name))
            snapshots.update(coverage["snapshots"])
        for entry in rows:
            if entry["related_application"] == name:
                entry.update(related_import=parent.name, period=source.historical_period or source.collection_period or "",
                    adjustment_periods=frappe.as_json(_source_linked_periods(source)),
                    adjustment_collection_rows=frappe.as_json(sorted({link.get("collection_row_id") for link in
                        frappe.parse_json(source.get("application_allocation_detail") or "[]")} - {None, ""})))
    doc.flags.adjustment_cash_snapshot = snapshots
    # Scope metadata is frozen together with the newly selected links only.
    if _writer.get() or doc.docstatus == 0:
        doc.set(FIELD, frappe.as_json(rows))


@frappe.whitelist(methods=["POST"])
def distribute(item_name, rows, modified):
    """Append reviewed links atomically, preserving submitted links and cash."""
    from frappe.utils import now_datetime
    from credinomina_reconciliation import application_adjustments as adjustments
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    owner = frappe.db.get_value("CN Complementary Item", item_name, "employer")
    lock_cash_pool([owner] if owner else [])
    doc = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    doc.check_permission("write")
    if doc.docstatus == 1:
        doc.check_permission("submit")
    if str(doc.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de distribuir el remanente."))
    if doc.docstatus not in (0, 1) or (doc.docstatus == 1 and doc.category != CATEGORY) or not doc.employer:
        frappe.throw(_("Seleccione un borrador o un ajuste confirmado de una empresa identificada."))
    if doc.docstatus == 1 and not doc.related_application:
        frappe.throw(_("El ajuste confirmado no tiene aplicación original; revise su evidencia antes de distribuir."))
    if doc.category in {"Saldo a favor del cliente", "Saldo a favor de la empresa", "Compensación entre partidas", "Diferencia por tolerancia"}:
        frappe.throw(_("La categoría seleccionada no admite ajustes de aplicaciones."))
    if doc.get("origin_deposit") or doc.get("registration_exception") or doc.get("registered_deposit") or doc.get("compensations"):
        frappe.throw(_("La partida ya tiene otro vínculo financiero; revise su tratamiento."))
    selected = frappe.parse_json(rows) if isinstance(rows, str) else rows
    if not isinstance(selected, list) or not 1 <= len(selected) <= 200 or any(not isinstance(row, dict) for row in selected):
        frappe.throw(_("Seleccione entre 1 y 200 aplicaciones con su importe."))
    seen, additions = set(), []
    for row in selected:
        name = str(row.get("application") or "").strip()
        amount = money(row.get("amount_usd"))
        if not name or name in seen or amount <= 0:
            frappe.throw(_("Las aplicaciones deben ser distintas y sus importes positivos."))
        seen.add(name)
        source = frappe.get_doc("CN Source Row", name)
        parent = frappe.get_doc("CN Accounting Import", source.parent)
        parent.check_permission("read")
        reason = adjustments.application_link_error(doc, source, parent)
        if reason:
            frappe.throw(reason)
        additions.append(dict(related_application=name, related_import=parent.name, application_adjustment_usd=money_float(amount),
            client_name=source.get("client_name"), loan_number=source.get("loan_number"),
            recorded_by=frappe.session.user, recorded_on=str(now_datetime())))
    if (total(doc) if doc.docstatus == 1 else money(0)) + sum_money(row["application_adjustment_usd"] for row in additions) > abs(money(doc.amount_usd)):
        frappe.throw(_("La suma distribuida supera el remanente US$ de la partida."))
    history = list(additional(doc)) if doc.docstatus == 1 else []
    if len(history) + len(additions) > 2000:
        frappe.throw(_("La partida supera el límite de 2,000 vínculos adicionales."))
    token = _writer.set(True)
    try:
        if doc.docstatus == 0:
            first = additions.pop(0)
            doc.related_application = first["related_application"]
            doc.related_import = first["related_import"]
            doc.application_adjustment_usd = first["application_adjustment_usd"]
        history.extend(additions)
        doc.set(FIELD, frappe.as_json(history))
        doc.category = doc.review_action = CATEGORY
        if doc.docstatus == 0:
            doc.save()
        else:
            adjustments.validate_adjustment(doc)
            validate_additional(doc)
            doc.db_set(FIELD, doc.get(FIELD))
            adjustments.reconcile_adjustment(doc)
        doc.add_comment("Comment", _("Distribución adicional entre aplicaciones: {0}.").format(
            "; ".join(f"{row['application']}: US$ {money(row['amount_usd']):.2f}" for row in selected)))
        return {"name": doc.name, "used_usd": money_float(total(doc)), "pending_usd": money_float(abs(money(doc.amount_usd)) - total(doc))}
    finally:
        _writer.reset(token)
