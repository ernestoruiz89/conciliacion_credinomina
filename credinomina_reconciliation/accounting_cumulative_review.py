"""Owner-scoped, durable absence report; no accounting mutations."""
import csv
import io
import json

import frappe

from credinomina_reconciliation.accounting_batch_store import BLOCK, dumps, insert_internal


def save_missing(token, rows):
    count = 0
    for offset in range(0, len(rows), 250):
        count += 1
        name = f"{token}-missing-{count}"
        payload = dumps(rows[offset:offset + 250])
        saved = frappe.db.get_value(BLOCK, name, "payload")
        if saved:
            if saved != payload:
                frappe.throw("La comparación cambió. Genere un nuevo análisis.")
        else:
            insert_internal(BLOCK, name, batch=token, block_index=-100000 - count, payload=payload)
    return count


@frappe.whitelist()
def download_missing_origins(token):
    from credinomina_reconciliation.bulk_accounting_import import _state
    state = _state(token)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    columns = [("doctype", "Tipo"), ("document", "Documento"), ("row", "Fila original"),
               ("event_date", "Fecha"), ("employer", "Empresa"), ("voucher", "Asiento"),
               ("account", "Cuenta"), ("currency", "Moneda"), ("debit", "Débito"),
               ("credit", "Crédito"), ("client_name", "Cliente"), ("loan_number", "Crédito del cliente"),
               ("description", "Descripción"), ("reason", "Revisión pendiente")]
    writer.writerow([label for _, label in columns])
    checked = set()
    for index in range(1, state.get("missing_chunks", 0) + 1):
        payload = frappe.db.get_value(BLOCK, f"{token}-missing-{index}", "payload")
        if not payload:
            frappe.throw("No se encontró la comparación guardada. Genere un nuevo análisis.")
        for row in json.loads(payload):
            ref = row["doctype"], row["document"]
            if ref not in checked:
                frappe.get_doc(*ref).check_permission("read")
                checked.add(ref)
            values = [str(row.get(key) if row.get(key) is not None else "") for key, _ in columns]
            writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
    frappe.local.response.update(type="download", filename="movimientos_ausentes_del_acumulado.csv",
                                 filecontent=stream.getvalue().encode("utf-8-sig"))
