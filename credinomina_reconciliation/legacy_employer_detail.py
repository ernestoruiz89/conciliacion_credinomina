"""Immutable legacy evidence. Never replay employer deductions into cash balances."""
import frappe
from frappe import _
from frappe.utils import now_datetime

PARENT_FIELDS = (
    "application_basis", "employer_response_file", "employer_response_import_key",
    "deduction_evidence_date", "deduction_basis", "deducted_usd", "deducted_nio",
    "deduction_recognition_deposit", "deduction_recognition_reference",
    "deduction_recognition_note", "deduction_recognition_on", "deduction_recognition_by",
)
ROW_FIELDS = ("deducted_usd", "deducted_nio", "deduction_status", "deduction_currency",
              "deduction_evidence_date", "deduction_match_note")


def archive_values(period):
    rows = period.get("collection_rows") or []
    stale_proposals = any(p.get("state") != "Materializado" and p.get("basis") != "Cobranza"
                          for p in period.get("provisional_adjustments") or [])
    evidence = (stale_proposals or any(period.get(key) for key in PARENT_FIELDS if key != "application_basis")
                or period.get("application_basis") == "Detalle de empresa"
                or any(any(row.get(key) for key in ROW_FIELDS if key != "deduction_status")
                       or row.get("deduction_status") not in (None, "", "Pendiente de detalle") for row in rows))
    if not evidence:
        return None
    review = []
    if period.get("application_basis") == "Detalle de empresa":
        review.append("La primera conciliación anterior utilizó detalle de empresa. Revise el control contra cobranza al reprocesar un período abierto.")
    if any(row.get("deduction_status") in ("Importes inconsistentes", "Ambigua", "Sin coincidencia") for row in rows):
        review.append("La evidencia anterior contiene filas pendientes de aclaración; se conservan sin corregirlas automáticamente.")
    if stale_proposals:
        review.append("Existen propuestas no materializadas con base anterior: regenérelas y apruébelas antes de usarlas en un depósito.")
    return dict(version=1, captured_on=str(now_datetime()), review=review,
                note="Auditoría: no participa en las conciliaciones nuevas. Los archivos no se trasladaron a depósitos.",
                parent={key: period.get(key) for key in PARENT_FIELDS},
                rows=[{key: row.get(key) for key in ("name", "row_key", "client_name", "loan_number", *ROW_FIELDS)}
                      for row in rows])


def guard_legacy_evidence(period):
    previous = period.get_doc_before_save()
    before = {row.name: row for row in previous.get("collection_rows") or []} if previous else {}
    for row in period.get("collection_rows") or []:
        if row.name not in before and any(row.get(key) for key in ROW_FIELDS if key != "deduction_status"):
            frappe.throw(_("Cargue las deducciones únicamente en Distribución de Depósito."))
        if row.name not in before and row.get("deduction_status") not in (None, "", "Pendiente de detalle"):
            frappe.throw(_("No se admite detalle de empresa nuevo en el período."))
    if not previous:
        if (any(period.get(key) for key in PARENT_FIELDS if key != "application_basis")
                or period.get("legacy_employer_detail")
                or period.get("application_basis") not in (None, "", "Cobranza")):
            frappe.throw(_("Cargue el detalle de la empresa únicamente en Distribución de Depósito."))
        return
    if (period.get("legacy_employer_detail") or "") != (previous.get("legacy_employer_detail") or ""):
        frappe.throw(_("La evidencia anterior es de solo lectura."))
    # Totals are retained physically for old documents, never used as capacity.
    for field in PARENT_FIELDS:
        if str(period.get(field) or "") != str(previous.get(field) or ""):
            frappe.throw(_("El detalle anterior es de solo lectura. Use Distribución de Depósito."))
    for row in period.get("collection_rows") or []:
        old = before.get(row.name)
        if old and any(str(row.get(key) or "") != str(old.get(key) or "") for key in ROW_FIELDS):
            frappe.throw(_("Las deducciones anteriores se conservan únicamente para auditoría."))
