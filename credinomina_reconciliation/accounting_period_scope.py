"""Limit an import's reconciliation action to its selected default period."""
import frappe
from frappe import _

from credinomina_reconciliation.deposit_reconciliation import entries


def load_imports(period):
    from credinomina_reconciliation.complementary_cancellation import _load_imports
    return _load_imports([period], [], [], {period.employer})


def selected_rows(document, period_name):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    selected = []
    for row in document.rows:
        if row.event_type != "Aplicacion":
            continue
        assigned = row.historical_period or document.historical_period
        links = set(engine._source_linked_periods(row))
        if assigned != period_name and period_name not in links:
            continue
        if links - {period_name} or (assigned and assigned != period_name):
            frappe.throw(_("La aplicación {0} está vinculada a varios períodos. Revise sus vínculos antes de conciliar únicamente {1}.").format(row.name, period_name))
        selected.append(row)
    return selected


def related_cash(periods, rows, items):
    from credinomina_reconciliation.complementary_cancellation import _related_cash
    deposits = {deposit.name: deposit for deposit in _related_cash([], periods, rows, items)}
    evidence = {period.get(field) for period in periods
                for field in ("deduction_recognition_deposit", "prepared_deposit")} - {None, ""}
    for name in sorted(evidence - set(deposits)):
        deposit = frappe.get_doc("CN Remittance Allocation", name)
        if deposit.docstatus == 1:
            deposits[name] = deposit
    return list(deposits.values())


def cash_evidence_periods(periods, deposits):
    # A single deposit may pay other periods. Read their row keys to validate
    # its full stored distribution, but never recalculate/save those periods.
    result = list(periods)
    known = {period.name for period in periods}
    names = {entry.get("periodo") for deposit in deposits for entry in entries(deposit.allocation_detail)
             if entry.get("tipo") == "Cobranza"} - known - {None, ""}
    for name in sorted(names):
        result.append(frappe.get_doc("CN Reconciliation Period", name))
    return result
