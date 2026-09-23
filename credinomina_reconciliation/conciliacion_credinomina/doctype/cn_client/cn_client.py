import frappe
from frappe import _
from frappe.model.document import Document

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


class CNClient(Document):
    def validate(self):
        self.client_name = clean_text(self.client_name)
        self.client_number = clean_text(self.client_number)
        self.employee_number = clean_text(self.employee_number)
        self.national_id = clean_text(self.national_id)
        if not self.employer:
            frappe.throw(_("Seleccione la empresa de convenio del cliente."))
        if not self.client_name:
            frappe.throw(_("Indique el nombre del cliente."))
        for fieldname, label in (
            ("client_number", "número de cliente"),
            ("national_id", "cédula"),
        ):
            value = canonical_identifier(self.get(fieldname))
            if not value:
                continue
            others = frappe.get_all(
                "CN Client", fields=["name", fieldname], limit_page_length=100000,
            )
            if any(
                row.name != self.name
                and canonical_identifier(row.get(fieldname)) == value
                for row in others
            ):
                frappe.throw(_("Ya existe otro cliente con este {0}.").format(label))
        employee_number = canonical_identifier(self.employee_number)
        if employee_number:
            others = frappe.get_all(
                "CN Client", filters={"employer": self.employer},
                fields=["name", "employee_number"], limit_page_length=100000,
            )
            if any(
                row.name != self.name
                and canonical_identifier(row.employee_number) == employee_number
                for row in others
            ):
                frappe.throw(_("Ya existe otro cliente con este número de empleado en la empresa."))
        seen = {name_key(self.client_name)}
        for alias in self.aliases or []:
            alias.alias_name = clean_text(alias.alias_name)
            key = name_key(alias.alias_name)
            if not key or key in seen:
                frappe.throw(_("Los alias deben ser nombres distintos y no vacíos."))
            seen.add(key)
