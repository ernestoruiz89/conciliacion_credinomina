"""Snapshot workbook for the Credinómina control page.

The source of each monetary amount is the reconciliation state already stored
on a period, claim or application. A deposit-to-claim link is emitted once per
actual allocation, so neither many-to-many deposits nor quincenas are reduced
to a first reference match.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from credinomina_reconciliation.rounding import CASH_EPSILON, money_float


NA = "N/D"
MAX_EXCEL_ROWS = 1_048_576
NAVY = "1F4E78"
PALE = "EDF3F8"
TEXT = "263547"
MONEY_FORMAT = '#,##0.00;[Red](#,##0.00);"-"'
DATE_FORMAT = "dd/mm/yyyy"


def build_control_workbook(
    data: dict, *, exceptions: list, actions: list, employer_label: str,
    generated_at: datetime,
) -> bytes:
    """Return a read-only-in-practice, value-based audit snapshot as XLSX bytes."""
    book = Workbook()
    summary = book.active
    summary.title = "Resumen"
    periods = data["periods"]
    _write_summary(summary, data, employer_label, generated_at)

    detail_rows = []
    link_rows = []
    issue_rows = []
    period_by_name = {period["name"]: period for period in periods}
    for period in periods:
        historical = period.get("reconciliation_mode") == "Historica"
        for claim in period.get("rows") or []:
            detail_rows.append(_collection_detail(period, claim))
            link_rows.extend(_claim_links(period, claim, historical=False))
            due = claim.get("employee_receivable_usd")
            if due is None and _money(claim.get("expected_usd")) > CASH_EPSILON:
                issue_rows.append(_issue(
                    "Detalle de deducción pendiente", period,
                    claim.get("client_number"), claim.get("loan_number"),
                    claim.get("application_reference"), _money(claim.get("expected_usd")),
                    "Pendiente", detail="Sin evidencia suficiente para calcular CxC del empleado.",
                ))
            elif due is not None and _money(due) > CASH_EPSILON:
                issue_rows.append(_issue(
                    "CxC empleado", period, claim.get("client_number"),
                    claim.get("loan_number"), claim.get("application_reference"),
                    _money(due), "Pendiente", detail=claim.get("deduction_match_note"),
                ))
        for application in period.get("historical_rows") or []:
            detail_rows.append(_historical_detail(period, application))
            link_rows.extend(_claim_links(period, application, historical=True))
            balance = _money(application.get("historical_balance_usd"))
            if balance > CASH_EPSILON:
                issue_rows.append(_issue(
                    "Aplicación histórica sin depósito", period,
                    application.get("client_number"), application.get("loan_number"),
                    application.get("reference"), balance,
                    application.get("deposit_match_status") or "Pendiente",
                    detail=application.get("deposit_match_reason"),
                ))
        assignment_gap = _money(period.get("employer_gap_usd"))
        if not historical and assignment_gap > CASH_EPSILON:
            issue_rows.append(_issue(
                "Deducido sin remesa asignada (no CxC confirmada)", period,
                None, None, None, assignment_gap, "Pendiente de asignación",
                detail="Puede existir un depósito recibido sin detalle suficiente para asignarlo.",
            ))
        for surplus in period.get("surpluses") or []:
            issue_rows.append(_issue(
                "Excedente documentado", period, None, None,
                surplus.get("deposit_reference"), _money(surplus.get("amount_usd")),
                surplus.get("result"), cause=surplus.get("reason_type"),
                detail=surplus.get("explanation"),
            ))

    for exception in exceptions:
        period = period_by_name.get(exception.get("period"))
        if period is None:
            continue
        issue_rows.append(_issue(
            "Excepción", period, exception.get("client_number"),
            exception.get("loan_number"), exception.get("name"),
            _money(exception.get("amount_usd")), exception.get("status"),
            cause=exception.get("cause_category") or exception.get("exception_type"),
            owner=exception.get("assigned_to"), action=exception.get("next_action"),
            commitment=exception.get("commitment_date"),
            evidence=exception.get("evidence_file"),
            external_reference=exception.get("external_reference"),
            detail=exception.get("description"), resolution=exception.get("resolution"),
        ))
    for deposit in data.get("open_deposits") or []:
        unclassified = _money(deposit.get("unclassified_usd"))
        if unclassified <= CASH_EPSILON:
            continue
        issue_rows.append(_issue(
            "Depósito sin clasificar", None, None, None,
            deposit.get("reference"), unclassified, "Pendiente de clasificación",
            employer=deposit.get("employer_text"),
            detail=deposit.get("allocation_reason"),
            event_date=deposit.get("event_date"),
        ))
    for surplus in data.get("unassigned_surpluses") or []:
        issue_rows.append(_issue(
            "Saldo a favor documentado sin período", None, None, None,
            surplus.get("deposit_reference"), _money(surplus.get("amount_usd")),
            surplus.get("result"), employer=surplus.get("employer"),
            cause=surplus.get("reason_type"), detail=surplus.get("explanation"),
        ))
    for application in data.get("unassigned_historical_applications") or []:
        issue_rows.append(_issue(
            "Aplicación histórica sin período", None, None,
            application.get("loan_number"), application.get("reference"),
            _money(application.get("amount")), "Sin período",
            detail=application.get("match_reason"),
            event_date=application.get("event_date"),
        ))
    issue_rows = [
        (*row, _commitment_delay(row, generated_at.date())) for row in issue_rows
    ]

    scope = f"Año de planilla: {data['year']}    Empresa: {employer_label}"
    _write_table(
        book.create_sheet("Detalle cliente"), "Detalle por cliente y aplicación", scope,
        DETAIL_HEADERS, detail_rows,
        money_columns={14, 15, 16, 17, 18, 19, 20, 21},
        date_columns={10},
    )
    _write_table(
        book.create_sheet("Cruces"), "Cruces aplicación y depósito", scope,
        LINK_HEADERS, link_rows, money_columns={13, 14}, date_columns={11},
    )
    _write_table(
        book.create_sheet("Partidas y excepciones"), "Partidas y excepciones", scope,
        ISSUE_HEADERS, issue_rows, money_columns={8}, date_columns={2, 13, 18},
    )
    _write_table(
        book.create_sheet("Gestiones"), "Gestiones de excepciones", scope,
        ACTION_HEADERS, _action_rows(actions, exceptions, period_by_name),
        date_columns={4},
    )
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


SUMMARY_HEADERS = (
    "Período", "Empresa", "Mes planilla", "Modalidad", "Alcance histórico",
    "Fecha inicio aplicación", "Fecha fin aplicación", "Vencimiento pago",
    "Estado", "Cobranza USD", "Deducido USD", "Aplicado USD",
    "Complementario USD", "Ajuste USD", "Depósito asignado USD",
    "Pendiente histórico USD", "CxC empleado USD", "Sin remesa asignada USD",
    "Sin clasificar USD", "Excepciones abiertas", "Último corte de control",
    "Motivo y próxima gestión", "Resumen guardado en el corte",
)

DETAIL_HEADERS = (
    "Período", "Empresa", "Modalidad", "N.º cliente", "N.º empleado",
    "Nombre del cliente", "Cédula", "N.º crédito", "N.º cuota",
    "Fecha aplicación", "Asiento contable", "Recibo", "Referencia aplicación",
    "Cobranza USD", "Deducido USD", "Aplicado USD", "Complementario USD",
    "Ajuste USD", "Depósito asignado USD", "CxC empleado USD",
    "Pendiente histórico USD", "Estado", "Comentarios",
)

LINK_HEADERS = (
    "Período", "Empresa", "Modalidad", "N.º cliente", "Nombre del cliente",
    "N.º crédito", "ID partida", "Referencia aplicación",
    "Referencia depósito", "Comprobante depósito", "Fecha depósito", "Destino",
    "Monto vinculado USD", "Ajuste USD", "Origen", "Comentario heredado",
)

ISSUE_HEADERS = (
    "Tipo", "Fecha", "Período", "Empresa", "N.º cliente", "N.º crédito",
    "Referencia / ID", "Monto USD", "Estado", "Causa", "Responsable",
    "Próxima acción", "Compromiso", "Evidencia", "Referencia externa",
    "Detalle", "Resolución", "Vencimiento contractual", "Días atraso compromiso",
)

ACTION_HEADERS = (
    "Excepción", "Período", "Empresa", "Fecha gestión", "Usuario",
    "Tipo de gestión", "Detalle", "Evidencia", "Referencia externa",
)


def _write_summary(sheet, data, employer_label, generated_at):
    _base_sheet(sheet, "Resumen de conciliaciones")
    sheet["A2"] = "Año de planilla"
    sheet["B2"] = data["year"]
    sheet["D2"] = "Empresa"
    sheet["E2"] = _safe_text(employer_label)
    sheet["A3"] = "Generado"
    sheet["B3"] = generated_at
    sheet["B3"].number_format = "dd/mm/yyyy hh:mm"
    periods = data["periods"]
    operational = [p for p in periods if p.get("reconciliation_mode") != "Historica"]
    historical = [p for p in periods if p.get("reconciliation_mode") == "Historica"]
    measures = [
        ("Períodos", len(periods)),
        ("Operativos", len(operational)),
        ("Históricos", len(historical)),
        ("Aplicado USD", _money(data["totals"].get("applied_usd"))),
        ("Depósito asignado USD", _money(data["totals"].get("remitted_usd"))),
        ("Pendiente histórico USD", _money(data["totals"].get("historical_pending_usd")) if historical else NA),
        ("Cobranza operativa USD", _money(data["totals"].get("expected_usd")) if operational else NA),
        ("Deducido operativo USD", _money(data["totals"].get("deducted_usd")) if operational else NA),
        ("CxC empleados USD", _money(data["totals"].get("worker_gap_usd")) if operational else NA),
        ("Deducido sin remesa asignada USD", _money(data["totals"].get("employer_gap_usd")) if operational else NA),
        ("Excedente sin clasificar USD", _money(data["totals"].get("unclassified_deposit_usd"))),
    ]
    for row_number, (label, value) in enumerate(measures, start=5):
        sheet.cell(row_number, 1, label)
        cell = sheet.cell(row_number, 2, value)
        if isinstance(value, (float, int)) and row_number >= 8:
            cell.number_format = MONEY_FORMAT
        if row_number % 2 == 1:
            sheet.cell(row_number, 1).fill = PatternFill("solid", fgColor=PALE)
            cell.fill = PatternFill("solid", fgColor=PALE)
    sheet["A17"] = "N/D: el histórico no registra cobranza ni deducción; no equivale a cero."
    sheet["A17"].font = Font(name="Arial", size=10, italic=True, color="5A6673")
    summary_rows = []
    for period in periods:
        historical_mode = period.get("reconciliation_mode") == "Historica"
        first_date, last_date = _historical_dates(period) if historical_mode else (None, None)
        summary_rows.append((
            period.get("name"), period.get("employer_name"), period.get("month"),
            period.get("reconciliation_mode"), period.get("historical_scope") if historical_mode else None,
            first_date, last_date, _date(period.get("remittance_due_date")),
            _control_state(period.get("control_state")),
            NA if historical_mode else _money(period.get("expected_usd")),
            NA if historical_mode else _money(period.get("deducted_usd")),
            _money(period.get("applied_usd")), _money(period.get("complementary_usd")),
            _money(period.get("rounding_adjustment_usd")), _money(period.get("remitted_usd")),
            _money(period.get("historical_pending_usd")) if historical_mode else NA,
            NA if historical_mode else _money(period.get("worker_gap_usd")),
            NA if historical_mode else _money(period.get("employer_gap_usd")),
            _money(period.get("unclassified_deposit_usd")),
            len(period.get("exceptions") or []),
            _date(period.get("control_cut_on")),
            period.get("control_cut_note"),
            period.get("control_cut_summary"),
        ))
    _table(sheet, SUMMARY_HEADERS, summary_rows, header_row=19,
           money_columns=set(range(10, 20)), date_columns={6, 7, 8, 21})
    sheet.column_dimensions["A"].width = 31
    sheet.column_dimensions["B"].width = 24
    sheet.column_dimensions["E"].width = 27
    sheet.column_dimensions["U"].width = 23
    sheet.column_dimensions["V"].width = 40
    sheet.column_dimensions["W"].width = 65
    sheet.tabColor = NAVY


def _historical_dates(period):
    scope = period.get("historical_scope")
    if scope == "Fecha exacta":
        day = _date(period.get("historical_application_date"))
        return day, day
    if scope == "Rango de fechas":
        return _date(period.get("historical_start_date")), _date(period.get("historical_end_date"))
    return None, None


def _control_state(value):
    return {
        "historico_excepcion": "Histórico con excepción",
        "historico_excedente": "Histórico con excedente",
        "historico_conciliado": "Histórico conciliado",
        "historico_parcial": "Histórico parcial",
        "historico_pendiente": "Histórico pendiente",
        "diferencia": "Diferencia",
        "pendiente_detalle": "Pendiente detalle",
        "conciliado": "Conciliado",
        "parcial": "Parcial",
        "en_transito": "En tránsito",
        "excedente": "Excedente",
    }.get(value, value)


def _collection_detail(period, claim):
    comments = " | ".join(str(item) for item in (
        claim.get("comments"), claim.get("application_comment"),
        claim.get("inherited_exception_comment"),
    ) if item)
    return (
        period.get("name"), period.get("employer_name"), "Operativa",
        claim.get("client_number"), claim.get("employee_number"), claim.get("client_name"),
        claim.get("national_id"), claim.get("loan_number"), claim.get("installment_number"),
        None, None, None, claim.get("application_reference"),
        _money(claim.get("expected_usd")), _money(claim.get("deducted_usd")),
        _money(claim.get("applied_usd")), _money(claim.get("complementary_usd")),
        _money(claim.get("rounding_adjustment_usd")), _money(claim.get("remitted_usd")),
        _money(claim.get("employee_receivable_usd")) if claim.get("employee_receivable_usd") is not None else NA,
        NA,
        " / ".join(str(item) for item in (claim.get("deduction_status"), claim.get("application_status")) if item),
        comments,
    )


def _historical_detail(period, application):
    return (
        period.get("name"), period.get("employer_name"), "Histórica",
        application.get("client_number"), application.get("employee_number"), application.get("client_name"),
        application.get("national_id"), application.get("loan_number"), application.get("installment_number"),
        _date(application.get("event_date")), application.get("accounting_entry"), application.get("receipt"),
        application.get("reference"), NA, NA, _money(application.get("amount")),
        0, 0, _money(application.get("historical_remitted_usd")), NA,
        _money(application.get("historical_balance_usd")),
        application.get("deposit_match_status"), None,
    )


def _claim_links(period, claim, *, historical):
    if historical:
        details = _json_list(claim.get("historical_detail"))
        claim_id = claim.get("name")
        application_reference = claim.get("reference")
    else:
        details = _json_list(claim.get("remittance_detail"))
        claim_id = claim.get("row_key")
        application_reference = claim.get("application_reference")
    rows = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        inherited = detail.get("excepciones_heredadas") or []
        inherited_text = " | ".join(
            str(note.get("comment") or "") for note in inherited if isinstance(note, dict)
        ) if isinstance(inherited, list) else ""
        rows.append((
            period.get("name"), period.get("employer_name"),
            "Histórica" if historical else "Operativa",
            claim.get("client_number"), claim.get("client_name"), claim.get("loan_number"),
            claim_id, application_reference, detail.get("referencia"), detail.get("comprobante"),
            _date(detail.get("fecha")),
            detail.get("destino") or ("Aplicación histórica" if historical else "Cobranza"),
            _money(detail.get("importe_usd")),
            _money(detail.get("diferencia_usd")) if detail.get("diferencia_usd") is not None else None,
            detail.get("origen"), inherited_text or claim.get("inherited_exception_comment"),
        ))
    return rows


def _issue(issue_type, period, client, loan, reference, amount, status, *,
           employer=None, cause=None, owner=None, action=None, commitment=None,
           evidence=None, external_reference=None, detail=None, resolution=None,
           event_date=None):
    return (
        issue_type, _date(event_date), period.get("name") if period else None,
        period.get("employer_name") if period else employer,
        client, loan, reference, amount, status, cause, owner, action,
        _date(commitment), evidence, external_reference, detail, resolution,
        _date(period.get("remittance_due_date")) if period else None,
    )


def _action_rows(actions, exceptions, period_by_name):
    exception_periods = {item.get("name"): item.get("period") for item in exceptions}
    result = []
    for action in actions:
        period = period_by_name.get(exception_periods.get(action.get("parent")))
        result.append((
            action.get("parent"), period.get("name") if period else None,
            period.get("employer_name") if period else None,
            _date(action.get("action_at")), action.get("action_by"),
            action.get("action_type"), action.get("details"),
            action.get("evidence_file"), action.get("external_reference"),
        ))
    return result


def _commitment_delay(issue_row, as_of):
    # This is a follow-up deadline, not the employer's contractual payment date.
    if issue_row[0] != "Excepción" or issue_row[8] not in {"Abierta", "En revision"}:
        return None
    commitment = _date(issue_row[12])
    if not isinstance(commitment, date):
        return None
    day = commitment.date() if isinstance(commitment, datetime) else commitment
    return max((as_of - day).days, 0)


def _json_list(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _money(value):
    return money_float(value)


def _date(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)) if len(str(value)) > 10 else date.fromisoformat(str(value))
    except ValueError:
        return value


def _safe_text(value):
    if not isinstance(value, str):
        return value
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _base_sheet(sheet, title):
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = title
    sheet["A1"].font = Font(name="Arial", size=15, bold=True, color=NAVY)
    sheet.row_dimensions[1].height = 27


def _write_table(sheet, title, scope, headers, rows, *, money_columns=(), date_columns=()):
    _base_sheet(sheet, title)
    sheet["A2"] = _safe_text(scope)
    sheet["A2"].font = Font(name="Arial", size=10, italic=True, color="5A6673")
    _table(sheet, headers, rows, header_row=4,
           money_columns=money_columns, date_columns=date_columns)
    sheet.freeze_panes = "E5" if len(headers) > 12 else "A5"


def _table(sheet, headers, rows, *, header_row, money_columns=(), date_columns=()):
    if header_row + len(rows) > MAX_EXCEL_ROWS:
        raise ValueError("El informe supera el máximo de filas permitido por Excel.")
    for column, heading in enumerate(headers, start=1):
        cell = sheet.cell(header_row, column, heading)
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        width = 17
        if any(text in heading.lower() for text in ("nombre", "detalle", "comentario", "acción", "resolución")):
            width = 35
        elif any(text in heading.lower() for text in ("referencia", "evidencia", "asiento", "comprobante")):
            width = 25
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[header_row].height = 36
    for row_number, values in enumerate(rows, start=header_row + 1):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_number, column, _safe_text(value))
            cell.font = Font(name="Arial", size=10, color=TEXT)
            cell.alignment = Alignment(vertical="center")
            if column in money_columns and isinstance(value, (int, float)):
                cell.number_format = MONEY_FORMAT
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif column in money_columns and value == NA:
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif column in date_columns and isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT if isinstance(value, date) and not isinstance(value, datetime) else "dd/mm/yyyy hh:mm"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif isinstance(value, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            if row_number % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F7F9FC")
        sheet.row_dimensions[row_number].height = 20
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{max(header_row, header_row + len(rows))}"
