"""Scoped, batched lookups that also find previously stored padded credits."""
import frappe
from frappe.query_builder.functions import CustomFunction, Lower

from credinomina_reconciliation.parsers import canonical_credit_number, credit_number_pattern


def get_credit_rows(doctype, *, filters, fields, loan_field, loans):
    loans = sorted({canonical_credit_number(loan) for loan in loans} - {""})
    if not loans:
        return []
    # get_all's supported filter operators differ between Frappe versions.
    # Use MariaDB's parameterized expression through the query builder instead.
    table = frappe.qb.DocType(doctype)
    query = frappe.qb.from_(table).select(*(table[field] for field in fields))
    for field, value in filters.items():
        if isinstance(value, (list, tuple)):
            operator, operand = value
            if operator == "in":
                condition = table[field].isin(operand)
            elif operator == "!=":
                condition = table[field] != operand
            else:
                raise ValueError(f"Unsupported credit lookup filter: {operator}")
        else:
            condition = table[field] == value
        query = query.where(condition)
    matches = CustomFunction("REGEXP_INSTR", ["value", "pattern"])
    rows = []
    for offset in range(0, len(loans), 500):
        pattern = credit_number_pattern(loans[offset:offset + 500])
        rows.extend(query.where(matches(Lower(table[loan_field]), pattern) > 0).run(as_dict=True))
    return rows
