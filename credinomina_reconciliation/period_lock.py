"""Server-only scope for controlled writes to a closed reconciliation period."""

from contextlib import contextmanager
from contextvars import ContextVar


_write_action = ContextVar("cn_period_write_action", default="")


def current_period_write_action():
    return _write_action.get()


def lock_period_companies(period_names=(), *, employers=()):
    """Serialize closure and exception changes before locking document rows.

    The cash generation check also rejects an earlier repeatable-read snapshot.
    Informational origin_period links are deliberately outside this scope.
    """
    import frappe
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies

    names = sorted(set(period_names) - {None, ''})
    owners = set(employers) - {None, ''}
    if names:
        owners.update(row.employer for row in frappe.get_all('CN Reconciliation Period',
            filters={'name': ['in', names]}, fields=['employer'], limit_page_length=0) if row.employer)
    if owners:
        lock_cash_pool({company for owner in owners for company in reconciliation_companies(owner)})


@contextmanager
def period_write_action(action):
    token = _write_action.set(action)
    try:
        yield
    finally:
        _write_action.reset(token)
