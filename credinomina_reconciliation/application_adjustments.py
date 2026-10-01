"""Confirmed application reductions, distinct from cash and deposit complements."""

from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.reconciliation import net_application_amount as net_amount

CATEGORY = "Ajuste de aplicación"


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
    if money(row.get("historical_remitted_usd")):
        frappe.throw(_("La aplicación tiene depósitos asignados. Revise y retire sus asignaciones antes de ajustarla."))
    links = frappe.parse_json(row.get("application_allocation_detail") or "[]")
    collection_ids = {link.get("collection_row_id") for link in links} | set(saved_collections)
    if row.get("collection_row_id"):
        collection_ids.add(row.collection_row_id)
    targets = frappe.get_all("CN Remittance Target", filters={"docstatus": ["<", 2]},
                            fields=["historical_application", "complementary_item", "period", "row_key"], limit_page_length=0)
    collection_keys = set()
    for name in collection_ids - {None, ""}:
        collection = frappe.db.get_value("CN Collection Row", name, ["parent", "row_key", "remitted_usd"], as_dict=True)
        if collection:
            collection_keys.add((collection.parent, collection.row_key))
            if money(collection.remitted_usd):
                frappe.throw(_("La cobranza vinculada tiene depósitos asignados. Revise sus asignaciones antes de ajustar la aplicación."))
    if any(target.historical_application == row.name
           or (item_name and target.complementary_item == item_name)
           or (target.period, target.row_key) in collection_keys for target in targets):
        frappe.throw(_("La aplicación o partida está seleccionada en destinos de un depósito. Retire esos destinos antes de confirmar o cancelar el ajuste."))


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
