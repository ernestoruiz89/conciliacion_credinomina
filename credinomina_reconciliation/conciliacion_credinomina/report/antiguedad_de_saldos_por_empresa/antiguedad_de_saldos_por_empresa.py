"""Aggregate the existing permission-filtered aging report without client detail."""

from collections import defaultdict

from frappe import _

from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as detailed
from credinomina_reconciliation.rounding import money_float, sum_money


def execute(filters=None):
    result = detailed.execute(filters)
    source_columns, source_rows = result[:2]
    columns = [dict(column) for column in source_columns
               if column["fieldname"] in {"employer", "balance_type", "cutoff_date", "cutoff_warning"} or column["fieldtype"] == "Currency"]
    money_fields = [column["fieldname"] for column in columns if column["fieldtype"] == "Currency"]
    applications = "applied_usd" in money_fields
    if applications:
        columns.append({"fieldname": "missing_fx_count", "label": _("Aplicaciones sin conversión US$"),
                        "fieldtype": "Int", "width": 170})
    columns.append({"fieldname": "observation", "label": _("Observación"), "fieldtype": "Data", "width": 320})
    groups = defaultdict(list)
    for row in source_rows:
        # Never net different balance types or re-age totals with one due date.
        groups[(row.get("employer") or "", row.get("balance_type") or "")].append(row)
    data = []
    for (employer, balance_type), rows in sorted(groups.items()):
        item = {"employer": employer or None, "usd_currency": "USD"}
        if rows[0].get('cutoff_date'):
            item.update(cutoff_date=rows[0]['cutoff_date'], cutoff_warning=rows[0].get('cutoff_warning') or '')
        if any(column["fieldname"] == "balance_type" for column in columns):
            item["balance_type"] = balance_type
        for field in money_fields:
            values = [row[field] for row in rows if row.get(field) is not None]
            item[field] = money_float(sum_money(values)) if values else None
        notes = []
        if not employer:
            notes.append(_("Empresa pendiente de identificar"))
        if applications:
            item["missing_fx_count"] = sum(row.get("amount_usd") is None for row in rows)
            if item["missing_fx_count"]:
                notes.append(_("Totales incompletos: hay aplicaciones sin conversión a US$."))
        item["observation"] = " ".join(notes)
        data.append(item)
    message = " ".join(filter(None, [result[2] if len(result) > 2 else None,
        _("Resumen por empresa de los mismos saldos y rangos del reporte detallado. "
          "Las aplicaciones sin conversión no se incluyen en los importes; no equivalen a saldo cero.")]))
    summary = result[4] if len(result) > 4 else []
    return columns, data, message, None, summary
