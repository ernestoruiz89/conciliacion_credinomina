"""Narrow payroll candidates without changing application matching rules."""
from collections import defaultdict

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.collection_identity import collection_client_number
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier


class CollectionCandidateIndex:
    """Keep every possible identity match, in its original payroll order.

    A credit is mandatory when supplied. Without it, at least one supplied
    identifier must match; missing fields are allowed and conflicting fields
    are still rejected by application_matches_collection. Names/aliases are
    candidates only when the application has no identifiers.
    """

    def __init__(self, rows, aliases_by_target):
        self.rows = list(rows)
        self.credits = defaultdict(list)
        self.identifiers = defaultdict(set)
        self.names = defaultdict(set)
        for position, row in enumerate(self.rows):
            period = row.get("parent")
            credit = canonical_credit_number(row.get("loan_number"))
            if credit:
                self.credits[(period, credit)].append(position)
            for field, value in (
                ("client_number", collection_client_number(row)),
                ("employee_number", row.get("employee_number")),
                ("national_id", row.get("national_id")),
            ):
                key = canonical_identifier(value)
                if key:
                    self.identifiers[(period, field, key)].add(position)
            for label in (row.get("client_name"), *aliases_by_target.get(row.get("name"), ())):
                key = name_key(label)
                if key:
                    self.names[(period, key)].add(position)

    def candidates(self, period, application):
        credit = canonical_credit_number(application.get("loan_number"))
        if credit:
            positions = self.credits.get((period, credit), ())
        else:
            positions = set()
            has_identifier = False
            for field in ("client_number", "employee_number", "national_id"):
                key = canonical_identifier(application.get(field))
                if key:
                    has_identifier = True
                    positions.update(self.identifiers.get((period, field, key), ()))
            if not has_identifier:
                positions.update(self.names.get((period, name_key(application.get("client_name"))), ()))
            positions = sorted(positions)
        return [self.rows[position] for position in positions]
