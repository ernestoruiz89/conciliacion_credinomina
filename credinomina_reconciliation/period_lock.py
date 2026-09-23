"""Server-only scope for controlled writes to a closed reconciliation period."""

from contextlib import contextmanager
from contextvars import ContextVar


_write_action = ContextVar("cn_period_write_action", default="")


def current_period_write_action():
    return _write_action.get()


@contextmanager
def period_write_action(action):
    token = _write_action.set(action)
    try:
        yield
    finally:
        _write_action.reset(token)
