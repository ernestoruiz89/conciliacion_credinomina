"""Read-only acceptance against saved complementary items on the isolated site."""
import frappe

from credinomina_reconciliation.client_position import load_position, summary
from credinomina_reconciliation.complementary_balances import get_list_balances
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo.estado_de_cuenta_operativo import execute
from credinomina_reconciliation.rounding import money


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    filters = {'position_type': 'Partida complementaria'}
    rows = load_position([], filters)
    assert rows, 'Use the existing isolated UI acceptance data.'
    listed = get_list_balances([row['source_document'] for row in rows])
    for row in rows:
        assert row['operational_status'] == listed[row['source_document']]['state']
        assert money(row['complementary_pending_usd']) == money(listed[row['source_document']]['pending_usd'])
    credits = [row for row in rows if row['treatment_status'] == 'Documentado']
    assert credits
    for row in credits:
        selected = {**filters, 'operational_status': 'Conciliado', 'category': row['category'],
                    'management_status': row['management_status'], 'employer': row['employer']}
        columns, found, message, _, figures = execute(selected)
        assert row['source_document'] in {entry['source_document'] for entry in found}
        assert 'Totales de la selección' in message
        assert not next(column for column in columns if column['fieldname'] == 'category').get('hidden')
        assert not any(figure['label'] == 'CxC por aplicaciones US$' for figure in figures)
    figures = {figure['label']: figure['value'] for figure in summary(rows, filters)}
    assert money(figures['Complementarias pendientes de conciliar US$']) == sum(
        (money(row['complementary_pending_usd']) for row in rows
         if row['operational_status'] != 'Revisar distribución' and money(row['complementary_pending_usd']) >= 0), money(0))
    assert money(figures['Saldos a favor por gestionar US$']) == sum(
        (money(row['credit_pending_usd']) for row in rows), money(0))
    return dict(saved_items=len(rows), documented_credits=len(credits), totals=figures)
