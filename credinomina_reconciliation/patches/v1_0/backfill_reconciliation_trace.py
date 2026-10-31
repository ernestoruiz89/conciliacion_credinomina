"""Seed attribution from the last editor, without rewriting financial evidence."""
import frappe

from credinomina_reconciliation import reconciliation_trace as trace


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_reconciliation_trace")
    from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report

    for kind, doctype in report.TRANSACTION_DOCTYPES.items():
        records, sources, parents = report.load_transactions(kind, False, {}, ["1900-01-01", "9999-12-31"])
        if kind == "Aplicaciones":
            report._application_progress(records, sources, parents)
        selected = {row["name"] for row in records if row["state"] in {"Conciliado", "Parcial"}}
        wanted = {row["parent"] if kind == "Aplicaciones" else row["name"]
                  for row in sources if row["name"] in selected}
        editors = {}
        names = sorted(wanted)
        for offset in range(0, len(names), 500):
            editors.update({row.name: row for row in frappe.get_all(doctype,
                filters={"name": ["in", names[offset:offset + 500]]},
                fields=["name", "modified_by", "modified"], limit_page_length=0)})
        for row in sources:
            if row["name"] not in selected:
                continue
            source = row["parent"] if kind == "Aplicaciones" else row["name"]
            editor = editors.get(source)
            if not editor:
                continue
            trace.append(doctype, source, trace.APPLICATION if kind == "Aplicaciones" else doctype,
                row["name"], key="initial-last-editor", user=editor.modified_by, timestamp=editor.modified,
                origin=trace.INITIAL, description="Atribución inicial al último editor; no identifica asignaciones históricas.")
