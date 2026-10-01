"""Explicit bank evidence; never confuse ledger turnover with cash received."""
import re

from credinomina_reconciliation.rounding import money_float


def extract_deposit(record, description):
    from credinomina_reconciliation.parsers import clean_text, parse_amount, parse_date

    currency = clean_text(record.get("moneda")).upper()
    amount = parse_amount(record.get("dep_en_banco"))
    explicit = bool(currency in {"USD", "NIO"} and amount > 0)
    match = re.search(r"^\s*(US\$|U\$|C\$)\s*([\d,]+(?:\.\d+)?)", description, re.I)
    if not explicit and match:
        currency = "NIO" if match[1].upper() == "C$" else "USD"
        amount = parse_amount(match[2])
        explicit = True
    # A bank name without an account identifier is not enough, even if there
    # happens to be only one account for that bank in the database.
    banks = re.findall(r"\bCUENTA\s+(BANPRO|BAC|LAFISE|BDF|FICOHSA|AVANZ)\s+([\d][\d-]*)\s*(US\$|U\$|C\$)(?!\w)", description, re.I)
    bank, number, bank_currency = "", "", ""
    if len(set(banks)) == 1:
        bank, number, symbol = banks[0]
        bank = bank.upper()
        bank_currency = "NIO" if symbol.upper() == "C$" else "USD"
    date_match = re.search(r"\bEL\s+DIA\s+(\d{1,2}/\d{1,2}/\d{4})", description, re.I)
    return {
        "bank_deposit_date": parse_date(date_match[1]) if date_match else None,
        "bank_deposit_currency": currency if explicit else "",
        "bank_deposit_amount": money_float(amount) if explicit else 0,
        "bank_deposit_reference": clean_text(record.get("no_ref_banco")) or clean_text(record.get("no_ref")),
        "bank_name_hint": bank, "bank_number_hint": number, "bank_currency_hint": bank_currency,
        "bank_deposit_notes": clean_text(record.get("comentaro_det_deposito") or record.get("comentario_det_deposito")),
    }
