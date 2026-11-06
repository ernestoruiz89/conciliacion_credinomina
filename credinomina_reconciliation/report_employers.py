"""Optional, direct payer scope for reports; record permissions still apply."""
from frappe.utils import cint

from credinomina_reconciliation.paying_employers import allowed_employers


def selected_employers(filters):
    employer = filters.get('employer')
    if employer and cint(filters.get('include_paying_companies')):
        return sorted(allowed_employers(employer))
    return [employer] if employer else []


def company_scopes(filters):
    companies = selected_employers(filters)
    return [dict(filters, employer=company, include_paying_companies=0) for company in companies] or [dict(filters)]
