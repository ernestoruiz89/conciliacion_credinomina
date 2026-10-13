"""Keep classification signs separate from the immutable ledger magnitude."""
import frappe
from frappe import _

from credinomina_reconciliation.rounding import decimal_value, money


def ledger_magnitude(item):
    if not item.get('accounting_source_key'):
        return None
    net = abs(money(item.get('source_debit')) - money(item.get('source_credit')))
    if not net:
        return None
    if item.get('source_currency') == 'USD':
        return net
    rate = decimal_value(item.get('source_fx_rate'))
    if item.get('source_currency') == 'NIO' and rate > 0:
        return money(net / rate)
    return None


def validate_imported_magnitude(item, previous=None):
    if not item.get('accounting_source_key'):
        return
    expected = ledger_magnitude(item)
    if expected is None and previous:
        # Older evidence without a usable FX rate still cannot shrink on edit.
        expected = abs(money(previous.get('amount_usd')))
    if expected is not None and abs(money(item.get('amount_usd'))) != expected:
        frappe.throw(_(
            'La partida importada debe conservar su magnitud contable de {0} US$. '
            'Puede revisar el signo, pero no reducir ni aumentar el importe. '
            'Distribuya o compense parcialmente para conservar el remanente; '
            'si la evidencia es incorrecta, revise el archivo de origen.'
        ).format(expected))
