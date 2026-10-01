"""Preview and transactional background creation of company/day imports."""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from frappe.utils.file_manager import save_file

from credinomina_reconciliation.accounting_batch import accounting_group_csv, group_applications, movement_key
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.accounting_review import create_review_items, plan_review_items
from credinomina_reconciliation.accounting_deposits import plan_deposits, create_deposits
from credinomina_reconciliation.client_registry import enrich_source_import_clients
from credinomina_reconciliation.credit_portfolio import enrich_accounting_records
from credinomina_reconciliation.employer_naming import attach_employer_aliases, UNIDENTIFIED_EMPLOYER, ensure_unidentified_employer
from credinomina_reconciliation.rounding import money_float, sum_money
from credinomina_reconciliation.parsers import (
    SourceFileError, apply_accounting_currency_override, clean_text, file_sha256,
    parse_accounting_movements, source_key, read_table, _records_from_header,
)

DOCTYPE = "CN Accounting Import"
TTL = 24 * 60 * 60
MAX_MOVEMENTS = 100_000


def _key(token):
    return f"cn-accounting-batch:{token}"


def _store(token, state):
    frappe.cache.set_value(_key(token), state, expires_in_sec=TTL)


def _permissions():
    if not frappe.has_permission(DOCTYPE, "create") or not frappe.has_permission(DOCTYPE, "write"):
        frappe.throw(_("No tiene permisos para crear importaciones contables."), frappe.PermissionError)


def _state(token):
    _permissions()
    state = frappe.cache.get_value(_key(token))
    if not state or state["user"] != frappe.session.user:
        frappe.throw(_("La carga no existe, expiró o pertenece a otro usuario."), frappe.PermissionError)
    return state


def _file(url):
    # An upload may be unattached or already attached to a readable document.
    for name in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
        document = frappe.get_doc("File", name)
        if document.has_permission("read"):
            content = document.get_content()
            if isinstance(content, str):
                content = content.encode("utf-8")
            if len(content) > 20 * 1024 * 1024:
                frappe.throw(_("El archivo supera 20 MB. Divida la carga en varios archivos."))
            return document, content
    frappe.throw(_("No tiene acceso al archivo seleccionado."), frappe.PermissionError)


def _plan(options):
    _permissions()
    file_doc, content = _file(options["source_file"])
    employers = frappe.get_list(
        "CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0,
    )
    attach_employer_aliases(employers)
    fallback = options.get("employer") or ""
    if fallback:
        frappe.get_doc("CN Employer", fallback).check_permission("read")
    snapshot = options.get("portfolio_snapshot") or ""
    if snapshot:
        frappe.get_doc("CN Credit Portfolio Snapshot", snapshot).check_permission("read")
    if not frappe.has_permission("CN Credit Portfolio Snapshot", "read"):
        frappe.throw(_("Se requiere acceso de lectura a los cortes de cartera."), frappe.PermissionError)
    records = apply_accounting_currency_override(
        parse_accounting_movements(file_doc.file_name, content),
        options["currency"], options.get("manual_fx_rate", 0),
    )
    if len(records) > MAX_MOVEMENTS:
        frappe.throw(_("La carga supera {0} movimientos. Divida el archivo.").format(f"{MAX_MOVEMENTS:,}"))
    file_hash = file_sha256(content)
    repeated_evidence, seen_evidence = [], set()
    for row in records:
        key = row["accounting_source_key"]
        if key in seen_evidence and row.get("event_type") != "Aplicacion":
            repeated_evidence.append({"row": row["source_row"], "reason": "Movimiento similar a otra línea del archivo; se conservará como registro independiente en borrador"})
        seen_evidence.add(key)
    identify_lines(records, file_hash)
    # Preview must never create clients or modify portfolio/master documents.
    records = enrich_accounting_records(records, snapshot, register_clients=False)
    parents = frappe.get_all(
        DOCTYPE, filters={"status": ["!=", "Fallido"]}, fields=["name", "employer", "file_hash", "bulk_source_hash"],
        limit_page_length=0,
    )
    companies = {parent.name: parent.employer for parent in parents}
    same_file_parents = {parent.name for parent in parents if file_hash in (parent.file_hash, parent.bulk_source_hash)}
    dates = [str(row["event_date"])[:10] for row in records if row.get("event_date")]
    existing = set()
    imported_lines = set()
    if dates and companies:
        for row in frappe.get_all(
            "CN Source Row", filters={"parenttype": DOCTYPE, "event_type": "Aplicacion",
                                      "event_date": ["between", [min(dates), max(dates)]]},
            fields=["parent", "source_row", "event_type", "loan_number", "reference", "currency", "amount", "event_date", "voucher"],
            limit_page_length=0,
        ):
            if row.parent in companies:
                existing.add((companies[row.parent], movement_key(row)))
            if row.parent in same_file_parents:
                imported_lines.add(row.source_row)
    review_records = [dict(row, event_type="Aplicacion") for row in records if row.get("event_type") == "Ajuste"]
    enrich_accounting_records(review_records, snapshot, register_clients=False)
    for row in review_records:
        row["event_type"] = "Ajuste"
    review_records = plan_review_items(review_records, employers, fallback)
    deposit_records = plan_deposits(records, employers, fallback)
    applications = [row for row in records if row.get("event_type") != "Ajuste" and row.get("accounting_classification") != "Depósito"]
    plan = group_applications([row for row in applications if row["source_row"] not in imported_lines], employers, fallback, existing)
    plan["already_imported"] = [{"row": row["source_row"], "reason": "Esta misma fila del mismo archivo ya fue importada; use su documento para reprocesarla"}
                                for row in applications if row["source_row"] in imported_lines]
    plan["duplicates"].extend(repeated_evidence)
    if deposit_records and not frappe.has_permission("CN Remittance Allocation", "create"):
        frappe.throw(_("Se requiere permiso para crear depósitos."), frappe.PermissionError)
    # Keep already imported evidence in the plan: creation is idempotent and
    # checks currency/rate consistency instead of silently discarding changes.
    plan["deposits"] = deposit_records
    if review_records and not frappe.has_permission("CN Complementary Item", "create"):
        frappe.throw(_("Se requiere permiso para crear partidas complementarias en revisión."), frappe.PermissionError)
    existing_review = set(frappe.get_all("CN Complementary Item", filters={
        "accounting_source_key": ["in", [row["accounting_source_key"] for row in review_records]],
    }, pluck="accounting_source_key")) if review_records else set()
    plan["complementary"] = []
    for row in review_records:
        if not row.get("event_date"):
            plan["issues"].append({"row": row["source_row"], "reason": "Falta fecha válida en el movimiento por revisar"})
        elif row["accounting_source_key"] in existing_review:
            plan["already_imported"].append({"row": row["source_row"], "reason": "Esta misma fila del mismo archivo ya tiene una partida complementaria"})
        else:
            existing_review.add(row["accounting_source_key"])
            plan["complementary"].append(row)
    plan.update(file_hash=file_sha256(content), file_name=file_doc.file_name)
    return plan


def _summary(plan):
    groups = [{key: value for key, value in group.items() if key != "rows"} for group in plan["groups"]]
    return {
        "sections": {key: _identification_summary(plan, unidentified)
                     for key, unidentified in (("identified", False), ("unidentified", True))},
        "groups": groups, "rows": sum(group["count"] for group in groups),
        "complementary_count": len(plan.get("complementary", [])),
        "deposit_count": len(plan.get("deposits", [])),
        "unidentified_count": sum(group["count"] for group in groups if group["employer"] == UNIDENTIFIED_EMPLOYER)
            + sum(row.get("resolved_employer") == UNIDENTIFIED_EMPLOYER
                  for row in plan.get("complementary", []) + plan.get("deposits", [])),
        "deposits": [{"row": row["source_row"], "employer": row.get("resolved_employer"),
                      "currency": row["deposit_currency"], "amount": row["deposit_amount"],
                      "reference": row["bank_deposit_reference"]} for row in plan.get("deposits", [])[:100]],
        "complementary": [{"row": row["source_row"], "classification": row["accounting_classification"],
                           "employer": row.get("resolved_employer"), "reason": row["classification_reason"]}
                          for row in plan.get("complementary", [])[:100]],
        **{key: plan[key][:100] for key in ("issues", "duplicates", "excluded", "already_imported")},
        **{f"{key}_count": len(plan[key]) for key in ("issues", "duplicates", "excluded", "already_imported")},
    }


def _identification_summary(plan, unidentified):
    def belongs(employer):
        return (not employer or employer == UNIDENTIFIED_EMPLOYER) == unidentified

    groups = [group for group in plan["groups"] if belongs(group["employer"])]
    items = [row for row in plan.get("complementary", []) if belongs(row.get("resolved_employer"))]
    deposits = [row for row in plan.get("deposits", []) if belongs(row.get("resolved_employer"))]
    applications = [row for group in groups for row in group["rows"]]
    return {
        "groups": [{key: value for key, value in group.items() if key != "rows"} for group in groups],
        "rows": len(applications), "application_total_usd": money_float(sum_money(row.get("amount_usd") for row in applications)),
        "applications": [{"row": row["source_row"], "event_date": row.get("event_date"),
                          "client_name": row.get("client_name"), "loan_number": row.get("loan_number"),
                          "employer_text": row.get("employer_text"), "voucher": row.get("voucher"),
                          "total_usd": row.get("amount_usd"), "reason": row.get("portfolio_validation_status")}
                         for row in applications[:100]] if unidentified else [],
        "complementary_count": len(items),
        "complementary": [{"row": row["source_row"], "classification": row["accounting_classification"],
                           "employer": row.get("resolved_employer"), "reason": row["classification_reason"],
                           "employer_text": row.get("employer_text")} for row in items[:100]],
        "deposit_count": len(deposits),
        "deposits": [{"row": row["source_row"], "employer": row.get("resolved_employer"),
                      "currency": row["deposit_currency"], "amount": row["deposit_amount"],
                      "reference": row["bank_deposit_reference"], "employer_text": row.get("employer_text")}
                     for row in deposits[:100]],
    }


def _signature(plan):
    # Confirmation cannot silently import a different grouping or amount.
    return source_key(plan["file_hash"], json.dumps([plan["groups"], plan.get("complementary", []), plan.get("deposits", [])], sort_keys=True, default=str))


@frappe.whitelist(methods=["POST"])
def preview_bulk_import(source_file, currency, manual_fx_rate=0, employer="", portfolio_snapshot="", historical_backfill=0):
    _permissions()
    _file(source_file)
    token = frappe.generate_hash(length=32)
    state = {"user": frappe.session.user, "status": "En cola", "phase": "preview", "options": {
        "source_file": source_file, "currency": clean_text(currency), "manual_fx_rate": manual_fx_rate,
        "employer": clean_text(employer), "portfolio_snapshot": clean_text(portfolio_snapshot),
        "historical_backfill": cint(historical_backfill),
    }}
    _store(token, state)
    _enqueue(token, state)
    return {"token": token}


def _enqueue(token, state):
    try:
        state["job_id"] = f"cn-accounting-batch-{token}-{state['phase']}"
        _store(token, state)
        frappe.enqueue(
            "credinomina_reconciliation.bulk_accounting_import.run_bulk_job",
            queue="long", timeout=3600, token=token, user=state["user"],
            enqueue_after_commit=True, job_id=state["job_id"],
        )
    except Exception:
        state.update(status="Error", error=_("No se pudo encolar la carga. Revise los workers y vuelva a intentarlo."))
        _store(token, state)
        raise


@frappe.whitelist(methods=["POST"])
def confirm_bulk_import(token):
    with frappe.cache.lock(_key(token) + ":confirmation", timeout=30):
        state = _state(token)
        if state["status"] != "Vista previa" or state["summary"]["issues_count"]:
            frappe.throw(_("Primero genere una vista previa sin errores."))
        if not state["summary"]["rows"] and not state["summary"].get("complementary_count") and not state["summary"].get("deposit_count"):
            frappe.throw(_("No hay movimientos nuevos para importar."))
        state.update(status="En cola", phase="create")
        _store(token, state)
        _enqueue(token, state)
    return {"token": token}


@frappe.whitelist()
def get_bulk_import_status(token):
    state = _state(token)
    if state["status"] in {"En cola", "Procesando"} and state.get("job_id"):
        from frappe.utils.background_jobs import get_job

        job = get_job(state["job_id"])
        if job and job.get_status() in {"failed", "stopped", "canceled"}:
            state.update(status="Error", error=_(
                "El worker interrumpió la carga. Genere una nueva vista previa para comprobar qué movimientos faltan; los ya importados no se duplicarán."
            ))
            _store(token, state)
    return {key: state.get(key) for key in ("status", "phase", "summary", "created", "error", "progress", "options")}


def _create_imports(plan, options, progress=None):
    """One transaction, no reconciliation or period changes. Caller owns commit."""
    created = []
    source, content = _file(options["source_file"])
    source_records = dict(_records_from_header(read_table(source.file_name, content), "cuenta_contable"))
    if any(group["employer"] == UNIDENTIFIED_EMPLOYER for group in plan["groups"]):
        ensure_unidentified_employer()
    records = []
    for group in plan["groups"]:
        for record in group["rows"]:
            if group["employer"] != UNIDENTIFIED_EMPLOYER and not record.get("employer_text") and not record.get("portfolio_employer"):
                record["employer_text"] = group["employer"]
            records.append(record)
    # The preview already selected the dated cut. Reuse that evidence and build
    # the registry once for the whole batch, without changing its company scope.
    enrich_source_import_clients(records)
    for index, group in enumerate(plan["groups"], 1):
        employer = group["employer"]
        frappe.get_doc("CN Employer", employer).check_permission("read")
        records = group["rows"]
        csv_content = accounting_group_csv(source_records, records)
        document = frappe.get_doc({
            "doctype": DOCTYPE, "employer": employer, "source_file": options["source_file"],
            "currency": options["currency"], "manual_fx_rate": options.get("manual_fx_rate", 0),
            "portfolio_snapshot": options.get("portfolio_snapshot") or "",
            "historical_backfill": options.get("historical_backfill", 0),
            "bulk_source_hash": plan["file_hash"], "bulk_event_date": group["event_date"],
            "bulk_source_file": options["source_file"],
            "file_hash": file_sha256(csv_content),
            "status": "Importado", "imported_on": now_datetime(), "imported_by": frappe.session.user,
            "notes": _("Carga masiva de {0}. Fecha: {1}. Pendiente de conciliar esta empresa.").format(
                plan["file_name"], group["event_date"])
                + (_(" Empresa pendiente de identificar; se conservó el texto original del movimiento.") if employer == UNIDENTIFIED_EMPLOYER else ""),
            "rows": [{**record, "effective": 1, "match_status": "Pendiente", "deposit_match_status": "Pendiente"}
                     for record in records],
        }).insert()
        csv_file = save_file(
            f"{document.name}.csv", csv_content, DOCTYPE, document.name,
            is_private=1, df="source_file",
        )
        document.source_file = csv_file.file_url
        stored_content = csv_file.get_content()
        if isinstance(stored_content, str):
            stored_content = stored_content.encode("utf-8")
        # File.get_content may strip the UTF-8 BOM; use the exact same input
        # representation as the individual importer for duplicate detection.
        document.file_hash = file_sha256(stored_content)
        document.save()
        # Keep the original report separately as provenance, never as reimport input.
        frappe.get_doc({
            "doctype": "File", "file_name": source.file_name, "file_url": source.file_url,
            "is_private": source.is_private, "attached_to_doctype": DOCTYPE,
            "attached_to_name": document.name, "attached_to_field": "bulk_source_file",
        }).insert()
        created.append({"name": document.name, "employer": employer, "event_date": group["event_date"],
                        "rows": len(records), "total_usd": document.total_usd})
        if progress:
            progress(index, len(plan["groups"]))
    for item in create_review_items(plan.get("complementary", []), options["source_file"], plan["file_hash"]):
        created.append({"doctype": "CN Complementary Item", "name": item.name, "employer": item.employer,
                        "event_date": item.posting_date, "rows": 1, "total_usd": item.amount_usd})
    create_deposits(plan.get("deposits", []), options["source_file"], plan["file_hash"])
    for row in plan.get("deposits", []):
        created.append({"doctype": "CN Remittance Allocation", "name": row["remittance_allocation"],
                        "employer": row.get("resolved_employer"), "event_date": row["event_date"],
                        "rows": 1, "total_usd": row["deposit_usd"]})
    return created


def run_bulk_job(token, user):
    frappe.set_user(user)
    state = _state(token)
    try:
        state.update(status="Procesando", progress="")
        _store(token, state)
        # Serialize bulk confirmations, including files with overlapping rows.
        with frappe.cache.lock(f"{frappe.local.site}:cn-accounting-batch-create", timeout=3700, blocking_timeout=5):
            plan = _plan(state["options"])
            if state["phase"] == "preview":
                state.update(status="Vista previa", summary=_summary(plan), signature=_signature(plan))
            else:
                if plan["issues"] or _signature(plan) != state["signature"]:
                    frappe.throw(_("Los datos o las aplicaciones existentes cambiaron. Genere otra vista previa antes de importar."))

                def progress(done, total):
                    state["progress"] = _("Creando documento {0} de {1}").format(done, total)
                    _store(token, state)

                created = _create_imports(plan, state["options"], progress)
                frappe.db.commit()
                state.update(status="Completado", created=created)
        _store(token, state)
    except Exception as exc:
        frappe.db.rollback()
        frappe.log_error(title="Carga masiva contable", message=frappe.get_traceback())
        message = str(exc) if isinstance(exc, (frappe.ValidationError, SourceFileError)) else _(
            "No se pudo completar o informar la carga. Revise el registro de errores y los workers; una nueva vista previa excluirá los movimientos ya importados."
        )
        state.update(status="Error", error=message)
        _store(token, state)
