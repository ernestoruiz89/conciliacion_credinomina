"""Evidence-based bank/core adjustments, presented outside the management balance."""
from collections import defaultdict

from frappe.utils import getdate
from credinomina_reconciliation.rounding import money, money_float, decimal_value

ADJUSTMENT_FIELD = 'deposit_documentation_adjustment_usd'
CALCULATED_FIELD = 'calculated_balance_usd'
CONTROL_FIELDS = {ADJUSTMENT_FIELD, CALCULATED_FIELD}


def project(core, cutoff_date=None):
    from credinomina_reconciliation.company_accounting_statement import FIELDS, MANAGEMENT

    groups = defaultdict(list)
    for line in core:
        deposit = line.get('_bank_deposit')
        if deposit:
            groups[deposit['name']].append(line)
    result = []
    for name, lines in groups.items():
        deposit = lines[0]['_bank_deposit']
        when = deposit.get('deposit_date')
        if (not when or deposit.get('docstatus') == 2 or deposit.get('cash_identity_status') == 'Origen vinculado'
                or (deposit.get('docstatus') != 1 and not deposit.get('accounting_source_key') and not deposit.get('linked_core_deposit'))
                or (cutoff_date and getdate(when) > getdate(cutoff_date))):
            continue
        values = [line.get('core_balance_usd') for line in lines]
        original = None if any(value is None for value in values) else sum((money(value) for value in values), money(0))
        bank = deposit.get('amount_usd')
        adjustment = None if original is None or bank is None else money(bank) + original
        if adjustment == 0:
            continue
        nio = [line.get('core_balance_nio') for line in lines]
        nio = None if any(value is None for value in nio) else sum((money(value) for value in nio), money(0))
        rate = decimal_value(deposit.get('fx_rate'))
        rounding = (deposit.get('deposit_currency') == 'NIO' and deposit.get('deposit_amount') is not None
            and nio is not None and -nio == money(deposit['deposit_amount']) and rate > 0 and bank is not None
            and money(bank) == money(-nio / rate)
            and all(line.get('source_currency') == 'NIO' and decimal_value(line.get('fx_rate')) == rate for line in lines))
        result.append(dict(employer=deposit.get('employer'), usd_currency='USD', nio_currency='NIO',
            statement_scope=MANAGEMENT, _component=None, _is_deposit_adjustment=True,
            event_date=when, source_document=name, source_doctype='CN Remittance Allocation',
            position_type='Redondeo de conversión del depósito' if rounding else 'Ajuste de depósito por documentar',
            origin_type='Diferencia de depósito', client_name=deposit.get('employer'),
            category='Ajuste de depósito en conciliación', reference=deposit.get('deposit_reference'),
            status='Redondeo verificado' if rounding else 'Pendiente de documentar',
            accounting_status='No requiere registro' if rounding else 'Verificar contrapartida', accounting_pending=False,
            observation=('Diferencia entre la conversión del total y la suma de líneas del core; no requiere asiento.' if rounding else
                'Diferencia entre importe bancario y origen contable. Revise el comprobante completo y documente la contrapartida.'),
            bank_amount_usd=bank, original_core_deposit_usd=None if original is None else money_float(-original),
            management_amount_usd=0.0, **{field: 0.0 for field in FIELDS},
            **{ADJUSTMENT_FIELD: None if adjustment is None else money_float(adjustment)}))
    return result
