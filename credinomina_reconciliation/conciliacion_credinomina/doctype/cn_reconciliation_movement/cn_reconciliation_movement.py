from frappe.model.document import Document


class CNReconciliationMovement(Document):
    """Audit record only. No GL or core-loan posting is performed here."""

