"""Read-only SQL checks for the operational review; isolated site only."""
import frappe
from credinomina_reconciliation.company_statement import load_detail, summarize
from credinomina_reconciliation.control_consultation import read_consultation


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    full = load_detail({})
    companies = sorted({row['employer'] for row in full if row.get('employer')})
    comparisons = 0
    for employer in companies:
        selected = load_detail({'employer': employer})
        assert selected == [row for row in full if row['employer'] == employer], employer
        for component in ('application_pending_usd', 'core_pending_usd', 'company_credit_usd',
                          'client_credit_usd', 'deposit_pending_usd', 'company_receivable_usd'):
            detail = load_detail({'employer': employer, 'component': component})
            assert detail == [row for row in selected if row['_component'] == component], (employer, component)
            comparisons += 1
    meta = frappe.get_meta('CN Complementary Item')
    assert [field.label for field in meta.fields if field.fieldtype == 'Tab Break'] == [
        '1. Partida y saldo', '2. Tratamiento y gestión', '3. Registro y evidencia']
    assert meta.get_field('credit_detail_row').hidden
    calls = []
    def build():
        calls.append(True)
        return {'rows': [dict(name='synthetic-check')]}
    data, first = read_consultation(None, {'scope': 'smoke'}, build)
    assert read_consultation(first['consultation'], {'scope': 'smoke'}, build)[0] == data
    assert len(calls) == 1
    return dict(companies=len(companies), component_comparisons=comparisons,
                summary_rows=len(summarize(full)), same_snapshot_reads=len(calls))
