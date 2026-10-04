"""New private, value-based files per cut. Never reconstruct or overwrite an older cut."""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.file_manager import save_file

from credinomina_reconciliation.control_export import build_control_workbook

READS = ("CN Accounting Import", "CN Remittance Allocation", "CN Complementary Item", "CN Reconciliation Exception")


def save_cut(period):
    if any(not frappe.has_permission(doctype, "read") for doctype in READS):
        frappe.throw(_("El corte detallado requiere acceso a aplicaciones, depósitos, partidas y excepciones."))
    from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import _build_control_data
    from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute
    data = _build_control_data("Todos", detail_period=period.name)
    data["year"] = str(period.payroll_month)[:4]
    aging = execute({"employer": period.employer, "as_of_date": str(period.control_cut_on)[:10]})[1]
    data["aging_rows"] = [row for row in aging if row.get("period") == period.name]
    record = data["periods"][0]
    # Scope is precisely this period. The workbook's empty deposit-month sheets
    # must not imply a full bank-account or company reconciliation.
    manifest = {"schema": 1, "recorded_at": str(period.control_cut_on), "recorded_by": frappe.session.user,
        "period": period.name, "note": period.control_cut_note, "summary": period.control_cut_summary,
        "scope": "Estado guardado de este período al registrar el corte; no reconstrucción retroactiva ni control bancario completo.",
        "period_document": period.as_dict(), "control_data": data}
    raw = json.dumps(manifest, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
    content = build_control_workbook(data, exceptions=record.get("exceptions", []), actions=[],
        employer_label=period.employer, generated_at=period.control_cut_on,
        date_format=frappe.db.get_single_value("System Settings", "date_format") or "yyyy-mm-dd")
    token = frappe.generate_hash(length=12)
    files = {}
    for extension, payload in (("json", raw), ("xlsx", content)):
        saved = save_file(f"corte_control_{token}.{extension}", payload, period.doctype, period.name, is_private=1)
        files[extension] = {"file": saved.name, "url": saved.file_url, "sha256": hashlib.sha256(payload).hexdigest()}
    evidence = {key: manifest[key] for key in ("recorded_at", "recorded_by", "period", "note", "summary", "scope")}
    evidence["files"] = files
    version = frappe.get_doc({"doctype": "Version", "ref_doctype": period.doctype, "docname": period.name,
        "data": json.dumps({"changed": [], "added": [], "removed": [], "row_changed": [], "cn_control_cut": evidence}, ensure_ascii=False)})
    version.insert(ignore_permissions=True)
    return {"cut": version.name, "files": files}


@frappe.whitelist()
def get_cuts(period_name, start=0):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    start = max(cint(start), 0)
    records = frappe.get_all("Version", filters={"ref_doctype": period.doctype, "docname": period.name,
        "data": ["like", '%"cn_control_cut":%']}, fields=["name", "data"],
        order_by="creation desc, name desc", limit_start=start, limit_page_length=21)
    return {"rows": [{"name": row.name, **json.loads(row.data)["cn_control_cut"]} for row in records[:20]],
        "has_more": len(records) > 20, "next_start": start + 20}
