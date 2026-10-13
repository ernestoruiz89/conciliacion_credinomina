"""Read-only deposit financial labels; persisted results remain audit evidence."""
from credinomina_reconciliation.rounding import money


SETTLED_RESULTS = {'Conciliado', 'Conciliado con saldo a favor', 'Conciliado con saldo a favor del cliente',
                   'Saldo a favor documentado', 'Parcial con saldo a favor'}
REVIEW_RESULTS = {'Revisar detalle', 'Revisar destinos', 'Detalle pendiente'}


def reconciliation_status(deposit):
    if deposit.get('cash_identity_status') == 'Origen vinculado':
        return 'Origen contable vinculado'
    if deposit.get('cash_identity_status') == 'Revisar identidad':
        return 'Revisar identidad del depósito'
    if deposit.get('docstatus') == 2:
        return 'No participa'
    if deposit.get('docstatus') != 1:
        return 'Sin conciliar'
    recorded = deposit.get('result') or 'Pendiente'
    # Old projections without monetary fields cannot prove a new financial state.
    if deposit.get('amount_usd') is None:
        return recorded
    total, assigned, credit = (money(deposit.get(field)) for field in
                              ('amount_usd', 'allocated_usd', 'justified_surplus_usd'))
    if total <= 0 or assigned < 0 or credit < 0 or assigned + credit > total:
        return 'Por revisar' if recorded in SETTLED_RESULTS else 'Revisar distribución'
    review = str(deposit.get('detail_status') or '').startswith('Revisar') or recorded in REVIEW_RESULTS
    settled_result = recorded in SETTLED_RESULTS
    if settled_result and (review or total != assigned + credit or money(deposit.get('unclassified_usd'))):
        return 'Por revisar'
    if settled_result:
        return 'Conciliado con saldo a favor' if credit else 'Conciliado'
    return recorded
