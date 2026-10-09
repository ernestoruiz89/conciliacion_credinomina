"""Explicit reuse of selected payroll collection rows as editable deposit detail."""
import hashlib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.application_deposit_detail import _load, _workbook, select_application_rows
from credinomina_reconciliation.collection_identity import collection_client_number
from credinomina_reconciliation.paying_employers import allowed_employers
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money


def collection_detail_available(employer, periods):
    """Availability for unsaved selections; reveal only readable, authorized periods."""
    if not employer or not frappe.has_permission("CN Remittance Allocation", "write"):
        return False
    names = frappe.parse_json(periods) if isinstance(periods, str) else periods
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        frappe.throw(_("Seleccione períodos válidos."))
    if not names:
        return False
    frappe.get_doc("CN Employer", employer).check_permission("read")
    readable = frappe.get_list("CN Reconciliation Period", filters={
        "name": ["in", names], "employer": ["in", sorted(allowed_employers(employer))],
        "status": ["!=", "Cerrado"],
    }, pluck="name", limit_page_length=0)
    return bool(readable and frappe.get_all("CN Collection Row", filters={
        "parent": ["in", readable], "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows",
    }, pluck="name", limit_page_length=1))


def collection_rows(document, periods):
    rows = []
    for period in periods:
        for source in period.collection_rows or []:
            if not source.row_key:
                frappe.throw(_("La fila {0} de {1} no tiene Fila ID; revise la cobranza cargada.").format(source.idx, period.name))
            amount = money(source.expected_usd)
            if amount <= 0 and money(source.expected_nio) > 0:
                rate = decimal_value(document.get("fx_rate"))
                if rate <= 0:
                    frappe.throw(_("La cobranza de {0} está en C$. Indique la tasa C$/US$ del depósito para generar el detalle.").format(period.name))
                amount = money(decimal_value(source.expected_nio) / rate)
            if amount <= 0:
                continue
            if not (source.client_name or "").strip():
                frappe.throw(_("Complete el nombre del cliente en la cobranza de {0}.").format(period.name))
            rows.append({field: source.get(field) for field in (
                "client_name", "employee_number", "national_id", "loan_number", "installment_number", "row_key",
            )} | {
                "client_number": collection_client_number(source), "employer": period.employer,
                "claim_id": "C:" + source.name, "period": period.name,
                "deducted_usd": money_float(amount), "deducted_nio": 0, "source_row": len(rows) + 2,
                "application_reference": "", "application_comment": f"Cobranza {period.name} / {source.name}",
                "comments": _("Generado desde la cobranza cargada de {0}; no confirma una deducción informada por la empresa.").format(period.name),
            })
    return rows


def _preview(document, periods):
    rows = collection_rows(document, periods)
    result = {"periods": [period.name for period in periods], "rows": rows,
        "collection_usd": money_float(sum_money(row["deducted_usd"] for row in rows)),
        "total_usd": money_float(sum_money(row["deducted_usd"] for row in rows)),
        "deposit_usd": money_float(document.amount_usd), "modified": str(document.modified),
        "replaces_detail": bool(document.detail_rows or document.detail_file)}
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def preview_collection_detail(remittance_name):
    return _preview(*_load(remittance_name))


def use_collection_detail(remittance_name, fingerprint, replace_detail=False, selected_claim_ids=None, selected_amounts=None):
    from frappe.utils.file_manager import save_file
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail

    frappe.db.sql("select name from `tabCN Remittance Allocation` where name=%s for update", (remittance_name,))
    document, periods = _load(remittance_name)
    preview = _preview(document, periods)
    if fingerprint != preview["fingerprint"]:
        frappe.throw(_("Los datos cambiaron. Abra nuevamente la vista previa antes de generar el detalle."))
    if not preview["rows"]:
        frappe.throw(_("No hay filas de cobranza con importe positivo en los períodos seleccionados."))
    if preview["replaces_detail"] and not cint(replace_detail):
        frappe.throw(_("Confirme el reemplazo del detalle existente."))
    rows = select_application_rows(preview["rows"], selected_claim_ids, selected_amounts, original_label="cobranza original")
    total = money_float(sum_money(row["deducted_usd"] for row in rows))
    content = _workbook(rows, amount_description=(
        "Importe del detalle inicializado desde la cobranza cargada y editable por el usuario. "
        "No modifica la cobranza ni confirma una deducción informada por la empresa."
    ))
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", document.name)
    attachment = save_file(f"detalle_cobranza_{safe_name}.xlsx", content,
        document.doctype, document.name, is_private=1, df="detail_file")
    document.detail_file = attachment.file_url
    document.detail_total_usd = total
    document.result = "Pendiente"
    _apply_remittance_detail(document, rows, content, attachment.file_url, origin="Cobranza de los períodos")
    document.add_comment("Comment", _(
        "Se generó detalle desde la cobranza de {0}: {1} filas seleccionadas de {2}, US$ {3}. "
        "Los cambios de importe constan en cada fila. Se conservaron la cobranza, los destinos y los archivos anteriores; "
        "se retiraron los vínculos al detalle reemplazado. No se confirmó ni concilió el depósito."
    ).format(", ".join(period.name for period in periods), len(rows), len(preview["rows"]), total))
    return {"rows": len(rows), "total_usd": total, "file_url": attachment.file_url}
