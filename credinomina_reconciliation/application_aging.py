"""Read-only aging of core applications against their assigned deposits."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from credinomina_reconciliation.aging import age_balance
from credinomina_reconciliation.accounting_client_summary import collection_links, collection_link_issue, entries, explicit_collection_cash
from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.rounding import money, money_float, sum_money


APPLICATION_BALANCE = "Aplicado pendiente de depósito"
TOTAL_RECEIVABLE_BALANCE = "CxC total"
ADJUSTMENT_BALANCE = "CxC por ajustes"
IDENTITY_FIELDS = (
    "client", "client_name", "client_number", "national_id", "loan_number",
    "installment_number",
)


def balance_status(source):
    """One presentation state for an application's current financial position."""
    if source.get('distribution_issue'):
        return 'Revisar distribución'
    if source.get('amount_usd') is None:
        return 'Sin conversión US$'
    if (money(source.get('paid_usd')) > money(source.get('applied_usd')) + money(source.get('adjustment_usd'))
            or money(source.get('fx_variance_usd'))):
        return 'Revisar distribución'
    if money(source.get('amount_usd')) <= 0:
        return 'Conciliado'
    return 'Parcial' if money(source.get('paid_usd')) > 0 or source.get('has_application_adjustment') else 'Pendiente'


def deposit_due_date(application_date, grace_days=10):
    """Same convention as CN Employer: Nth day from next month's first day."""
    if not application_date:
        return None
    applied = date.fromisoformat(str(application_date)[:10])
    next_month = date(applied.year + (applied.month == 12), applied.month % 12 + 1, 1)
    return next_month + timedelta(days=max(int(grace_days or 10), 1) - 1)


def _historical_coverage(source, *, statement_detail):
    """The stored total covers the loan. Its bank and credit components are distinct."""
    from credinomina_reconciliation.period_totals import credit_funding

    journal = entries(source.get('historical_detail'))
    paid = money(source.get('historical_remitted_usd'))
    adjustment = sum_money(part.get('diferencia_usd') for part in journal)
    if not statement_detail:
        return paid, adjustment, money(0)
    amounts = [Decimal(str(part['importe_usd'])) for part in journal if 'importe_usd' in part]
    if (paid < 0 or any(not value.is_finite() or value < 0 for value in amounts)
            or (amounts and sum_money(amounts) != paid)
            or any(not Decimal(str(part.get('diferencia_usd', 0))).is_finite() for part in journal)):
        raise ValueError()
    credit = credit_funding([source]) if any(part.get('saldo_a_favor') or part.get('origen') == 'Saldo a favor' for part in journal) else money(0)
    if credit is None or credit > paid:
        raise ValueError()
    return paid, adjustment, credit


def application_balances(sources, imports, periods, collections, employers, as_of, *, include_settled=False,
                         include_reviews=False, statement_detail=False):
    """Build balances without modifying allocations or offsetting unrelated credits.

    Operative cash belongs to a collection claim, not to each application. Group
    applications sharing that claim before subtracting cash, so a grouped core
    payment or multiple applications never consume the same cash twice.
    """
    groups = defaultdict(list)
    output = []
    sources = list(sources)
    usage, projected = defaultdict(lambda: money(0)), defaultdict(lambda: money(0))
    for source in sources:
        document = imports.get(source.get('parent'), {})
        if (not document or source.get('event_type') != 'Aplicacion' or not source.get('effective')
                or source.get('match_status') == 'Ignorado'):
            continue
        direct = (source.get('historical_period') or document.get('historical_period') or
                  document.get('historical_backfill') or source.get('processing_route') == 'Historica')
        if direct and (not statement_detail or source.get('match_status') != 'Conciliado'):
            continue
        try:
            for link in collection_links(source, converted_amount(source, 'USD')):
                (projected if direct else usage)[link.get('collection_row_id')] += max(money(link.get('amount_usd')), 0)
        except (ValueError, TypeError, ArithmeticError):
            pass  # The affected row below retains the invalid-evidence warning.

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
            "difference_reason": source.get("difference_reason") or "",
            "has_application_adjustment": money(source.get("application_adjustment_usd")) > 0,
            "application_date": source.get("event_date"),
            "period": period.get("name"), "employer": employer,
            "payroll_month": period.get("payroll_month"),
            "collection_cycle": period.get("collection_cycle") or ("Mensual" if period and mode == "Operativa" else None),
            "reconciliation_mode": mode, "usd_currency": "USD",
            "balance_type": APPLICATION_BALANCE,
            "due_date": due,
            "payment_term_origin": source.get("payment_term_origin") or "Plazo vigente sin conservar; no acredita el plazo histórico",
            "observation": "" if employer in employers and employer != "NO IDENTIFICADA" else "Empresa pendiente de identificar",
        }

    def emit(item, applied, paid=0, adjustment=0, fx=0, parts=()):
        balance = max(money(applied) + money(adjustment) - money(paid), 0)
        review = money(paid) > money(applied) + money(adjustment) or money(fx)
        if balance <= 0 and not include_settled and not (include_reviews and review):
            return
        if statement_detail and len({part['application_date'] for part in parts}) > 1:
            dates = sorted(str(part['application_date'])[:10] for part in parts if part.get('application_date'))
            item = dict(item, _statement_parts=parts, application_date=dates[0] if dates else None)
            if dates:
                item['observation'] = ' · '.join(filter(None, [item.get('observation'),
                    f'Aplicaciones del {dates[0]} al {dates[-1]}.']))
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
        if period:
            mode = period.get("reconciliation_mode") or mode
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
        if money(applied) <= 0 and money(source.get('application_adjustment_usd')) > 0:
            original = converted_amount({**source, 'application_adjustment_usd': 0}, 'USD')
            if original is not None and money(source['application_adjustment_usd']) > money(original):
                issue = 'El ajuste supera el importe original de la aplicación; revise su distribución.'
                output.append({**item, 'applied_usd': None, 'paid_usd': None, 'adjustment_usd': None,
                    'amount_usd': None, 'distribution_issue': issue, 'observation': issue,
                    **age_balance(None, item.get('due_date'), as_of)})
                continue
        if source.get('_cutoff_distribution_issue'):
            issue = source['_cutoff_distribution_issue']
            output.append({**item, 'applied_usd': money_float(applied),
                'paid_usd': None, 'adjustment_usd': None, 'fx_variance_usd': None,
                'amount_usd': None, 'distribution_issue': issue, 'observation': issue,
                **age_balance(None, item.get('due_date'), as_of)})
            continue
        if money(applied) <= 0 and money(source.get("application_adjustment_usd")) > 0:
            item["observation"] = "Aplicación compensada por ajuste; el ajuste no representa un depósito"
        if historical or mode == "Historica":
            linked = bool(historical and source.get("match_status") == "Conciliado")
            try:
                paid, adjustment, credit = _historical_coverage(source, statement_detail=statement_detail) if linked else (0, 0, 0)
            except (TypeError, ValueError, ArithmeticError):
                issue = 'La distribución histórica no coincide con la cobertura registrada. Revise la aplicación.'
                output.append({**item, 'applied_usd': money_float(applied), 'paid_usd': None, 'amount_usd': None,
                               'distribution_issue': issue, 'observation': issue, **age_balance(None, item.get('due_date'), as_of)})
                continue
            if statement_detail:
                item['deposit_cash_usd'] = money_float(paid - credit)
            if credit:
                item['observation'] = ' · '.join(filter(None, [item['observation'], f'Saldo a favor aplicado US$ {credit:.2f}.']))
            if not linked:
                item["observation"] = "Aplicación pendiente de vincular al período histórico"
            emit(item, applied, paid, adjustment)
            continue

        try:
            links = collection_links(source, applied)
            # Missing claims may belong to an inaccessible period. Preserve
            # the caller's scope instead of exposing them as unlinked debt.
            if any(link.get('collection_row_id') not in collections for link in links):
                continue
            # A valid zero share cannot consume or overbook the claim. Keep
            # validating its own links without assigning another row's excess.
            issue = collection_link_issue(links, applied, collections, usage if money(applied) > 0 else None)
        except (ValueError, TypeError, ArithmeticError):
            issue = 'Detalle de distribución inválido; revise los vínculos de la aplicación.'
        if issue:
            output.append({**item, 'applied_usd': money_float(applied),
                'paid_usd': None, 'adjustment_usd': None, 'fx_variance_usd': None,
                'amount_usd': None, 'distribution_issue': issue, 'observation': issue,
                **age_balance(None, item.get('due_date'), as_of)})
            continue
        if money(applied) <= 0:
            # Validate saved links before treating a zero net as settled. With
            # valid zero links, other applications own the collection's cash.
            if include_settled:
                emit(item, applied)
            continue
        for link in links:
            collection = collections.get(link.get("collection_row_id"))
            if not collection:
                continue
            period = periods[collection["parent"]]
            part = base(source, period, period["employer"], "Operativa")
            part.update({field: collection.get(field) or source.get(field) for field in IDENTITY_FIELDS})
            part['collection_row_id'] = collection['name']
            part["applied_usd"] = money(link.get("amount_usd"))
            groups[collection["name"]].append(part)
        remaining = money(applied) - sum_money(link.get("amount_usd") for link in links)
        if remaining > 0:
            item["observation"] = "Aplicación pendiente de vincular a la cobranza"
            emit(item, remaining)

    for name, parts in groups.items():
        collection = collections[name]
        try:
            journal = entries(collection.get('remittance_detail'))
            if any(not Decimal(str(part.get(key, 0))).is_finite() for part in journal
                   for key in ('importe_usd', 'diferencia_usd')):
                raise ValueError('Importe inválido en la distribución de la cobranza.')
            explicit = explicit_collection_cash(collection)
        except (ValueError, TypeError, ArithmeticError) as error:
            for part in parts:
                output.append({**part, 'paid_usd': None, 'amount_usd': None,
                    'distribution_issue': str(error), 'observation': str(error),
                    **age_balance(None, part.get('due_date'), as_of)})
            continue
        if explicit is not None:
            # Keep each application's identity/date when the executed ledger
            # specifies its share. Repeated links to one claim consume cash once.
            by_application = {}
            for part in parts:
                key = (part.get('source_import'), part.get('source_rows'))
                if key in by_application:
                    by_application[key]['applied_usd'] += money(part['applied_usd'])
                else:
                    by_application[key] = dict(part)
            for part in by_application.values():
                assigned = explicit.get(part.get('source_rows'), 0)
                if money(assigned) > money(part['applied_usd']):
                    issue = 'La distribución por aplicación supera su importe vinculado; revise la cobranza.'
                    output.append({**part, 'paid_usd': None, 'amount_usd': None,
                        'distribution_issue': issue, 'observation': issue,
                        **age_balance(None, part.get('due_date'), as_of)})
                else:
                    emit(part, part['applied_usd'], assigned, fx=collection.get('fx_variance_usd'))
            continue
        # Subtract only the cash assigned to the loan claim. A complementary
        # item may be paid partially or not paid; its face amount is not cash.
        paid = sum_money(
            entry.get("importe_usd") for entry in journal
            if entry.get("destino") != "Partida complementaria" and not entry.get("aplicacion_directa")
        )
        adjustment = money(collection.get("rounding_adjustment_usd")) - sum_money(
            entry.get("diferencia_usd") for entry in journal if entry.get("aplicacion_directa"))
        applied = sum_money(part["applied_usd"] for part in parts)
        if (statement_detail and (paid or adjustment) and collection.get('applied_usd') is not None
                and applied + projected[name] != money(collection['applied_usd'])):
            issue = 'La cobertura es compartida con otras aplicaciones de la cuota. Revise la distribución.'
            for part in parts:
                output.append({**part, 'paid_usd': None, 'amount_usd': None, 'distribution_issue': issue,
                               'observation': issue, **age_balance(None, part.get('due_date'), as_of)})
            continue
        if max(applied + adjustment - paid, 0) <= 0 and not include_settled and not (
                include_reviews and (paid > applied + adjustment or money(collection.get('fx_variance_usd')))):
            continue
        dates = {part["due_date"] for part in parts}
        if len(dates) > 1 and (paid or adjustment):
            # Do not invent a payment order when the stored allocation does
            # not identify which month's applications were covered.
            item = dict(parts[0], due_date=None, application_date=None)
            item['has_application_adjustment'] = any(part.get('has_application_adjustment') for part in parts)
            item["source_rows"] = ", ".join(sorted({part["source_rows"] for part in parts if part["source_rows"]}))
            item["difference_reason"] = " · ".join(sorted({part["difference_reason"] for part in parts if part["difference_reason"]}))
            if len({part["source_import"] for part in parts}) > 1:
                item["source_import"] = None
            item["observation"] = "Varias fechas de vencimiento: falta distribuir el pago entre aplicaciones"
            emit(item, applied, paid, adjustment, collection.get("fx_variance_usd"), parts)
            continue
        by_due = defaultdict(list)
        for part in parts:
            by_due[part["due_date"]].append(part)
        for grouped in by_due.values():
            item = dict(grouped[0])
            item['has_application_adjustment'] = any(part.get('has_application_adjustment') for part in grouped)
            item["source_rows"] = ", ".join(sorted({part["source_rows"] for part in grouped if part["source_rows"]}))
            item["difference_reason"] = " · ".join(sorted({part["difference_reason"] for part in grouped if part["difference_reason"]}))
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
                 collection.get("fx_variance_usd"), grouped)
    return output
