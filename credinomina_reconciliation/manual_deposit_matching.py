"""Conservative matching of late accounting evidence to existing bank receipts."""
import frappe

from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import money

DOCTYPE = 'CN Remittance Allocation'
UNKNOWN_BANK = 'NO IDENTIFICADA'
FIELDS = ['name', 'employer', 'bank_account', 'deposit_reference', 'deposit_date',
          'deposit_currency', 'deposit_amount', 'deposit_voucher', 'docstatus',
          'accounting_source_key', 'cash_identity_status', 'consolidated_deposit']


def find_candidates(row, bank):
    """Reference first; date/amount only locates cases for human review.

    Voucher differences or missing bank/company information must not hide a
    possible duplicate. Never disclose candidate names without permissions.
    """
    company = row.get('resolved_employer')
    filters = {'docstatus': ['!=', 2]}
    if company and company != UNIDENTIFIED_EMPLOYER:
        filters['employer'] = company

    def candidates(extra):
        return [item for item in frappe.get_all(DOCTYPE, filters={**filters, **extra},
                fields=FIELDS, limit_page_length=0)
                if item.get('cash_identity_status') != 'Origen vinculado'
                and not item.get('consolidated_deposit')
                and (not bank or bank == UNKNOWN_BANK or not item.get('bank_account')
                     or item['bank_account'] in {bank, UNKNOWN_BANK})]

    matches = candidates({'deposit_reference': row['bank_deposit_reference']})
    if matches or not company or company == UNIDENTIFIED_EMPLOYER:
        return matches
    return candidates({'deposit_date': row['deposit_date'],
                       'deposit_currency': row['deposit_currency'],
                       'deposit_amount': row['deposit_amount']})


def exact_manual_match(document, row, bank):
    """An empty manual voucher is normal before accounting arrives."""
    company = row.get('resolved_employer')
    if (not company or company == UNIDENTIFIED_EMPLOYER or not bank or bank == UNKNOWN_BANK
            or not row.get('bank_deposit_currency') or not row.get('bank_deposit_amount')
            or document.get('docstatus') not in (0, 1)
            or document.get('accounting_source_key') or document.get('accounting_sources')
            or document.get('consolidated_deposit')
            or document.get('cash_identity_status') in {'Revisar identidad', 'Origen vinculado'}):
        return False
    expected = {'employer': company, 'bank_account': bank,
                'deposit_reference': row['bank_deposit_reference'],
                'deposit_date': row['deposit_date'], 'deposit_currency': row['deposit_currency']}
    if any(clean_text(document.get(field)) != clean_text(value) for field, value in expected.items()):
        return False
    if money(document.get('deposit_amount')) != money(row['deposit_amount']):
        return False
    voucher, incoming = clean_text(document.get('deposit_voucher')), clean_text(row.get('voucher'))
    return not (voucher and incoming and voucher != incoming)


def link_manual_candidate(candidates, row, bank, source_file, file_hash, group_key):
    if len(candidates) != 1 or not exact_manual_match(candidates[0], row, bank):
        return None
    # Reload under a row lock: the daily workflow may still be editing this receipt.
    document = frappe.get_doc(DOCTYPE, candidates[0]['name'], for_update=True)
    if not document.has_permission('write') or not exact_manual_match(document, row, bank):
        return None
    from credinomina_reconciliation.deposit_accounting_sources import record_imported_source, grouped_source_fits
    if not grouped_source_fits(document, row):
        return None
    record_imported_source(document, row, source_file, file_hash, group_key)
    return document
