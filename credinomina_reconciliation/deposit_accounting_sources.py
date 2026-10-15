"""One bank receipt, many immutable physical ledger lines. Never discard turnover."""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json

import frappe
from frappe import _

from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import decimal_value

SOURCE_DOCTYPE = 'CN Deposit Accounting Source'
_writing = ContextVar('cn_deposit_source_writer', default=False)
IDENTITY_FIELDS = ('deposit_group_key', 'cash_identity_status', 'cash_identity_reason', 'consolidated_deposit',
                   'origin_link_status', 'origin_link_notes', 'linked_core_deposit')


@contextmanager
def source_writer():
    token = _writing.set(True)
    try:
        yield
    finally:
        _writing.reset(token)


def grouping_key(row, file_hash):
    """Only identical explicit bank events in the same file are auto-grouped.

    Without a bank/number, owner, voucher or explicit cash amount, similarity
    is a review candidate, not proof. Different descriptions/dates stay apart.
    """
    company = row.get('resolved_employer')
    if (not company or company == 'NO IDENTIFICADA' or not row.get('bank_name_hint')
            or not row.get('bank_number_hint') or not row.get('bank_deposit_currency')
            or not row.get('voucher') or not row.get('bank_deposit_reference') or not file_hash):
        return ''
    values = [file_hash, company, row['bank_name_hint'], row['bank_number_hint'].replace('-', ''),
        str(row.get('deposit_date')), str(row.get('event_date')), row['bank_deposit_reference'],
        row['voucher'], row['deposit_currency'], str(decimal_value(row['deposit_amount'])),
        row.get('source_currency'), str(decimal_value(row.get('source_fx_rate'))),
        clean_text(row.get('source_description')).casefold()]
    return sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def evidence_values(row, source_file, file_hash):
    from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
    return {**{field: row.get(field) for field in EVIDENCE_FIELDS if field in row},
        'source_date': row.get('event_date') or row.get('source_date'),
        'source_voucher': row.get('voucher') or row.get('source_voucher'),
        'source_client_name': row.get('client_name') or row.get('source_client_name') or '',
        'source_loan_number': row.get('loan_number') or row.get('source_loan_number') or '',
        'source_file': source_file, 'source_file_hash': file_hash}


def append_source(document, values, *, check_cash=True):
    if any(row.accounting_source_key == values['accounting_source_key'] for row in document.get('accounting_sources') or []):
        return False
    document.append('accounting_sources', values)
    if check_cash and len(document.accounting_sources) > 1:
        from credinomina_reconciliation.rounding import money, sum_money
        total = sum_money(decimal_value(row.source_credit) / decimal_value(row.source_fx_rate)
                          if row.source_currency == 'NIO' and decimal_value(row.source_fx_rate) > 0 else row.source_credit
                          for row in document.accounting_sources)
        if total > money(document.amount_usd) + money('0.01'):
            document.cash_identity_status = 'Revisar identidad'
            document.cash_identity_reason = f'Las líneas contables acumulan US$ {total:.2f} y la evidencia bancaria indica US$ {money(document.amount_usd):.2f}. Verifique si es un solo efectivo; todas las líneas se conservan en el control contable.'
    return True


def record_imported_source(document, row, source_file, file_hash, group_key):
    """Append evidence to a locked receipt without rerunning reconciliation hooks.

    Only the importer calls this, after matching the bank identity and checking
    the accounting creation lock. In particular, submitted/closed distributions
    and their dates, FX rates, targets and results must remain byte-for-byte intact.
    """
    from frappe.utils import now_datetime
    from credinomina_reconciliation.file_references import attach_existing_file
    document.check_permission('write')
    values = evidence_values(row, source_file, file_hash)
    if any(source.accounting_source_key == values['accounting_source_key']
           for source in document.get('accounting_sources') or []):
        return False
    source = document.append('accounting_sources', dict(values))
    timestamp = now_datetime()
    source.update({'docstatus': document.docstatus, 'owner': frappe.session.user,
                   'creation': timestamp, 'modified': timestamp, 'modified_by': frappe.session.user})
    # This narrowly scoped evidence insert deliberately avoids saving the parent:
    # an ordinary save validates/recalculates live financial inputs, including
    # closed periods. Source fields come only from the validated import plan.
    source.db_insert()
    updates = {'modified': timestamp, 'modified_by': frappe.session.user}
    if not document.get('accounting_source_key'):
        updates.update(values, deposit_group_key=group_key)
    frappe.db.set_value(document.doctype, document.name, updates)
    document.update(updates)
    attach_existing_file(source_file, document.doctype, document.name, 'source_file')
    document.add_comment('Comment', frappe._(
        'Contabilidad vinculada automáticamente al depósito existente. Fila {0}; '
        'asiento {1}. Se conserva el efectivo y la distribución registrada.'
    ).format(values.get('source_row'), clean_text(values.get('source_voucher'))))
    return True


def grouped_source_fits(document, row):
    """Never put already confirmed cash into review by appending excess turnover."""
    from credinomina_reconciliation.rounding import money, sum_money
    sources = [*(document.get('accounting_sources') or []), row]
    total = sum_money(decimal_value(source.get('source_credit')) / decimal_value(source.get('source_fx_rate'))
                      if source.get('source_currency') == 'NIO' and decimal_value(source.get('source_fx_rate')) > 0
                      else source.get('source_credit') for source in sources)
    return total <= money(document.amount_usd) + money('0.01')


def preview_deposits(rows, file_hash):
    """Cash receipt counts and ledger-line counts are explicit in the preview."""
    groups = {}
    for row in rows:
        key = grouping_key(row, file_hash) or row.get('accounting_source_key') or str(row['source_row'])
        group = groups.setdefault(key, dict(row, accounting_line_count=0))
        group['accounting_line_count'] += 1
    return list(groups.values())


def source_parent(key):
    names = frappe.get_all(SOURCE_DOCTYPE, filters={'accounting_source_key': key}, pluck='parent')
    active = frappe.get_all('CN Remittance Allocation', filters={'name': ['in', names],
        'consolidated_deposit': ['is', 'not set']}, pluck='name') if names else []
    if len(active) > 1:
        frappe.throw(_('La línea contable está vinculada a varios depósitos; revise su origen antes de reprocesar.'))
    return active[0] if active else None


def attach_sources(deposits):
    """Caller supplies permission-checked parents, never unrestricted child access."""
    from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
    from credinomina_reconciliation.report_records import child_records
    by_name = {row['name']: row for row in deposits}
    for row in by_name.values():
        row['accounting_sources'] = []
    for source in child_records(SOURCE_DOCTYPE, list(by_name), 'CN Remittance Allocation',
            'accounting_sources', fields=['parent', *EVIDENCE_FIELDS, 'origin_complementary_item']):
        by_name[source['parent']]['accounting_sources'].append(source)
    return deposits


def load_ledger_deposits(dates, fields):
    """Find parents by each physical posting date, then apply parent permissions."""
    deposits = frappe.get_list('CN Remittance Allocation', filters={'accounting_source_key': ['is', 'set'],
        'source_date': ['between', dates]}, fields=fields, limit_page_length=0)
    known = {row['name'] for row in deposits}
    parents = set(frappe.get_all(SOURCE_DOCTYPE, filters={'source_date': ['between', dates]}, pluck='parent')) - known
    for offset in range(0, len(parents), 500):
        deposits.extend(frappe.get_list('CN Remittance Allocation', filters={'name': ['in', sorted(parents)[offset:offset + 500]]},
            fields=fields, limit_page_length=0))
    return attach_sources(deposits)


def ledger_deposits(deposits):
    """Expand ledger origins only. Financial reports still count the parent once."""
    result = []
    for deposit in deposits:
        if deposit.get('cash_identity_status') == 'Origen vinculado':
            continue
        for source in deposit.get('accounting_sources') or [deposit]:
            primary = deposit.get('accounting_source_key') or (deposit.get('accounting_sources') or [{}])[0].get('accounting_source_key')
            result.append({**deposit, **source, 'name': deposit['name'], '_cash_primary_key': primary})
    return result


def validate_sources(doc, previous):
    if _writing.get():
        return
    if previous and previous.get('consolidated_deposit'):
        from credinomina_reconciliation.client_credit import _same_value
        if any(not _same_value(field, previous.get(field), doc.get(field)) for field in
                ('employer', 'bank_account', 'deposit_date', 'deposit_reference', 'deposit_voucher', 'deposit_currency', 'deposit_amount', 'fx_rate')) or doc.get('targets') or doc.get('detail_rows') or doc.get('detail_file'):
            frappe.throw(_('El origen vinculado conserva la identidad bancaria original y no admite otros destinos ni detalle. Modifique el depósito que representa el efectivo.'))
    from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
    def fingerprint(rows):
        # Browser JSON drops numeric trailing zeros (1623.0 becomes 1623).
        # Compare exact numeric values without rounding away evidence changes;
        # keep textual identifiers such as vouchers and TMOV/TDOC unchanged.
        numeric_fields = {'source_debit', 'source_credit', 'source_fx_rate', 'source_row'}
        return [{field: decimal_value(row.get(field)) if field in numeric_fields else str(row.get(field) or '')
                 for field in (*EVIDENCE_FIELDS, 'origin_complementary_item')} for row in rows or []]
    current_sources = fingerprint(doc.get('accounting_sources'))
    previous_sources = fingerprint(previous.get('accounting_sources') if previous else [])
    if current_sources != previous_sources:
        changes = []
        for index in range(max(len(current_sources), len(previous_sources))):
            if index >= len(previous_sources):
                changes.append(_('accounting_sources: línea {0} agregada.').format(index + 1))
            elif index >= len(current_sources):
                changes.append(_('accounting_sources: línea {0} eliminada.').format(index + 1))
            else:
                fields = [field for field in previous_sources[index]
                          if current_sources[index][field] != previous_sources[index][field]]
                if fields:
                    changes.append(_('Línea {0} de accounting_sources — campos modificados: {1}.').format(
                        index + 1, ', '.join(fields)))
        frappe.throw(_('Las líneas contables de origen son inmutables; use la importación o la revisión de identidad del depósito.')
                     + '<br><br>' + '<br>'.join(changes))
    if any(clean_text(doc.get(field)) != clean_text(previous.get(field) if previous else '') for field in IDENTITY_FIELDS):
        frappe.throw(_('La identidad del efectivo solo se modifica mediante Revisar origen del depósito.'))


def cash_needs_review(deposit):
    return deposit.get('cash_identity_status') == 'Revisar identidad'


@frappe.whitelist(methods=['POST'])
def link_duplicate_origin(deposit_name, canonical_name, modified, reason):
    """Explicitly archive a cash-free draft as another origin of a bank receipt.

    Original documents, files and row keys remain. No executions, balances,
    periods or cash amounts are rewritten; confirmed originals cannot be merged.
    """
    from credinomina_reconciliation.accounting_batch_store import creation_guard
    from credinomina_reconciliation.accounting_deposits import EVIDENCE_FIELDS
    if deposit_name == canonical_name or not clean_text(reason):
        frappe.throw(_('Seleccione otro depósito y documente por qué es el mismo efectivo.'))
    with creation_guard():
        documents = {name: frappe.get_doc('CN Remittance Allocation', name, for_update=True)
                     for name in sorted([deposit_name, canonical_name])}
        origin, canonical = documents[deposit_name], documents[canonical_name]
        if any(source.get('origin_complementary_item') for source in origin.get('accounting_sources') or []):
            frappe.throw(_('El depósito ya respalda movimientos internos. Conserve ese depósito y sus vínculos de origen.'))
        for document in documents.values():
            document.check_permission('write')
        if str(origin.modified) != str(modified):
            frappe.throw(_('El depósito cambió. Recárguelo antes de vincular su origen.'))
        if (origin.docstatus != 0 or origin.targets or origin.detail_rows or origin.detail_file
                or not origin.accounting_source_key or canonical.docstatus == 2
                or canonical.get('consolidated_deposit') or origin.get('consolidated_deposit')):
            frappe.throw(_('Solo se vincula un origen contable en borrador, sin detalle ni destinos, a un depósito vigente.'))
        if not canonical.accounting_source_key and canonical.docstatus != 1:
            frappe.throw(_('Confirme primero el depósito registrado manualmente. Hasta entonces el origen importado permanece en revisión y no se retira del control financiero.'))
        fields = ('employer', 'deposit_date', 'deposit_reference', 'deposit_currency', 'deposit_amount')
        if any(str(origin.get(field) or '') != str(canonical.get(field) or '') for field in fields):
            frappe.throw(_('Los datos de empresa, fecha, referencia, moneda, importe y comprobante deben corresponder al mismo depósito.'))
        if origin.deposit_voucher and canonical.deposit_voucher and origin.deposit_voucher != canonical.deposit_voucher:
            frappe.throw(_('Los comprobantes no coinciden. Verifique la identidad del depósito antes de vincular su origen.'))
        if origin.bank_account != canonical.bank_account and 'NO IDENTIFICADA' not in {origin.bank_account, canonical.bank_account}:
            frappe.throw(_('Las cuentas bancarias no coinciden. Verifique primero la cuenta del depósito.'))
        with source_writer():
            if not canonical.get('accounting_sources') and canonical.accounting_source_key:
                append_source(canonical, {field: canonical.get(field) for field in EVIDENCE_FIELDS}, check_cash=False)
            for source in origin.get('accounting_sources') or [origin]:
                append_source(canonical, {field: source.get(field) for field in EVIDENCE_FIELDS}, check_cash=False)
            canonical.save()
            origin.cash_identity_status = 'Origen vinculado'
            origin.consolidated_deposit = canonical.name
            origin.cash_identity_reason = clean_text(reason)
            origin.save()
        for document in documents.values():
            document.add_comment('Comment', _('Mismo efectivo: origen {0} vinculado a {1}. Motivo: {2}. Se conserva toda la evidencia contable; no se redistribuyó dinero.').format(origin.name, canonical.name, reason))
        return {'name': canonical.name, 'origin': origin.name}


@frappe.whitelist(methods=['POST'])
def verify_independent_deposit(deposit_name, modified, reason):
    """An explicit, audited human decision; never change bank or ledger amounts."""
    from credinomina_reconciliation.accounting_batch_store import creation_guard
    if not clean_text(reason):
        frappe.throw(_('Documente cómo verificó la identidad del depósito y su importe bancario.'))
    with creation_guard():
        document = frappe.get_doc('CN Remittance Allocation', deposit_name, for_update=True)
        document.check_permission('write')
        if str(document.modified) != str(modified) or document.docstatus == 2 or document.get('consolidated_deposit'):
            frappe.throw(_('El depósito cambió, está cancelado o su origen ya fue vinculado. Recárguelo.'))
        document._assert_open_related_periods()
        with source_writer():
            document.cash_identity_status = 'Depósito independiente verificado'
            document.cash_identity_reason = clean_text(reason)
            document.save()
        document.add_comment('Comment', _('Identidad del efectivo verificada por el usuario: {0}. No modifica importes ni distribuciones.').format(reason))
        return {'name': document.name}
