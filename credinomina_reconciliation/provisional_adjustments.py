"""Reviewed first-stage differences. No cash effect until an explicit deposit transfer."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from credinomina_reconciliation.application_quality import collection_quality, PENDING_DEDUCTION, COLLECTION, EMPLOYER_DETAIL
from credinomina_reconciliation.rounding import money, money_float, sum_money

_trusted = ContextVar("cn_provisional_write", default=False)
_materializing = ContextVar("cn_provisional_materializing", default=False)
CATEGORIES = {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación",
              "Saldo a favor del cliente", "Saldo a favor de la empresa"}
CREDITS = {"Saldo a favor del cliente", "Saldo a favor de la empresa"}
CLASSIFICATION = ("category", "subcategory", "reason_type", "description", "assigned_to",
                  "commitment_date", "existing_item")
PROTECTED = ("collection_row", "client_name", "client_number", "loan_number", "basis",
             "base_usd", "applied_usd", "amount_usd", "fingerprint", "state", "approved_by",
             "approved_on", "complementary_item", "deposit")


@contextmanager
def provisional_write():
    token = _trusted.set(True)
    try:
        yield
    finally:
        _trusted.reset(token)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def row_basis(period, row):
    # Exclude real complements from the base: proposals explain the original
    # base minus core applications, not an already adjusted/double-netted value.
    values = dict(row.as_dict()) if callable(getattr(row, "as_dict", None)) else dict(row)
    values["complementary_usd"] = 0
    quality = collection_quality(values, period.get("application_basis"))
    if quality["quality_expected_usd"] is None:
        frappe.throw(_("Revise la moneda o el detalle de la fila {0} antes de preparar ajustes.").format(row.get("idx")))
    base, applied = money(quality["quality_expected_usd"]), money(row.get("applied_usd"))
    if base < 0 or applied < 0:
        frappe.throw(_("La base y lo aplicado no pueden ser negativos."))
    context = {key: row.get(key) for key in (
        "name", "row_key", "client", "client_number", "client_name", "national_id", "loan_number",
        "installment_number", "expected_usd", "expected_nio", "deducted_usd", "deducted_nio",
        "deduction_status", "application_reference")}
    # Recognition is evidence of this deposit, not a new first-stage base.
    if period.get("application_basis") == COLLECTION:
        context.update(deduction_status="Cobranza", deducted_usd=0, deducted_nio=0)
    for field in ("expected_usd", "expected_nio", "deducted_usd", "deducted_nio"):
        context[field] = str(money(context[field]))
    for field in set(context) - {"expected_usd", "expected_nio", "deducted_usd", "deducted_nio"}:
        context[field] = str(context[field] or "")
    context.update(employer=period.get("employer"), base=str(base), applied=str(applied),
                   application_basis=period.get("application_basis"),
                   collection_file=period.get("collection_import_sha256"),
                   response_file=period.get("employer_response_import_key") if period.get("application_basis") == EMPLOYER_DETAIL else None)
    return {"collection_row": row.get("name"), "client_name": row.get("client_name"),
            "client_number": row.get("client_number"), "loan_number": row.get("loan_number"),
            "basis": quality["quality_basis"], "base_usd": money_float(base),
            "applied_usd": money_float(applied), "amount_usd": money_float(base - applied),
            "fingerprint": digest(context)}


def guard_period(period):
    """Read-only fields are server-protected; classification edits revoke approval."""
    if _trusted.get():
        return
    previous = period.get_doc_before_save()
    if (previous and previous.get("prepared_deposit") and
        period.get("application_basis") != previous.get("application_basis")):
        frappe.throw(_("La base ya fue trasladada a un depósito. Conserve esa evidencia y corrija mediante las partidas reales vinculadas."))
    if (period.get("prepared_deposit") or "") != (previous.get("prepared_deposit") or "" if previous else ""):
        frappe.throw(_("El depósito de la conciliación preparada solo se vincula desde su acción de traslado."))
    before = {row.name: row for row in (previous.get("provisional_adjustments") or [])} if previous else {}
    current = list(period.get("provisional_adjustments") or [])
    rows = {row.name: row for row in period.get("collection_rows") or []}
    for proposal in current:
        old = before.get(proposal.name)
        def same(field):
            if field in {"base_usd", "applied_usd", "amount_usd"}:
                return money(proposal.get(field)) == money(old.get(field))
            return str(proposal.get(field) or "") == str(old.get(field) or "")
        if not old or any(not same(key) for key in PROTECTED):
            frappe.throw(_("Use las acciones de ajustes provisionales para generar, aprobar o materializar filas."))
        changed = any(str(proposal.get(key) or "") != str(old.get(key) or "") for key in CLASSIFICATION)
        row = rows.get(proposal.collection_row)
        stale = (not row or collection_quality(row, period.get("application_basis"))["quality_expected_usd"] is None
                 or row_basis(period, row)["fingerprint"] != proposal.fingerprint)
        if old.state == "Materializado":
            if changed:
                frappe.throw(_("Corrija la partida real vinculada; no reescriba un ajuste materializado."))
            continue  # Later changes are handled by the real cash/period guards.
        if changed or stale:
            proposal.state = "Pendiente de revisión"
            proposal.approved_by = proposal.approved_on = None
    removed = set(before) - {row.name for row in current}
    if removed:
        frappe.throw(_("Use Generar ajustes provisionales para actualizar la tabla sin perder trazabilidad."))


def _load_period(name):
    period = frappe.get_doc("CN Reconciliation Period", name, for_update=True)
    period.check_permission("write")
    if period.status == "Cerrado" or period.reconciliation_mode == "Historica":
        frappe.throw(_("Seleccione un período operativo abierto."))
    if not period.collection_rows:
        frappe.throw(_("Cargue primero la cobranza del período."))
    return period


def _lock_period_pool(name):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    employer = frappe.db.get_value("CN Reconciliation Period", name, "employer")
    lock_cash_pool(reconciliation_companies(employer))


@frappe.whitelist(methods=["POST"])
def generate_proposals(period_name):
    _lock_period_pool(period_name)
    period = _load_period(period_name)
    if period.get("prepared_deposit") or any(row.state == "Materializado" for row in period.get("provisional_adjustments") or []):
        frappe.throw(_("Este período ya trasladó ajustes a un depósito. Revise las partidas reales vinculadas."))
    previous = {row.collection_row: row for row in period.get("provisional_adjustments") or []}
    rows = []
    for collection in period.collection_rows:
        values = row_basis(period, collection)
        if not money(values["amount_usd"]):
            continue
        old = previous.get(collection.name)
        if old and old.fingerprint == values["fingerprint"]:
            rows.append(old.as_dict())
        else:
            classification = {key: old.get(key) for key in CLASSIFICATION} if old else {}
            rows.append({**values, **classification, "state": "Pendiente de revisión"})
    with provisional_write():
        period.set("provisional_adjustments", rows)
        period.save()
    period.add_comment("Comment", _("Ajustes provisionales actualizados: {0}. Sin efecto en saldos.").format(len(rows)))
    return {"rows": len(rows)}


def validate_classification(proposal):
    if proposal.category not in CATEGORIES or not (proposal.description or "").strip():
        frappe.throw(_("Clasifique y describa cada ajuste provisional antes de aprobarlo."))
    if proposal.category == "Ajuste de conciliación":
        effect = frappe.db.get_value("CN Complementary Subcategory", proposal.subcategory, "effect") if proposal.subcategory else None
        if not effect or effect == "Por clasificar":
            frappe.throw(_("Seleccione una subcategoría definitiva para el ajuste."))
        if effect == "CxC a la empresa" and money(proposal.amount_usd) >= 0:
            frappe.throw(_("Una CxC de ajuste requiere un importe negativo."))
    if proposal.category in CREDITS:
        if money(proposal.amount_usd) <= 0 or not proposal.reason_type or not proposal.assigned_to or not proposal.commitment_date:
            frappe.throw(_("Para un saldo a favor indique importe positivo, motivo, responsable y fecha compromiso."))


@frappe.whitelist(methods=["POST"])
def approve_proposals(period_name):
    _lock_period_pool(period_name)
    period = _load_period(period_name)
    collections = {row.name: row for row in period.collection_rows}
    with provisional_write():
        for row in period.get("provisional_adjustments") or []:
            if row.state == "Materializado":
                continue
            source = collections.get(row.collection_row)
            if not source or row_basis(period, source)["fingerprint"] != row.fingerprint:
                frappe.throw(_("La base cambió. Genere nuevamente los ajustes antes de aprobar."))
            validate_classification(row)
            row.state, row.approved_by, row.approved_on = "Aprobado", frappe.session.user, now_datetime()
        period.save()
    period.add_comment("Comment", _("Ajustes provisionales aprobados. No se han creado partidas ni aplicado dinero."))
    return {"approved": len(period.get("provisional_adjustments") or [])}


def is_materializing():
    return _trusted.get() or _materializing.get()


@contextmanager
def defer_deposit_reconciliation():
    """Defer per-item deposit rebuilds while confirming a batch of credits."""
    token = _materializing.set(True)
    try:
        yield
    finally:
        _materializing.reset(token)


def explicit_complement_rows(periods):
    """Exact row links survive repeated client/credit combinations in a payroll."""
    return {proposal.complementary_item: proposal.collection_row
            for period in periods for proposal in period.get("provisional_adjustments") or []
            if proposal.get("complementary_item") and proposal.get("state") == "Materializado"}


def _deposit_context(name):
    from credinomina_reconciliation.client_credit import lock_credit_deposit
    from credinomina_reconciliation.remittance_periods import selected_periods
    from credinomina_reconciliation.paying_employers import allowed_employers
    document = lock_credit_deposit(name)
    document.check_permission("write")
    if document.docstatus != 1:
        frappe.throw(_("Confirme primero el depósito. Confirmarlo no materializa los ajustes."))
    names = selected_periods(document)
    if not names:
        frappe.throw(_("Seleccione los períodos a conciliar en el depósito."))
    periods = [_load_period(name) for name in sorted(names)]
    if any(period.employer not in allowed_employers(document.employer) for period in periods):
        frappe.throw(_("Hay períodos de empresas no autorizadas por la pagadora."))
    return document, periods


def _verified_rows(periods):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    rows = {row.name: row for period in periods for row in period.collection_rows}
    applied = {name: money(0) for name in rows}
    evidence = []
    for imported in engine.load_scoped_imports({period.employer for period in periods}):
        for source in imported.rows:
            if source.event_type != "Aplicacion" or not source.effective or source.match_status not in engine.LINKED_APPLICATION_STATUSES:
                continue
            for link in engine._application_allocations(source):
                if link["collection_row_id"] in applied:
                    applied[link["collection_row_id"]] += money(link["amount_usd"])
                    evidence.append([source.name, link, source.get("modified")])
    if any(money(row.applied_usd) != applied[name] for name, row in rows.items()):
        frappe.throw(_("Las aplicaciones cambiaron. Actualice la conciliación de la empresa y vuelva a revisar el período."))
    return evidence


def _plan(document, periods):
    from credinomina_reconciliation.client_credit import load_credits
    if any(period.get("prepared_deposit") for period in periods):
        frappe.throw(_("Un período ya fue trasladado a un depósito. Use sus partidas reales; no genere una segunda distribución provisional."))
    if document.targets or document.detail_rows or money(document.allocated_usd) or load_credits([document.name]):
        frappe.throw(_("El traslado preparado requiere un depósito sin detalle, destinos ni saldos a favor previos. No se reemplazará su trabajo."))
    evidence = _verified_rows(periods)
    result = {"deposit": document.name, "modified": str(document.modified), "rows": [], "adjustments": [],
              "periods": [period.name for period in periods], "applications": evidence}
    existing = set()
    for period in periods:
        proposals = {row.collection_row: row for row in period.get("provisional_adjustments") or []}
        if len(proposals) != len(period.get("provisional_adjustments") or []):
            frappe.throw(_("No repita una fila en los ajustes provisionales."))
        known = {row.name for row in period.collection_rows}
        if set(proposals) - known:
            frappe.throw(_("Hay ajustes de filas retiradas. Genere nuevamente la tabla."))
        for row in period.collection_rows:
            base = row_basis(period, row)
            if money(row.remitted_usd) or row.get("remittance_detail") not in (None, "", "[]"):
                frappe.throw(_("La fila {0} ya tiene depósitos asignados. Use el flujo de partidas pendientes para distribuciones parciales.").format(row.idx))
            if not row.get("client") or not row.loan_number or not row.row_key:
                frappe.throw(_("Identifique cliente y crédito en todas las filas antes de trasladar la conciliación."))
            if not money(base["base_usd"]) and money(base["applied_usd"]):
                frappe.throw(_("Una aplicación sin importe en la base requiere revisión y distribución manual."))
            proposal = proposals.get(row.name)
            if money(base["amount_usd"]):
                if not proposal or proposal.state != "Aprobado" or proposal.fingerprint != base["fingerprint"]:
                    frappe.throw(_("Genere, clasifique y apruebe las diferencias vigentes del período {0}.").format(period.name))
                validate_classification(proposal)
                if proposal.existing_item:
                    if proposal.existing_item in existing:
                        frappe.throw(_("Una partida existente no puede usarse dos veces."))
                    existing.add(proposal.existing_item)
                    _existing_item(proposal, period, row)
                result["adjustments"].append({"period": period.name, **{key: proposal.get(key) for key in
                    ("name", *CLASSIFICATION, *PROTECTED)}})
            elif proposal:
                frappe.throw(_("La diferencia ya no existe. Genere nuevamente los ajustes."))
            result["rows"].append({**base, "period": period.name, "employer": period.employer,
                                   "client": row.client, "row_key": row.row_key})
    result["total_usd"] = money_float(sum_money(row["base_usd"] for row in result["rows"]))
    result["applied_usd"] = money_float(sum_money(row["applied_usd"] for row in result["rows"]))
    result["adjustment_usd"] = money_float(sum_money(row["amount_usd"] for row in result["adjustments"]))
    result["deposit_usd"] = money_float(document.amount_usd)
    if money(result["total_usd"]) <= 0 or money(result["total_usd"]) != money(document.amount_usd):
        frappe.throw(_("El depósito (US$ {0}) no coincide con la base completa seleccionada (US$ {1}). Use la distribución habitual para pagos parciales o diferencias adicionales.").format(document.amount_usd, result["total_usd"]))
    result["fingerprint"] = digest(result)
    return result


def _existing_item(proposal, period, row):
    item = frappe.get_doc("CN Complementary Item", proposal.existing_item, for_update=True)
    item.check_permission("read")
    if (item.docstatus != 1 or not item.get("accounting_source_key") or item.category in CREDITS
        or item.category != proposal.category or item.employer != period.employer or item.period != period.name
        or item.loan_number != row.loan_number or item.client_number != row.client_number
        or money(item.amount_usd) != money(proposal.amount_usd)):
        frappe.throw(_("La partida del core debe estar confirmada, clasificada y vinculada al mismo período, cliente, crédito e importe."))
    # Planned and applied links both block reuse; they belong to another workflow.
    if frappe.db.exists("CN Remittance Target", {"complementary_item": item.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida existente ya tiene un destino. Revíselo antes de reutilizarla."))
    if frappe.db.exists("CN Provisional Adjustment", {"complementary_item": item.name, "state": "Materializado"}):
        frappe.throw(_("La partida existente ya fue trasladada desde otra propuesta."))
    if money(item.get("compensated_usd")) or item.get("related_application"):
        frappe.throw(_("La partida existente ya tiene una compensación o aplicación vinculada."))
    return item


@frappe.whitelist(methods=["POST"])
def preview_transfer(remittance_name):
    return _plan(*_deposit_context(remittance_name))


@frappe.whitelist(methods=["POST"])
def apply_transfer(remittance_name, fingerprint, confirm_correspondence=False):
    from frappe.utils import cint
    from frappe.utils.file_manager import save_file
    from credinomina_reconciliation.application_deposit_detail import _workbook
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail
    if not cint(confirm_correspondence):
        frappe.throw(_("Confirme que el depósito corresponde a esta cobranza/deducción, no solo que coincide el importe."))
    document, periods = _deposit_context(remittance_name)
    # Safe replay of a successful double-click: never manufacture a second item.
    if all(period.get("prepared_deposit") == document.name for period in periods):
        return {"deposit": document.name, "result": document.result, "already_applied": True}
    plan = _plan(document, periods)
    if fingerprint != plan["fingerprint"]:
        frappe.throw(_("Los datos cambiaron. Abra otra vez la vista previa; no se creó ninguna partida."))
    if plan["adjustments"] and not all(frappe.has_permission("CN Complementary Item", action) for action in ("create", "submit")):
        frappe.throw(_("Necesita permisos para crear y confirmar partidas complementarias."), frappe.PermissionError)
    with provisional_write():
        records = []
        for period in periods:
            for row in period.collection_rows:
                base = row_basis(period, row)
                proposal = next((p for p in period.get("provisional_adjustments") or [] if p.collection_row == row.name), None)
                detail_amount = money(base["base_usd"])
                if proposal and proposal.category == "Saldo a favor de la empresa":
                    detail_amount -= money(proposal.amount_usd)
                records.append({key: row.get(key) for key in ("client_number", "client_name", "national_id", "loan_number", "installment_number")}
                    | {"source_row": len(records) + 2, "row_key": row.row_key, "employer": period.employer,
                       "deducted_usd": money_float(detail_amount), "comments": f"Base preparada: {period.name} / {base['basis']}"})
                if period.get("application_basis") == COLLECTION and row.deduction_status in PENDING_DEDUCTION:
                    row.deducted_usd, row.deducted_nio = row.expected_usd, row.expected_nio
                    row.deduction_status, row.deduction_currency = "Inferida por depósito", ""
                    row.deduction_match_note = _("Inferida al trasladar la cobranza al depósito {0}; no es detalle recibido de la empresa.").format(document.name)
                    period.deduction_basis = "Depósito coincidente"
                    period.deduction_recognition_deposit = document.name
                    period.deduction_recognition_reference = document.deposit_reference
                    period.deduction_recognition_note = _("Correspondencia confirmada al trasladar la conciliación preparada.")
                    period.deduction_recognition_by, period.deduction_recognition_on = frappe.session.user, now_datetime()
            period.save()
        content = _workbook(records, amount_description=(
            "Base de cobranza o deducción revisada, expresada en US$. "
            "Generado desde la conciliación preparada; no es un archivo recibido de la empresa. "
            "El saldo a favor de la empresa se documenta aparte; el del cliente permanece en su fila."))
        attachment = save_file("detalle_preparado.xlsx", content, document.doctype, document.name,
                               is_private=1, df="detail_file")
        document.detail_file = attachment.file_url
        _apply_remittance_detail(document, records, content, attachment.file_url,
                                 origin="Conciliación preparada del período")
        details = iter(document.detail_rows)
        materialized = []
        for period in periods:
            for row in period.collection_rows:
                detail = next(details)
                if detail.client != row.client:
                    frappe.throw(_("La identidad del detalle no coincide con la cobranza. Revise cliente y crédito."))
                if money(row.applied_usd):
                    document.append("targets", {"period": period.name, "row_key": row.row_key,
                        "amount_usd": row.applied_usd, "detail_row": detail.name,
                        "notes": _("Aplicación vinculada desde la conciliación preparada")})
                for proposal in period.get("provisional_adjustments") or []:
                    if proposal.collection_row != row.name:
                        continue
                    if proposal.existing_item:
                        item = _existing_item(proposal, period, row)
                    else:
                        item = frappe.new_doc("CN Complementary Item")
                        item.update({"category": proposal.category, "subcategory": proposal.subcategory,
                            "reason_type": proposal.reason_type, "description": proposal.description,
                            "reference": document.deposit_reference, "posting_date": document.deposit_date,
                            "employer": period.employer, "period": period.name, "currency": "USD",
                            "amount": proposal.amount_usd, "client_number": row.client_number,
                            "loan_number": row.loan_number, "installment_number": row.installment_number,
                            "credit_assigned_to": proposal.assigned_to, "credit_commitment_date": proposal.commitment_date})
                        if proposal.category in CREDITS:
                            item.registered_deposit = document.name
                            item.credit_treatment = "Pendiente de decisión"
                            if proposal.category == "Saldo a favor del cliente":
                                item.credit_client, item.credit_detail_row = row.client, detail.name
                            else:
                                item.client_number = item.loan_number = item.installment_number = ""
                        item.flags.defer_reconciliation = True
                        item.insert()
                        item.submit()
                    proposal.state, proposal.complementary_item, proposal.deposit = "Materializado", item.name, document.name
                    materialized.append(item.name)
                    if item.category not in CREDITS:
                        document.append("targets", {"complementary_item": item.name, "amount_usd": item.amount_usd,
                            "detail_row": detail.name, "employer": period.employer, "notes": proposal.description})
                    item.add_comment("Comment", _("Vinculada desde ajuste provisional {0} del período {1}, depósito {2}.").format(proposal.name, period.name, document.name))
            period.prepared_deposit = document.name
            period.save()
        document.save()
        result = reconcile_deposit(document)
        document.reload()
        if (any(row.result != "Aplicada" for row in document.targets)
            or any(row.match_status not in {"Conciliada", "No deducido"} for row in document.detail_rows)
            or money(document.unclassified_usd)):
            frappe.throw(_("La distribución preparada no pudo aplicarse completamente. No se guardaron partidas ni cambios. Revise los destinos y saldos disponibles."))
        document.add_comment("Comment", _("Conciliación preparada trasladada desde {0}. Ajustes reales: {1}. Correspondencia confirmada por {2}.").format(
            ", ".join(plan["periods"]), ", ".join(materialized) or "Sin diferencias", frappe.session.user))
    return result
