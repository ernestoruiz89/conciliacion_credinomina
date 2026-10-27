"""Core evidence creates movements; manual differences only describe them."""
import frappe
from frappe import _
from contextlib import contextmanager
from contextvars import ContextVar

_import_key = ContextVar("cn_complementary_import_key", default=None)


@contextmanager
def imported_item(key):
    token = _import_key.set(key)
    try:
        yield
    finally:
        _import_key.reset(token)


def guard_new_item(document):
    previous = document.get_doc_before_save()
    key = document.get("accounting_source_key")
    if key and (document.is_new() or previous and previous.get("accounting_source_key") != key):
        if _import_key.get() != key:
            frappe.throw(_("El origen contable solo puede establecerse desde la importación del core."))
    newly_confirmed = document.docstatus == 1 and (not previous or previous.docstatus != 1)
    if (document.is_new() or newly_confirmed) and not document.get("accounting_source_key"):
        frappe.throw(_("Las partidas nuevas deben proceder de movimientos importados del core. Use Clasificar / documentar diferencia; documentar no modifica saldos."))


def assert_no_legacy_tolerance(filters):
    items = frappe.get_all("CN Complementary Item", filters={
        **filters, "category": "Diferencia por tolerancia", "docstatus": 1, "status": "Vigente",
    }, pluck="name", limit_page_length=1)
    if items:
        frappe.throw(_("Esta conciliación contiene tolerancias históricas. Se conserva su distribución; revise el reporte Revisión de Partidas Manuales antes de reprocesarla. No se generan ni revierten tolerancias automáticamente."))
