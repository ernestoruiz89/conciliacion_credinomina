"""Short-lived, user-bound read snapshots for control pagination, never commands.

Balances belong to the displayed consultation time. Refresh starts a new read;
expiry, scope changes or changed readable parents restart at the first page.
"""
from hashlib import sha256
import json
import re
from uuid import uuid4

import frappe
from frappe.utils import now_datetime

TTL = 180
PARENTS = ('CN Reconciliation Period', 'CN Accounting Import',
           'CN Remittance Allocation', 'CN Complementary Item', 'CN Reconciliation Exception')


def visibility_signature():
    """Recheck row-level access on every page, not just the user's role names.

    Names and modification timestamps are small compared with the financial
    journals. Parent changes also invalidate ordinary stale consultations.
    """
    visibility = {}
    for doctype in PARENTS:
        visibility[doctype] = (frappe.get_list(doctype, fields=['name', 'modified'],
            order_by='name asc', limit_page_length=0)
            if frappe.has_permission(doctype, 'read') else None)
    return sha256(json.dumps(visibility, default=str, sort_keys=True).encode()).hexdigest()


def read_consultation(token, scope, builder):
    user = frappe.session.user
    signature = visibility_signature()
    cache = frappe.cache
    previous = None
    if token and re.fullmatch(r'[a-f0-9]{32}', str(token)):
        previous = cache.get_value(f'cn-control-consultation:{user}:{token}')
    if (previous and previous['user'] == user and previous['scope'] == scope
            and previous['visibility'] == signature):
        return previous['data'], dict(consultation=token, consulted_at=previous['created'], restarted=False)
    data = builder()
    token = uuid4().hex
    created = str(now_datetime())
    cache.set_value(f'cn-control-consultation:{user}:{token}', dict(user=user,
        scope=scope, visibility=signature, data=data, created=created), expires_in_sec=TTL)
    return data, dict(consultation=token, consulted_at=created, restarted=True)
