"""Read-only document links and attributable amounts for expanded child rows."""
import frappe
from frappe import _

from credinomina_reconciliation.application_deposit_links import _entries
from credinomina_reconciliation.detail_balances import selected_links
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float, sum_money

SCOPES = {"CN Remittance Allocation": "detail_rows", "CN Accounting Import": "rows"}


class Links:
    def __init__(self):
        self.rows = {}
        self.readable = {}
        self.notes = set()

    def add(self, doctype, document, amount, kind, state, row_name="", label=""):
        key = (doctype, document)
        if key not in self.readable:
            self.readable[key] = bool(document and frappe.has_permission(doctype, "read") and list(
                records(doctype, filters={"name": document}, fields=["name"])))
        if not self.readable[key]:
            self.notes.add("Hay documentos vinculados que no están disponibles para consulta.")
            doctype, document, row_name, label = "", "", "", "Documento no disponible"
        key = (doctype, document, kind, state, row_name)
        entry = self.rows.setdefault(key, dict(doctype=doctype, document=document, kind=kind, state=state,
                                               row_label=label, amount_usd=money(0)))
        entry["amount_usd"] = (None if entry["amount_usd"] is None or amount is None
                               else entry["amount_usd"] + money(amount))

    def claim(self, claim, amount, state, fifo=None):
        parts = _entries(fifo)
        if (parts and all(part.get("application_id") and money(part.get("amount_usd")) > 0 for part in parts)
                and sum_money(part.get("amount_usd") for part in parts) == money(amount)):
            for part in parts:
                self.claim("H:" + str(part.get("application_id") or ""), part.get("amount_usd"), state)
            return
        kind, separator, name = str(claim or "").partition(":")
        if kind == "X" and name:
            self.add("CN Complementary Item", name, amount, "Partida complementaria", state)
            return
        child = {"H": "CN Source Row", "C": "CN Collection Row"}.get(kind)
        parenttype = {"H": "CN Accounting Import", "C": "CN Reconciliation Period"}.get(kind)
        row = frappe.db.get_value(child, name, ["parent", "parenttype", "parentfield", "idx", "source_row"]
            if kind == "H" else ["parent", "parenttype", "parentfield", "idx"], as_dict=True) if child and name else None
        if row and row.parenttype == parenttype and row.parentfield == ("rows" if kind == "H" else "collection_rows"):
            self.add(parenttype, row.parent, amount, "Aplicación" if kind == "H" else "Cobranza", state,
                     name, _("Fila {0}").format(row.get("source_row") or row.idx))
        else:
            self.add("CN Complementary Item", "", amount, "Vínculo por revisar", state)

    def result(self):
        rows = [{**row, "amount_usd": money_float(row["amount_usd"]) if row["amount_usd"] is not None else None}
                for row in self.rows.values()]
        known = all(row["amount_usd"] is not None for row in rows)
        if not known:
            self.notes.add("Hay coberturas compartidas sin un importe atribuible a esta aplicación; consulte el período.")
        return dict(rows=rows, total_usd=money_float(sum_money(row["amount_usd"] for row in rows)) if known else None,
                    notes=sorted(self.notes))


def _deposit(document, row):
    data = Links()
    manual = [target for target in document.targets or [] if target.detail_row == row.name]
    for target in selected_links(manual, row.get("matched_targets")):
        claim = target.get("claim_id")
        if not claim:
            if target.get("historical_application"):
                claim = "H:" + target.historical_application
            elif target.get("complementary_item"):
                claim = "X:" + target.complementary_item
            elif target.get("period") and target.get("row_key"):
                name = frappe.db.get_value("CN Collection Row", {"parent": target.period,
                    "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows", "row_key": target.row_key}, "name")
                claim = "C:" + (name or "")
        state = target.get("result") or ("Conciliada" if row.match_status == "Conciliada" else "Destino identificado")
        data.claim(claim, target.get("amount_usd"), state, target.get("fifo_applications"))
    if document.docstatus == 1:
        from credinomina_reconciliation.client_credit import load_credits, RESULT
        for credit in load_credits([document.name]):
            if credit.get("credit_detail_row") == row.name and credit.get("result") == RESULT:
                data.add("CN Complementary Item", credit.name, credit.amount_usd, credit.category, "Documentado")
    if row.get("row_kind") == "Diferencia documentada" and row.get("difference_core_item") and not data.rows:
        data.add("CN Complementary Item", row.difference_core_item, row.get("difference_amount_usd"),
                 "Diferencia de empresa", row.get("difference_core_status") or "Core seleccionado")
    data.notes.add("Los destinos seleccionados o identificados no prueban una conciliación. Revise el estado de cada vínculo.")
    result = data.result()
    if row.get("row_kind") != "Diferencia documentada":
        from credinomina_reconciliation.remittance_target_summary import read_targets
        expected = sum_money(row.get(field) for field in ("linked_usd", "client_credit_usd", "company_credit_usd"))
        if (read_targets(row.get("matched_targets") or "[]") is None
                or result["total_usd"] is not None and expected > money(result["total_usd"])):
            result["total_usd"] = None
            result["notes"].append("El detalle vinculado no está disponible por completo. Revise los destinos y sus permisos.")
    return result


def _application(row):
    from credinomina_reconciliation.application_deposit_links import load_links as deposits
    from credinomina_reconciliation.application_complementary_links import load_links as items

    data = Links()
    source = row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row)
    for link in deposits([source], include_amounts=True).get(row.name, []):
        data.add("CN Remittance Allocation", link["document"], link["amount_usd"], "Depósito",
                 "Cobertura compartida" if link["via_collection"] else "Conciliado")
    for link in items([source], include_amounts=True).get(row.name, []):
        data.add("CN Complementary Item", link["document"], link["amount_usd"], link["category"], "Confirmado")
    result = data.result()
    if row.get("historical_period") and result["total_usd"] is not None:
        expected = (money(row.get("historical_remitted_usd")) + money(row.get("application_adjustment_usd"))
                    - sum_money(entry.get("diferencia_usd") for entry in _entries(row.get("historical_detail"))))
        if expected > money(result["total_usd"]):
            result["total_usd"] = None
            result["notes"].append("La cobertura guardada incluye importes sin documento disponible o identificable; el total no puede determinarse.")
    return result


def _source(row):
    if row.event_type == "Aplicacion":
        return _application(row)
    data = Links()
    if row.get("remittance_allocation"):
        data.add("CN Remittance Allocation", row.remittance_allocation, row.amount_usd, "Origen contable del depósito", "Vinculado")
    elif row.get("complementary_item"):
        data.add("CN Complementary Item", row.complementary_item, row.amount_usd, "Partida complementaria", "Vinculado")
    else:
        for entry in _entries(row.get("allocation_detail")):
            claim = ("H:" + entry["aplicacion_id"] if entry.get("aplicacion_id") else
                     "X:" + entry["partida"] if entry.get("partida") else "")
            if not claim and entry.get("periodo") and entry.get("fila_id"):
                name = frappe.db.get_value("CN Collection Row", {"parent": entry["periodo"], "row_key": entry["fila_id"],
                    "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows"}, "name")
                claim = "C:" + (name or "")
            data.claim(claim, entry.get("importe_usd"), "Conciliado", entry.get("aplicaciones_fifo"))
    return data.result()


@frappe.whitelist()
def get_links(doctype, name, row_name, modified=None):
    field = SCOPES.get(doctype)
    if not field:
        frappe.throw(_("El documento no admite esta consulta de vínculos."))
    document = frappe.get_doc(doctype, name)
    document.check_permission("read")
    if modified and str(document.modified) != str(modified):
        frappe.throw(_("El documento cambió. Recargue el formulario para consultar sus vínculos."))
    row = next((row for row in document.get(field) or [] if row.name == row_name), None)
    if row is None:
        frappe.throw(_("La fila no pertenece a este documento. Recargue el formulario."))
    result = _deposit(document, row) if field == "detail_rows" else _source(row)
    return dict(result, modified=str(document.modified))
