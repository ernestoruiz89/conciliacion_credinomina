"""Original accounting turnover, independent of cash allocation and internal offsets."""
from collections import defaultdict

from credinomina_reconciliation.accounting_evidence import is_deposit, same_ledger_evidence
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money
from credinomina_reconciliation.complementary_balances import financial_balance, reconciliation_state

MONEY_FIELDS = ("debit_nio", "credit_nio", "net_nio", "debit_usd", "credit_usd", "net_usd")


def _deposit_status(deposit):
    if deposit.get('cash_identity_status') == 'Revisar identidad':
        return 'Revisar identidad del depósito'
    from credinomina_reconciliation.deposit_presentation import reconciliation_status
    return reconciliation_status(deposit)


def _status(row, item=None, parent=None, collections=None, progress=None):
    if item:
        if item.get("docstatus") == 2:
            return "Partida cancelada; evidencia conservada"
        if item.get("category"):
            balance = item.get("_financial_balance")
            if balance is None:
                from credinomina_reconciliation.complementary_balances import CASH_CATEGORIES
                if item.get("category") in CASH_CATEGORIES and item.get("docstatus") == 1 and item.get("cash_assigned_usd") is None:
                    return "Estado de depósito no disponible"
                balance = financial_balance(item, [{"amount_usd": item.get("cash_assigned_usd") or 0}])
            return reconciliation_state(balance)
        return (item.get("compensation_status") or item.get("result") or item.get("review_status")
                or "Por revisar")
    if row.get("match_status") == "Ignorado":
        return "Ignorado para conciliación"
    if row.get('event_type') == 'Aplicacion':
        from credinomina_reconciliation.company_transactions import application_progress
        return (progress if progress is not None else application_progress(row, parent or {}, collections))["state"]
    if row.get("event_type") == "Ajuste":
        return "Por revisar"
    if row.get("application_adjustment_status") and row["application_adjustment_status"] != "Sin ajuste":
        return row["application_adjustment_status"]
    return "Pendiente"


def _amounts(row, currency, rate):
    values = {field: None for field in MONEY_FIELDS}
    if not row.get("source_account") or not any(money(row.get(field)) for field in ("source_debit", "source_credit")):
        return values, "Sin evidencia de débitos/créditos originales: revisar o reprocesar el archivo."
    debit, credit = money(row.get("source_debit")), money(row.get("source_credit"))
    if currency == "NIO":
        values.update(debit_nio=money_float(debit), credit_nio=money_float(credit), net_nio=money_float(debit - credit))
        if decimal_value(rate) <= 0:
            return values, "Sin tasa para convertir a US$: totales convertidos incompletos."
        debit, credit = money(debit / decimal_value(rate)), money(credit / decimal_value(rate))
    elif currency != "USD":
        return values, "Moneda original sin identificar: no se incluyen importes en los totales."
    values.update(debit_usd=money_float(debit), credit_usd=money_float(credit), net_usd=money_float(debit - credit))
    return values, ""


def build_rows(sources, imports, items, clients=(), deposits=(), *, collections=None, progress=None, include_financial=True):
    """Count mirrors once, but never remove physical lines by value similarity."""
    from credinomina_reconciliation.deposit_accounting_sources import ledger_deposits
    deposits = ledger_deposits(deposits)
    by_item = {item["name"]: item for item in items}
    by_deposit = defaultdict(list)
    by_deposit_name = {deposit["name"]: deposit for deposit in deposits}
    for deposit in deposits:
        if deposit.get("accounting_source_key"):
            by_deposit[deposit["accounting_source_key"]].append(deposit)
    by_client = defaultdict(list)
    for client in clients:
        by_client[client.get("client_number")].append(client)
    seen, represented_items, represented_deposits = set(), set(), set()
    represented_evidence = defaultdict(list)
    output, duplicates = [], 0

    def provenance(row, parent=None):
        parent = parent or {}
        return {**row, "source_currency": row.get("source_currency") or parent.get("currency"),
                "source_file_hash": row.get("source_file_hash") or parent.get("bulk_source_hash") or parent.get("file_hash")}

    def deposit_for(row, parent=None):
        candidates = by_deposit.get(row.get("accounting_source_key"), [])
        linked = by_deposit_name.get(row.get("remittance_allocation"))
        if linked and linked not in candidates:
            candidates = [*candidates, linked]
        matches = [candidate for candidate in candidates
                   if (is_deposit(row) or (candidate.get('origin_complementary_item')
                       and row.get('accounting_classification') == 'Movimiento interno'))
                   and same_ledger_evidence(provenance(row, parent), candidate)]
        if len(matches) == 1:
            return matches[0], ""
        warning = ("Vínculo de origen contable inconsistente: no se mezclaron los datos del depósito; revise la identidad de esta fila."
                   if candidates else "")
        return None, warning

    def make_row(row, parent=None, item=None, deposit=None, identity_warning=""):
        parent = parent or {}
        currency = row.get("source_currency") or parent.get("currency") or ""
        rate = row.get("source_fx_rate") or row.get("manual_fx_rate") or row.get("fx_rate") or parent.get("manual_fx_rate") or 0
        values, warning = _amounts(row, currency, rate)
        missing_conversion = int(bool(warning))
        warning = "; ".join(filter(None, [warning, identity_warning]))
        source_import = parent.get("name") or ""
        # Cash beneficiaries/other companies never change the owner of an application.
        employer = (deposit or item or {}).get("employer") or row.get("portfolio_employer") or parent.get("employer") or row.get("employer") or ""
        number = row.get("client_number") or (item or {}).get("client_number") or ""
        client_name = row.get("client_name") or row.get("source_client_name") or row.get("portfolio_client_name") or ""
        matches = [client for client in by_client.get(number, []) if not employer or client.get("employer") == employer]
        if not client_name and len(matches) == 1:
            client_name = matches[0].get("client_name") or ""
        date = str(row.get("event_date") or row.get("source_date") or row.get("posting_date") or "")[:10]
        financial = (progress or {}).get(row.get('name')) if include_financial else None
        if not item and not deposit and financial and financial.get('employer'):
            employer = financial['employer']
        financial_warning = '; '.join(financial.get('observations') or []) if financial else ''
        warning = '; '.join(filter(None, [warning, financial_warning]))
        pending_amount = financial.get('pending_usd') if financial else None
        if deposit and include_financial:
            pending_amount = (None if deposit.get('amount_usd') is None else money_float(
                money(deposit['amount_usd']) - money(deposit.get('allocated_usd')) - money(deposit.get('justified_surplus_usd'))))
            if deposit.get('_cash_primary_key') != deposit.get('accounting_source_key'):
                pending_amount = None
                warning = '; '.join(filter(None, [warning, 'El saldo financiero pertenece al depósito y se muestra una sola vez; esta fila conserva su movimiento contable original.']))
        elif include_financial and item and item.get('_financial_balance'):
            pending_amount = item['_financial_balance'].get('pending_usd')
        return {"event_date": date, "month": date[:7], "source_account": row.get("source_account") or "Sin identificar",
            "source_currency": currency or "Sin identificar", "nio_currency": "NIO", "usd_currency": "USD", **values,
            "fx_rate": rate if currency == "NIO" else None,
            "client_name": client_name or ("" if deposit else "Sin identificar"), "client_number": number,
            "loan_number": row.get("loan_number") or row.get("source_loan_number") or (item or {}).get("loan_number") or "",
            "employer": employer, "voucher": row.get("source_voucher") or row.get("voucher") or "",
            "movement_type": row.get("accounting_classification") or row.get("event_type") or "Por revisar",
            "state": (_deposit_status(deposit) if deposit else _status(row, item, parent, collections, financial)) if include_financial else "Origen contable",
            "applied_usd": financial.get('net_usd') if financial else None,
            "assigned_usd": financial.get('assigned_usd') if financial else None,
            "pending_usd": pending_amount,
            "audit_action": "Ver origen",
            "recorded_application_state": row.get('deposit_match_status') or '',
            "recorded_deposit_result": (deposit or {}).get("result") or "",
            "accounting_status": (item or {}).get("accounting_status") or "",
            "management_status": (item or {}).get("credit_management_status") or "",
            "category": (item or {}).get("category") or "", "reason_type": (item or {}).get("reason_type") or "",
            "difference_reason": row.get("difference_reason") or "",
            "subcategory": (item or {}).get("subcategory") or "",
            "treatment_status": ((item or {}).get("_financial_balance") or {}).get("financial_status") or (item or {}).get("review_status") or "",
            "description": row.get("source_description") or row.get("description") or "",
            "remittance_allocation": deposit["name"] if deposit else "", "bank_account": (deposit or {}).get("bank_account") or "",
            "accounting_import": source_import, "complementary_item": (item or {}).get("name") or "",
            "source_file": row.get("source_file") or parent.get("bulk_source_file") or parent.get("source_file") or "",
            "source_hash": row.get("source_file_hash") or parent.get("bulk_source_hash") or parent.get("file_hash") or "",
            "source_row": row.get('source_row'), "source_row_id": row.get('name') if source_import else '',
            "source_idx": row.get('idx') if source_import else None,
            "evidence_key": row.get("accounting_source_key") or "", "warning": warning,
            "identity_conflict": int(bool(identity_warning)),
            "movement_count": 1, "missing_conversion": missing_conversion,
            "import_status": parent.get("status") or ""}

    for source in sorted(sources, key=lambda row: (str(imports.get(row.get("parent"), {}).get("creation") or ""), row.get("parent") or "", row.get("idx") or 0)):
        parent = imports.get(source.get("parent"))
        if not parent:
            continue
        evidence = provenance(source, parent)
        represented_evidence[source.get("accounting_source_key")].append(evidence)
        item = by_item.get(source.get("complementary_item"))
        if item and same_ledger_evidence(evidence, item):
            represented_items.add(item["name"])
        else:
            item = None
        deposit, warning = deposit_for(source, parent)
        if deposit:
            represented_deposits.add((deposit["name"], deposit.get('accounting_source_key')))
        output.append(make_row(source, parent, item, deposit, warning))
    for item in items:
        if not item.get("accounting_source_key"):
            continue  # Manually created fees/offsets are not evidence of an imported ledger.
        if item["name"] in represented_items or any(
            same_ledger_evidence(item, source)
            for source in represented_evidence.get(item["accounting_source_key"], [])
        ):
            continue
        deposit, warning = deposit_for(item)
        if deposit:
            represented_deposits.add((deposit['name'], deposit.get('accounting_source_key')))
        output.append(make_row(item, item=item, deposit=deposit, identity_warning=warning))
    for deposit in deposits:
        if not deposit.get("accounting_source_key") or (deposit["name"], deposit.get('accounting_source_key')) in represented_deposits:
            continue
        output.append(make_row(deposit, deposit=deposit))
    for row in output:
        key = tuple(row.get(field) for field in (
            "event_date", "source_account", "source_currency", "voucher", "description",
            "debit_nio", "credit_nio", "debit_usd", "credit_usd", "employer", "movement_type",
        ))
        if key in seen:
            duplicates += 1
            row["warning"] = "; ".join(filter(None, [row["warning"],
                "Posible repetición contable: incluida en los totales; revise la evidencia original."]))
        seen.add(key)
    return sorted(output, key=lambda row: (row["event_date"], row["source_account"], row["voucher"], row["accounting_import"])), duplicates


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["month"], row["source_account"], row["source_currency"])].append(row)
    result = []
    for (month, account, currency), group in sorted(groups.items()):
        result.append({"month": month, "source_account": account, "source_currency": currency,
            "nio_currency": "NIO", "usd_currency": "USD", "movement_count": len(group),
            "missing_conversion": sum(row["missing_conversion"] for row in group),
            **monetary_totals(group)})
    return result


def monetary_totals(rows):
    """Unknown amounts poison only their applicable total, never known subtotals.

    USD originals have no NIO original amount; that is not missing evidence.
    An unidentified currency, in contrast, cannot certify either currency total.
    """
    result = {}
    for field in MONEY_FIELDS:
        applicable = [row for row in rows if not (field.endswith('_nio') and row.get('source_currency') == 'USD')]
        missing = sum(row.get(field) is None for row in applicable)
        known = money_float(sum_money(row.get(field) for row in applicable if row.get(field) is not None))
        result.update({field: None if missing else known, field + '_known': known, field + '_missing': missing})
    return result
