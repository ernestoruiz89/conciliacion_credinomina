"""Read-only monthly counts, using cash-reconciliation states, not collection matching."""
from collections import defaultdict
from datetime import date

from credinomina_reconciliation.rounding import money


IMPORTED = {"Importado", "Importado con excepciones"}
SETTLED_APPLICATION = {"Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"}
SETTLED_DEPOSIT = {"Conciliado", "Conciliado con saldo a favor del cliente", "Conciliado con saldo a favor",
                   "Saldo a favor documentado", "Parcial con saldo a favor"}


def application_state(row, parent):
    if parent.get("status") not in IMPORTED or not row.get("effective") or row.get("match_status") == "Ignorado":
        return "Pendiente"
    status = row.get("deposit_match_status")
    if status in SETTLED_APPLICATION:
        return "Conciliado"
    if status == "Depósito parcial" or money(row.get("historical_remitted_usd")) > 0:
        return "Parcial"
    # Confirmed offsets may cancel just part of an application without a deposit.
    if money(row.get("application_adjustment_usd")) < 0:
        return "Parcial"
    return "Pendiente"


def deposit_state(row):
    if row.get("docstatus") != 1:
        return "Pendiente"
    total = money(row.get("amount_usd"))
    assigned = money(row.get("allocated_usd"))
    surplus = money(row.get("justified_surplus_usd"))
    if (total > 0 and assigned >= 0 and assigned + surplus == total and surplus >= 0
            and not money(row.get("unclassified_usd")) and row.get("result") in SETTLED_DEPOSIT):
        return "Conciliado"
    return "Parcial" if assigned > 0 or surplus > 0 else "Pendiente"


def monthly_counts(records, year):
    """Count physical application rows / deposit documents, never deduplicate by amounts."""
    groups = defaultdict(lambda: defaultdict(list))
    for record in records:
        try:
            day = date.fromisoformat(str(record.get("date") or "")[:10])
        except ValueError:
            continue
        if day.year == year:
            groups[record.get("employer") or ""][day.month].append(record["state"])
    output = []
    for employer, months in sorted(groups.items(), key=lambda item: item[0].casefold()):
        row = {"employer": employer, "total": 0}
        for month in range(1, 13):
            key = f"m{month:02}"
            states = months.get(month, [])
            row[key] = len(states)
            row["total"] += len(states)
            row[key + "_conciliado"] = states.count("Conciliado")
            row[key + "_parcial"] = states.count("Parcial")
            row[key + "_pendiente"] = states.count("Pendiente")
            row[key + "_state"] = ("" if not states else "Conciliado" if all(value == "Conciliado" for value in states)
                else "Parcial" if any(value != "Pendiente" for value in states) else "Pendiente")
        output.append(row)
    return output
