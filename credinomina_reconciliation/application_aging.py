"""Read-only aging of core applications against their assigned deposits."""

from collections import defaultdict
from datetime import date, timedelta
import json

from credinomina_reconciliation.aging import age_balance
from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.rounding import money, money_float, sum_money


APPLICATION_BALANCE = "Aplicado pendiente de depósito"
IDENTITY_FIELDS = (
    "client", "client_name", "client_number", "national_id", "loan_number",
    "installment_number",
)


def deposit_due_date(application_date, grace_days=10):
    """Same convention as CN Employer: Nth day from next month's first day."""
    if not application_date:
        return None
    applied = date.fromisoformat(str(application_date)[:10])
    next_month = date(applied.year + (applied.month == 12), applied.month % 12 + 1, 1)
    return next_month + timedelta(days=max(int(grace_days or 10), 1) - 1)


def _details(value):
    entries = json.loads(value or "[]") if isinstance(value, str) else value or []
    return entries if isinstance(entries, list) else []


def application_balances(sources, imports, periods, collections, employers, as_of, *, include_settled=False):
    """Build balances without modifying allocations or offsetting unrelated credits.

    Operative cash belongs to a collection claim, not to each application. Group
    applications sharing that claim before subtracting cash, so a grouped core
    payment or multiple applications never consume the same cash twice.
    """
    groups = defaultdict(list)
    output = []

    def base(source, period, employer, mode):
        period = period or {}
        due = source.get("payment_due_date")
        if due:
            due = date.fromisoformat(str(due)[:10])
        elif employer in employers and employer != "NO IDENTIFICADA":
            due = deposit_due_date(source.get("event_date"), employers[employer].get("grace_days"))
        return {
            **{field: source.get(field) for field in IDENTITY_FIELDS},
            "source_import": source.get("parent"),
            "source_rows": source.get("name") or "",
            "application_date": source.get("event_date"),
            "period": period.get("name"), "employer": employer,
            "payroll_month": period.get("payroll_month"),
            "collection_cycle": period.get("collection_cycle"),
            "reconciliation_mode": mode, "usd_currency": "USD",
            "balance_type": APPLICATION_BALANCE,
            "due_date": due,
            "payment_term_origin": source.get("payment_term_origin") or "Plazo vigente sin conservar; no acredita el plazo histórico",
            "observation": "" if employer in employers and employer != "NO IDENTIFICADA" else "Empresa pendiente de identificar",
        }

    def emit(item, applied, paid=0, adjustment=0, fx=0):
        balance = max(money(applied) + money(adjustment) - money(paid), 0)
        if balance <= 0 and not include_settled:
            return
        output.append({
            **item, "applied_usd": money_float(applied),
            "paid_usd": money_float(paid), "adjustment_usd": money_float(adjustment),
            "fx_variance_usd": money_float(fx), "amount_usd": money_float(balance),
            **age_balance(balance, item.get("due_date"), as_of),
        })

    for source in sources:
        if (source.get("event_type") != "Aplicacion" or not source.get("effective")
                or source.get("match_status") == "Ignorado"):
            continue
        document = imports.get(source.get("parent"))
        if not document:
            continue
        historical = source.get("historical_period") or document.get("historical_period")
        mode = source.get("processing_route") or (
            "Historica" if historical or document.get("historical_backfill") else "Operativa"
        )
        period = periods.get(historical) if historical else None
        # A link to a period outside the caller's permissions must not become
        # an apparently unlinked, visible application.
        if historical and not period:
            continue
        employer = (period or {}).get("employer") or document.get("employer") or source.get("portfolio_employer")
        item = base(source, period, employer, mode)
        applied = converted_amount(source, "USD")
        if applied is None:
            output.append({**item, "observation": "Falta tipo de cambio; saldo US$ sin determinar"})
            continue
        if money(applied) <= 0:
            if include_settled and money(source.get("application_adjustment_usd")) > 0:
                item["observation"] = "Aplicación compensada por ajuste; no representa un depósito"
                emit(item, applied)
            continue
        if mode == "Historica":
            linked = bool(historical and source.get("match_status") == "Conciliado")
            paid = source.get("historical_remitted_usd") if linked else 0
            adjustment = sum_money(
                entry.get("diferencia_usd") for entry in _details(source.get("historical_detail"))
            ) if linked else 0
            if not linked:
                item["observation"] = "Aplicación pendiente de vincular al período histórico"
            emit(item, applied, paid, adjustment)
            continue

        links = []
        if source.get("match_status") in {"Conciliado", "Enlace provisional"}:
            links = _details(source.get("application_allocation_detail"))
            if not links and source.get("collection_row_id"):
                links = [{"collection_row_id": source["collection_row_id"], "amount_usd": applied}]
        for link in links:
            collection = collections.get(link.get("collection_row_id"))
            if not collection:
                continue
            period = periods[collection["parent"]]
            part = base(source, period, period["employer"], "Operativa")
            part.update({field: collection.get(field) or source.get(field) for field in IDENTITY_FIELDS})
            part["applied_usd"] = money(link.get("amount_usd"))
            groups[collection["name"]].append(part)
        remaining = money(applied) - sum_money(link.get("amount_usd") for link in links)
        if remaining > 0:
            item["observation"] = "Aplicación pendiente de vincular a la cobranza"
            emit(item, remaining)

    for name, parts in groups.items():
        collection = collections[name]
        # Subtract only the cash assigned to the loan claim. A complementary
        # item may be paid partially or not paid; its face amount is not cash.
        paid = sum_money(
            entry.get("importe_usd") for entry in _details(collection.get("remittance_detail"))
            if entry.get("destino") != "Partida complementaria"
        )
        adjustment = money(collection.get("rounding_adjustment_usd"))
        applied = sum_money(part["applied_usd"] for part in parts)
        if max(applied + adjustment - paid, 0) <= 0 and not include_settled:
            continue
        dates = {part["due_date"] for part in parts}
        if len(dates) > 1 and (paid or adjustment):
            # Do not invent a payment order when the stored allocation does
            # not identify which month's applications were covered.
            item = dict(parts[0], due_date=None, application_date=None)
            item["source_rows"] = ", ".join(sorted({part["source_rows"] for part in parts if part["source_rows"]}))
            if len({part["source_import"] for part in parts}) > 1:
                item["source_import"] = None
            item["observation"] = "Varias fechas de vencimiento: falta distribuir el pago entre aplicaciones"
            emit(item, applied, paid, adjustment, collection.get("fx_variance_usd"))
            continue
        by_due = defaultdict(list)
        for part in parts:
            by_due[part["due_date"]].append(part)
        for grouped in by_due.values():
            item = dict(grouped[0])
            item["source_rows"] = ", ".join(sorted({part["source_rows"] for part in grouped if part["source_rows"]}))
            application_dates = [part["application_date"] for part in grouped if part.get("application_date")]
            item["application_date"] = min(application_dates) if application_dates else None
            if len({part["source_import"] for part in grouped}) > 1:
                item["source_import"] = None
            if len(grouped) > 1:
                item["observation"] = "; ".join(filter(None, [item["observation"],
                    "Aplicaciones agrupadas de la misma cuota y vencimiento"]))
            if money(collection.get("fx_variance_usd")):
                item["observation"] = "; ".join(filter(None, [item["observation"],
                    "Diferencia cambiaria pendiente de revisión"]))
            emit(item, sum_money(part["applied_usd"] for part in grouped), paid, adjustment,
                 collection.get("fx_variance_usd"))
    return output
