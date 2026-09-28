"""Monthly portfolio cuts and deterministic links to the client registry."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.client_registry import ClientIndex, load_client_index
from credinomina_reconciliation.employer_naming import (
    attach_employer_aliases,
    employer_alias_index,
    employer_label_key,
)
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def _is_convenio(value):
    return clean_text(value).casefold() in {"s", "si", "sí", "1", "true", "yes"}


def _is_not_convenio(value):
    return clean_text(value).casefold() in {"n", "no", "0", "false"}


def credit_lifecycle(status):
    """Keep source states visible; only explicit settled states mean canceled."""
    value = clean_text(status).casefold()
    if value in {"corriente", "vigente", "vencido", "en mora", "mora"}:
        return "Activo"
    if value in {"cancelado", "liquidado", "saldado", "finiquitado"}:
        return "Cancelado"
    if value in {"saneado", "castigado"}:
        return "Saneado"
    return "Por revisar"


def _employer_for(text, aliases, ambiguous):
    key = employer_label_key(text)
    if not key:
        return "", "Empresa no informada"
    if key in ambiguous:
        return "", "Empresa ambigua"
    employer = aliases.get(key)
    return (employer, "Empresa identificada") if employer else ("", "Empresa no registrada")


def _client_for_portfolio_row(row, clients, employer):
    identifiers = {
        canonical_identifier(row.get(field))
        for field in ("client_number_migrated", "client_number_core")
        if canonical_identifier(row.get(field))
    }
    national_id = canonical_identifier(row.get("national_id"))
    found = {}
    for client in clients:
        if (
            (identifiers and canonical_identifier(client.get("client_number")) in identifiers)
            or (national_id and canonical_identifier(client.get("national_id")) == national_id)
        ):
            found[client["name"]] = client

    if len(found) > 1:
        return None, "Identificadores en conflicto", "Identificación ambigua"
    if len(found) == 1:
        client = next(iter(found.values()))
        if employer and client.get("employer") != employer:
            return client, "Identificador pertenece a otra empresa", "Empresa no coincide"
        return client, "Identificador exacto", "Cliente identificado"

    key = name_key(row.get("client_name"))
    if not key:
        return None, "Sin identificadores", "Cliente no identificado"
    candidates = [
        client for client in clients
        if (not employer or client.get("employer") == employer)
        and key in {
            name_key(client.get("client_name")),
            *(name_key(alias) for alias in client.get("client_aliases", [])),
        }
    ]
    if len(candidates) == 1:
        return candidates[0], "Nombre o alias exacto", "Cliente identificado por nombre"
    if len(candidates) > 1:
        return None, "Nombre repetido", "Nombre ambiguo"
    return None, "No encontrado", "Cliente no registrado"


def analyze_portfolio_rows(rows):
    """Add client/employer checks without creating or changing client records."""
    clients = load_client_index()
    employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    attach_employer_aliases(employers)
    aliases, ambiguous = employer_alias_index(employers)

    for row in rows:
        convenio = row.get("is_convenio")
        if _is_not_convenio(convenio):
            employer, employer_status = "", "No es convenio"
        else:
            employer, employer_status = _employer_for(
                row.get("employer_text"), aliases, ambiguous
            )
            if not _is_convenio(convenio) and employer_status == "Empresa identificada":
                employer_status = "Convenio no confirmado"

        client, match_status, client_status = _client_for_portfolio_row(
            row, clients, employer
        )
        if not clean_text(row.get("credit_number")):
            client_status = "Fila sin número de crédito"
        if _is_not_convenio(convenio):
            if clean_text(row.get("credit_number")):
                client_status = (
                    "Cliente existente; no es convenio"
                    if client else "No aplica; no es convenio"
                )
        elif client and employer and client.get("employer") != employer:
            client_status = "Cliente pertenece a otra empresa"
        elif client and not employer:
            client_status = "Cliente existe; empresa sin validar"
        elif client and employer:
            client_status = "Cliente y empresa validados"

        row.update({
            "credit_lifecycle": credit_lifecycle(row.get("credit_status")),
            "matched_client": client.get("name") if client else "",
            "client_match_status": match_status,
            "employer": employer,
            "employer_match_status": employer_status,
            "validation_status": client_status,
        })
    return rows


def _load_snapshot_rows(snapshot_name):
    rows = frappe.get_all(
        "CN Credit Portfolio Row",
        filters={"parent": snapshot_name},
        fields=[
            "name", "credit_number", "client_number_migrated", "client_number_core",
            "client_name", "credit_status", "credit_lifecycle", "employer_text",
            "employer", "employer_match_status", "national_id", "is_convenio",
            "matched_client", "client_match_status", "validation_status",
        ],
        limit_page_length=100000,
    )
    by_credit = defaultdict(list)
    by_client_number = defaultdict(list)
    for row in rows:
        credit_number = canonical_identifier(row.credit_number)
        if credit_number:
            by_credit[credit_number].append(row)
        client_number_siaf = canonical_identifier(row.get("client_number_core"))
        if client_number_siaf:
            by_client_number[client_number_siaf].append(row)
    return {"credit": by_credit, "client_number_siaf": by_client_number}


def _portfolio_client_identity(row):
    """Group several credit rows only when they clearly belong to one client."""
    if row.get("matched_client"):
        return (
            "client", row.matched_client,
            canonical_identifier(row.get("national_id")),
            canonical_identifier(row.get("employer")),
        )
    identity = (
        canonical_identifier(row.get("national_id")),
        name_key(row.get("client_name")),
        canonical_identifier(row.get("employer")),
    )
    return ("snapshot", *identity) if any(identity) else ("row", row.name)


def _unique_client_portfolio_row(index, client_number):
    candidates = index["client_number_siaf"].get(client_number, [])
    if not candidates:
        return None, "Cliente no encontrado por número de cliente"
    if len({_portfolio_client_identity(row) for row in candidates}) > 1:
        return None, "Número de cliente ambiguo en el corte"
    return candidates[0], ""


def _copy_portfolio_client(record, portfolio):
    record.update({
        "portfolio_client_name": portfolio.client_name,
        "portfolio_client": portfolio.matched_client or "",
        "portfolio_employer": portfolio.employer or "",
    })
    if not clean_text(record.get("client_name")):
        record["client_name"] = portfolio.client_name
    if not clean_text(record.get("client_number")):
        record["client_number"] = portfolio.client_number_core or ""
    if not clean_text(record.get("national_id")):
        record["national_id"] = portfolio.national_id
    if not clean_text(record.get("employer_text")):
        record["employer_text"] = portfolio.employer_text


def _movement_employer_issue(movement_employer_text, portfolio, employer_aliases):
    movement_employer_text = clean_text(movement_employer_text)
    if not movement_employer_text or not portfolio.employer:
        return ""
    movement_employer = employer_aliases.get(employer_label_key(movement_employer_text))
    if movement_employer:
        return (
            "Empresa del movimiento distinta a la del corte"
            if movement_employer != portfolio.employer else ""
        )
    return (
        "Empresa del movimiento no identificada"
        if movement_employer_text.casefold() != clean_text(
            portfolio.employer_text
        ).casefold() else ""
    )


def _register_portfolio_client(record, portfolio, client_index, allow_create=True):
    """Validate a client link and create a missing one from the matched cut row."""
    if (
        not portfolio.employer
        or portfolio.employer_match_status != "Empresa identificada"
    ):
        record["portfolio_client"] = portfolio.matched_client or ""
        record["client_registry_status"] = (
            "Cliente existente; convenio o empresa no confirmado"
            if portfolio.matched_client
            else "No creado: convenio o empresa de cartera no confirmado"
        )
        return client_index
    if not allow_create:
        record["portfolio_client"] = portfolio.matched_client or ""
        record["client_registry_status"] = (
            "Revisar: número de cliente del movimiento no coincide con SIAF"
        )
        return client_index

    if client_index is None:
        client_index = ClientIndex()
    client_name, resolution = client_index.ensure_from_portfolio(
        {
            "client_name": portfolio.client_name,
            "client_number": portfolio.client_number_core,
            "national_id": portfolio.national_id,
            "portfolio_client": portfolio.matched_client or "",
        },
        portfolio.employer,
    )
    record["portfolio_client"] = client_name
    record["client_registry_status"] = resolution
    return client_index


def enrich_accounting_records(records, selected_snapshot=""):
    """Enrich payment movements by loan or client number using the dated cut.

    A monthly cut applies to movements in that same month even when the report
    date is month-end. If no cut exists for that month, use the newest earlier
    month. An explicit selection overrides this rule for historical corrections.
    Client numbers validate identity even when a credit number is missing; a
    mismatch is a warning and never silently discards the accounting movement.
    """
    if selected_snapshot:
        selected = frappe.db.get_value(
            "CN Credit Portfolio Snapshot", selected_snapshot,
            ["name", "report_date", "status"], as_dict=True,
        )
        if not selected or selected.status not in {"Importado", "Importado con alertas"}:
            frappe.throw(_("Seleccione un corte de cartera importado."))
        snapshots = [selected]
    else:
        snapshots = frappe.get_all(
            "CN Credit Portfolio Snapshot",
            filters={"status": ["in", ["Importado", "Importado con alertas"]]},
            fields=["name", "report_date"],
            order_by="report_date asc",
            limit_page_length=100000,
        )

    snapshots = sorted(
        snapshots,
        key=lambda snapshot: (getdate(snapshot.report_date), snapshot.name),
    )
    explicit = bool(selected_snapshot)
    snapshot_by_record = {}
    for record in records:
        if record.get("event_type") != "Aplicacion":
            continue
        if explicit:
            snapshot = snapshots[0]
        else:
            event_date = record.get("event_date")
            event_month = getdate(event_date).strftime("%Y-%m") if event_date else ""
            same_month = [
                item for item in snapshots
                if event_month and getdate(item.report_date).strftime("%Y-%m") == event_month
            ]
            applicable = same_month or [
                item for item in snapshots
                if event_month and getdate(item.report_date).strftime("%Y-%m") < event_month
            ]
            snapshot = applicable[-1] if applicable else None
        snapshot_by_record[id(record)] = snapshot
    used_snapshots = {
        snapshot.name: snapshot
        for snapshot in snapshot_by_record.values() if snapshot
    }
    row_indexes = {
        name: _load_snapshot_rows(name) for name in used_snapshots
    }
    client_index = None
    employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    attach_employer_aliases(employers)
    employer_aliases, _ambiguous_employers = employer_alias_index(employers)

    for record in records:
        if record.get("event_type") != "Aplicacion":
            continue
        snapshot = snapshot_by_record.get(id(record))
        if not snapshot:
            record["portfolio_validation_status"] = "Sin corte de cartera aplicable"
            record["client_registry_status"] = "No validado: sin corte aplicable"
            continue

        record["portfolio_snapshot_used"] = snapshot.name
        index = row_indexes[snapshot.name]
        credit_key = canonical_identifier(record.get("loan_number"))
        client_key = canonical_identifier(record.get("client_number"))
        if not credit_key:
            if not client_key:
                record["portfolio_validation_status"] = (
                    "Movimiento sin número de crédito ni número de cliente"
                )
                record["client_registry_status"] = (
                    "No creado: faltan número de crédito y de cliente"
                )
                continue
            portfolio, reason = _unique_client_portfolio_row(index, client_key)
            if not portfolio:
                record["portfolio_validation_status"] = reason
                record["client_registry_status"] = "No creado: cliente no identificado en el corte"
                continue
            movement_employer_text = record.get("employer_text")
            _copy_portfolio_client(record, portfolio)
            employer_issue = _movement_employer_issue(
                movement_employer_text, portfolio, employer_aliases
            )
            if employer_issue:
                record["portfolio_validation_status"] = employer_issue
            elif portfolio.validation_status == "Cliente y empresa validados":
                record["portfolio_validation_status"] = _(
                    "Cliente y empresa validados por número de cliente; falta número de crédito"
                )
            else:
                record["portfolio_validation_status"] = _(
                    "{0}; falta número de crédito"
                ).format(portfolio.validation_status or _("Cliente identificado"))
            client_index = _register_portfolio_client(
                record, portfolio, client_index
            )
            continue

        matches = index["credit"].get(credit_key, [])
        if len(matches) != 1:
            if matches:
                record["portfolio_validation_status"] = "Crédito duplicado en corte"
                record["client_registry_status"] = "No creado: crédito duplicado en el corte"
            elif client_key:
                portfolio, reason = _unique_client_portfolio_row(index, client_key)
                if portfolio:
                    movement_employer_text = record.get("employer_text")
                    _copy_portfolio_client(record, portfolio)
                    employer_issue = _movement_employer_issue(
                        movement_employer_text, portfolio, employer_aliases
                    )
                    if employer_issue:
                        record["portfolio_validation_status"] = employer_issue
                    else:
                        record["portfolio_validation_status"] = _(
                            "Crédito no encontrado en el corte; cliente identificado por número de cliente"
                        )
                    client_index = _register_portfolio_client(
                        record, portfolio, client_index
                    )
                else:
                    record["portfolio_validation_status"] = (
                        "Crédito no encontrado en el corte; {0}"
                    ).format(_(reason).lower())
                    record["client_registry_status"] = (
                        "No creado: cliente no identificado en el corte"
                    )
            else:
                record["portfolio_validation_status"] = "Crédito no encontrado en el corte"
                record["client_registry_status"] = "No creado: crédito no encontrado en el corte"
            continue

        portfolio = matches[0]
        movement_employer_text = record.get("employer_text")
        client_number_mismatch = bool(
            client_key
            and client_key != canonical_identifier(
                portfolio.get("client_number_core")
            )
        )
        record.update({
            "portfolio_client_name": portfolio.client_name,
            "portfolio_client": portfolio.matched_client or "",
            "portfolio_employer": portfolio.employer or "",
            "portfolio_credit_status": portfolio.credit_status,
            "portfolio_credit_lifecycle": portfolio.credit_lifecycle,
            "portfolio_validation_status": portfolio.validation_status,
        })
        _copy_portfolio_client(record, portfolio)

        if credit_lifecycle(portfolio.credit_status) == "Cancelado":
            record["portfolio_validation_status"] = "Crédito cancelado; revisar antes de conciliar"
        elif credit_lifecycle(portfolio.credit_status) == "Saneado":
            record["portfolio_validation_status"] = "Crédito saneado; validar situación"
        elif credit_lifecycle(portfolio.credit_status) == "Por revisar":
            record["portfolio_validation_status"] = "Estado de crédito por revisar"
        elif portfolio.validation_status != "Cliente y empresa validados":
            record["portfolio_validation_status"] = portfolio.validation_status
        else:
            employer_issue = _movement_employer_issue(
                movement_employer_text, portfolio, employer_aliases
            )
            if employer_issue:
                record["portfolio_validation_status"] = employer_issue
        if client_number_mismatch:
            record["portfolio_validation_status"] = _(
                "Número de cliente del movimiento no coincide con el crédito en cartera"
            )
        client_index = _register_portfolio_client(
            record, portfolio, client_index,
            allow_create=not client_number_mismatch,
        )
    return records
