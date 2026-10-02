"""Pure file parsers for the standalone Credinomina reconciliation app.

This module intentionally has no Frappe imports so it can be tested with the
real source files before the app is installed on a site.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from credinomina_reconciliation.rounding import (
    RATE_PRECISION, decimal_value, money, money_float,
)
from credinomina_reconciliation.accounting_types import DEPOSIT, accounting_code, classify_movement


SOURCE_ACCOUNTING = "Movimientos contables"


class SourceFileError(ValueError):
    pass


KNOWN_MOJIBAKE = {
    "c\ufffddula": "cedula",
    "cr\ufffddito": "credito",
    "aplicaci\ufffdn": "aplicacion",
    "dep\ufffdsito": "deposito",
}


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    for broken, repaired in KNOWN_MOJIBAKE.items():
        text = text.replace(broken, repaired)
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().lstrip("'")


def normalize_credit_number(value: Any) -> str:
    """Use the core's default cycle only for credit numbers without a suffix."""
    number = clean_text(value)
    return f"{number}-1" if re.fullmatch(r"\d+", number) else number


def has_legacy_numeric_credit_numbers(credit_numbers: Iterable[Any]) -> bool:
    """Return whether stored portfolio rows still need the default ``-1`` suffix."""
    return any(clean_text(number).isdigit() for number in credit_numbers)


def canonical_identifier(value: Any) -> str:
    """Normalize deterministic spreadsheet formatting, never names or fuzzy text."""
    text = clean_text(value)
    if re.fullmatch(r"\d+", text):
        return text.lstrip("0") or "0"
    return text.casefold()


def _parse_decimal(value: Any, *, is_rate: bool = False) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return decimal_value(value)
    text = clean_text(value)
    text = re.sub(r"[^0-9,().+\-]", "", text)
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if not text:
        return Decimal(0)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        last = text.rsplit(",", 1)[-1]
        text = (
            text.replace(",", ".")
            if is_rate or len(last) <= 2 else text.replace(",", "")
        )
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise SourceFileError(f"Importe no valido: {value!r}") from exc
    return -result if negative else result


def parse_amount(value: Any) -> float:
    """Parse a monetary value and round it half-up to the nearest cent."""
    return money_float(_parse_decimal(value))


def parse_exchange_rate(value: Any) -> float:
    """Parse an exchange rate without applying the two-decimal money rule."""
    try:
        return float(_parse_decimal(value, is_rate=True))
    except SourceFileError as exc:
        raise SourceFileError(f"Tipo de cambio no valido: {value!r}") from exc


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_key(*parts: Any) -> str:
    payload = "|".join(clean_text(part).casefold() for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_table(
    file_name: str, content: bytes, *, sheet_name: str | None = None
) -> list[list[Any]]:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise SourceFileError("Se requiere openpyxl para leer archivos .xlsx.") from exc
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            sheet = workbook.active
            if sheet_name:
                sheet = next(
                    (item for item in workbook.worksheets
                     if normalize_header(item.title) == normalize_header(sheet_name)),
                    None,
                )
                if sheet is None:
                    if len(workbook.worksheets) == 1:
                        sheet = workbook.worksheets[0]
                    else:
                        raise SourceFileError(f"No se encontró la pestaña {sheet_name}.")
            return [list(row) for row in sheet.iter_rows(values_only=True)]
        finally:
            workbook.close()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise SourceFileError("Se requiere xlrd para leer archivos .xls.") from exc
        workbook = xlrd.open_workbook(file_contents=content)
        sheet = workbook.sheet_by_index(0)
        if sheet_name:
            sheet = next(
                (workbook.sheet_by_index(index) for index in range(workbook.nsheets)
                 if normalize_header(workbook.sheet_by_index(index).name)
                 == normalize_header(sheet_name)),
                None,
            )
            if sheet is None:
                if workbook.nsheets == 1:
                    sheet = workbook.sheet_by_index(0)
                else:
                    raise SourceFileError(f"No se encontró la pestaña {sheet_name}.")
        result = []
        for row_index in range(sheet.nrows):
            row = []
            for column_index in range(sheet.ncols):
                cell = sheet.cell(row_index, column_index)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(cell.value, workbook.datemode))
                else:
                    row.append(cell.value)
            result.append(row)
        return result
    if suffix == ".csv":
        text = content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [list(row) for row in csv.reader(io.StringIO(text), dialect)]
    raise SourceFileError("Use un archivo .xlsx, .xls o .csv.")


COLLECTION_ALIASES = {
    "employer": {"empresa", "empresa_beneficiaria", "empresa_de_convenio"},
    "row_key": ("fila_id", "row_id", "id_fila"),
    "client_number": ("nro_cliente", "numero_cliente", "no_cliente", "customer_id"),
    "employee_number": ("nro_empleado", "numero_empleado", "no_empleado", "codigo_empleado", "cod_empleado", "nro_de_empleado"),
    "client_name": (
        "nombre_y_apellidos_del_cliente",
        "nombre_del_cliente",
        "cliente",
        "customer_name",
    ),
    "national_id": ("nro_cedula", "numero_cedula", "cedula", "identificacion"),
    "loan_number": (
        "nro_credito",
        "numero_credito",
        "no_credito",
        "credito",
        "loan",
    ),
    "installment_number": ("nro_cuota", "numero_cuota", "no_cuota"),
    "total_installments": (
        "nro_de_cuotas_totales",
        "numero_de_cuotas_totales",
        "cuotas_totales",
    ),
    "expected_usd": ("monto_de_la_cuota_en_us", "monto_de_la_cuota_en_usd", "cuota_usd"),
    "expected_nio": ("monto_de_la_cuota_en_c", "monto_de_la_cuota_en_cs", "monto_de_la_cuota_en_nio", "cuota_nio"),
    "comments": ("comentarios", "comentario", "observacion", "observaciones"),
    "application_reference": ("referencia_de_aplicacion", "referencia_aplicacion"),
    "application_comment": ("comentario_de_aplicacion", "comentario_aplicacion"),
    "deducted_nio": ("deducido_c", "deducido_cs", "deducido_nio", "monto_deducido_nio"),
    "deducted_usd": ("deducido_us", "deducido_usd", "monto_deducido_usd"),
}


def header_mapping(row: Iterable[Any], aliases: dict[str, tuple[str, ...]]) -> dict[str, int]:
    normalized = [normalize_header(value) for value in row]
    result = {}
    for fieldname, candidates in aliases.items():
        for candidate in candidates:
            if candidate in normalized:
                result[fieldname] = normalized.index(candidate)
                break
    return result


def _value(row: list[Any], mapping: dict[str, int], fieldname: str) -> Any:
    index = mapping.get(fieldname)
    return row[index] if index is not None and index < len(row) else None


def parse_collection_file(
    file_name: str, content: bytes, *, require_deduction: bool = False,
    keep_zero_rows: bool = False, require_name: bool = False,
) -> list[dict[str, Any]]:
    rows = read_table(file_name, content)
    mapping: dict[str, int] | None = None
    parsed: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=1):
        candidate = header_mapping(row, COLLECTION_ALIASES)
        if (
            "client_name" in candidate
            or "loan_number" in candidate and (
                "client_number" in candidate or "national_id" in candidate
            )
        ):
            mapping = candidate
            continue
        if not mapping:
            continue
        loan_number = clean_text(_value(row, mapping, "loan_number"))
        client_number = clean_text(_value(row, mapping, "client_number"))
        employee_number = clean_text(_value(row, mapping, "employee_number"))
        national_id = clean_text(_value(row, mapping, "national_id"))
        client_name = clean_text(_value(row, mapping, "client_name"))
        expected_usd = parse_amount(_value(row, mapping, "expected_usd"))
        expected_nio = parse_amount(_value(row, mapping, "expected_nio"))
        deducted_usd = parse_amount(_value(row, mapping, "deducted_usd"))
        deducted_nio = parse_amount(_value(row, mapping, "deducted_nio"))
        if not any((loan_number, client_number, employee_number, national_id, client_name)):
            if require_name and any((expected_usd, expected_nio, deducted_usd, deducted_nio)):
                raise SourceFileError(
                    f"La fila {row_number} tiene importe pero no tiene Nombre y Apellidos del Cliente."
                )
            continue
        if require_name and not client_name:
            raise SourceFileError(
                f"La fila {row_number} no tiene Nombre y Apellidos del Cliente."
            )
        if not keep_zero_rows and not any((expected_usd, expected_nio, deducted_usd, deducted_nio)):
            continue
        if require_deduction and not (
            "deducted_usd" in mapping or "deducted_nio" in mapping
        ):
            raise SourceFileError(
                "El detalle de la empresa debe contener Deducido C$ o Deducido US$."
            )
        parsed.append(
            {
                "source_row": row_number,
                "row_key": clean_text(_value(row, mapping, "row_key")),
                "employer": clean_text(_value(row, mapping, "employer")),
                "client_number": client_number,
                "employee_number": employee_number,
                "client_name": client_name,
                "national_id": national_id,
                "loan_number": loan_number,
                "installment_number": clean_text(_value(row, mapping, "installment_number")),
                "total_installments": clean_text(_value(row, mapping, "total_installments")),
                "expected_usd": expected_usd,
                "expected_nio": expected_nio,
                "comments": clean_text(_value(row, mapping, "comments")),
                "application_reference": clean_text(
                    _value(row, mapping, "application_reference")
                ),
                "application_comment": clean_text(
                    _value(row, mapping, "application_comment")
                ),
                "deducted_usd": deducted_usd,
                "deducted_nio": deducted_nio,
            }
        )
    if not parsed:
        raise SourceFileError("No se encontraron filas de cobranza reconocibles.")
    return parsed


def _records_from_header(rows: list[list[Any]], required_header: str) -> list[tuple[int, dict[str, Any]]]:
    mapping = None
    records = []
    for row_number, row in enumerate(rows, start=1):
        normalized = [normalize_header(value) for value in row]
        if required_header in normalized:
            mapping = {header: index for index, header in enumerate(normalized) if header}
            continue
        if not mapping or not any(value not in (None, "") for value in row):
            continue
        records.append(
            (
                row_number,
                {
                    header: row[index] if index < len(row) else None
                    for header, index in mapping.items()
                },
            )
        )
    return records


def _extract_reference(description: str, fallback: Any = None) -> str:
    match = re.search(r"\|\s*REF\s*:\s*([^|]+)\|", description, re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    match = re.search(r"\|\s*([^|]+)\s*\|", description)
    return clean_text(match.group(1) if match else fallback)


def _extract_employer(description: str, fallback: Any = None) -> str:
    match = re.search(
        r"CONVENIO\s+(.+?)(?:\s*\(|\s+EN\s+LA\s+CUENTA|\s+-|\s+\||$)",
        description,
        re.IGNORECASE,
    )
    return clean_text(match.group(1) if match else fallback)


def _extract_application_client_name(description: str) -> str:
    match = re.search(
        r"\bCLIENTE\s*[:=]?\s*(.+?)(?:\s+N\.?\s*C\.?(?:\s|$)|\s+CONVENIO\b|\s+PAGO\s+APLICADO\b|\s+-)",
        description, re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else ""


def _extract_receipt(description: str) -> str:
    match = re.search(r"\bNO\.?\s*DOCUM(?:ENTO)?\.?\s*[:#]?\s*([A-Z0-9-]+)", description, re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def _account_description(record: dict[str, Any]) -> str:
    return clean_text(
        record.get("descripcion_cta_contable") or record.get("descripcion_cuenta")
    ).upper()


def _native_deposit_amount(description: str, fallback: Any) -> tuple[str, float]:
    match = re.search(r"\b(U\$|US\$|C\$)\s*([0-9.,]+)", description, re.IGNORECASE)
    if match:
        currency = "USD" if "U" in match.group(1).upper() else "NIO"
        return currency, parse_amount(match.group(2))
    return "USD", parse_amount(fallback)


def parse_accounting_movements(file_name: str, content: bytes) -> list[dict[str, Any]]:
    records = _records_from_header(read_table(file_name, content), "cuenta_contable")
    parsed = []
    for row_number, record in records:
        description = clean_text(record.get("descripcion"))
        if not re.fullmatch(r"\d[\d.-]*", clean_text(record.get("cuenta_contable"))):
            continue
        upper = description.upper()
        event_type = None
        loan_number = ""
        reference = ""
        employer = ""
        source_loan_number = clean_text(record.get("no_credito"))
        debit = parse_amount(record.get("debito_del_mes"))
        credit = parse_amount(record.get("credito_del_mes"))
        if not debit and not credit:
            continue
        tmov, tdoc = accounting_code(record.get("tmov")), accounting_code(record.get("tdoc"))
        classification, is_payment, reason = classify_movement(tmov, tdoc, debit, credit, description)
        if tmov or tdoc or "DEPOSITO POR" not in upper:
            event_type = "Deposito" if classification == DEPOSIT else "Aplicacion" if is_payment else "Ajuste"
            match = re.search(
                r"(?:NOTA\s+AL\s+)?PRESTAMO\s+0*([A-Z0-9-]+)",
                description, re.IGNORECASE,
            )
            loan_number = source_loan_number or clean_text(match.group(1) if match else "")
            reference = _extract_reference(description, record.get("no_ref"))
            account_name = _account_description(record)
            currency = "NIO" if "M.N" in account_name else "USD"
            amount = debit if is_payment else float(abs(money(debit) - money(credit)) or max(abs(money(debit)), abs(money(credit))))
            employer = clean_text(record.get("empresa")) or _extract_employer(description)
        elif "DEPOSITO POR" in upper:
            event_type = "Deposito"
            reference = clean_text(record.get("no_ref"))
            currency, amount = _native_deposit_amount(
                description, record.get("credito_del_mes")
            )
            employer = _extract_employer(description)
        else:
            continue
        if amount <= 0:
            continue
        event_date = parse_date(record.get("fecha_aplica"))
        equivalent_currency = ""
        equivalent_amount = 0.0
        fx_basis = ""
        if event_type == "Deposito" and classification != DEPOSIT:
            account_name = _account_description(record)
            accounting_currency = (
                "USD" if "M.E." in account_name else "NIO" if "M.N." in account_name else ""
            )
            accounting_amount = parse_amount(record.get("credito_del_mes"))
            if accounting_currency and accounting_currency != currency and accounting_amount > 0:
                equivalent_currency = accounting_currency
                equivalent_amount = accounting_amount
                fx_basis = "Importe del movimiento contable"
        parsed.append(
            _source_record(
                row_number=row_number,
                event_type=event_type,
                event_date=event_date,
                reference=reference,
                voucher=record.get("no_cmpte"),
                accounting_entry=record.get("no_cmpte") if event_type != "Deposito" else "",
                receipt=_extract_receipt(description) if event_type != "Deposito" else "",
                employer=employer,
                client_name=(
                    clean_text(record.get("nombre_cliente"))
                    or _extract_application_client_name(description)
                    if event_type != "Deposito" else (clean_text(record.get("nombre_cliente")) if clean_text(record.get("nombre_cliente")) not in {"0", "N/A"} else "")
                ),
                loan_number=loan_number,
                currency=currency,
                amount=amount,
                description=description,
                equivalent_currency=equivalent_currency,
                equivalent_amount=equivalent_amount,
                fx_basis=fx_basis,
            )
        )
        parsed[-1].update({
            "_csv_employer_assignment": clean_text(record.get("cn_empresa_asignada")),
            "source_description": "" if record.get("descripcion") is None else str(record["descripcion"]),
            "tmov": tmov, "tdoc": tdoc,
            "accounting_classification": classification, "classification_reason": reason,
            "source_classification": clean_text(record.get("clasificacion")),
            "source_account": clean_text(record.get("cuenta_contable")),
            "source_debit": debit, "source_credit": credit,
            "accounting_reference": clean_text(record.get("no_ref")),
            "accounting_source_key": source_key(
                "accounting-evidence-v1", record.get("cuenta_contable"), event_date, tmov, tdoc,
                record.get("no_cmpte"), record.get("no_ref"), description, debit, credit,
            ),
        })
        if classification == DEPOSIT:
            from credinomina_reconciliation.deposit_evidence import extract_deposit
            parsed[-1].update(extract_deposit(record, description))
    if not parsed:
        raise SourceFileError("No se encontraron movimientos contables con importe.")
    return parsed


def apply_accounting_currency_override(
    records: list[dict[str, Any]],
    currency: Any,
    manual_fx_rate: Any = 0,
) -> list[dict[str, Any]]:
    """Normalize an accounting file to the currency selected on its import.

    Applications and deposits are reconciled in USD. For a NIO file, preserve
    the original amount in ``amount_nio`` and use a source-provided USD
    equivalent where available; otherwise use the file-level rate.
    """
    selected_currency = clean_text(currency).upper()
    rate_decimal = _parse_decimal(manual_fx_rate, is_rate=True)
    rate = float(rate_decimal)
    if not selected_currency:
        raise SourceFileError("Seleccione la moneda reportada en el archivo antes de cargarlo.")
    if selected_currency not in {"USD", "NIO"}:
        raise SourceFileError("La moneda del archivo debe ser USD o NIO.")
    if selected_currency == "NIO" and rate <= 0:
        raise SourceFileError("Ingrese el tipo de cambio manual en C$ por US$ para el archivo NIO.")
    if selected_currency == "USD" and rate:
        raise SourceFileError("La tasa manual solo se utiliza cuando la moneda del archivo es NIO.")

    for record in records:
        if record.get("event_type") not in {"Aplicacion", "Deposito", "Ajuste"}:
            continue
        source_amount = money(record.get("amount"))
        record["source_currency"] = selected_currency
        if selected_currency == "USD":
            record.update({
                "currency": "USD",
                "amount": float(source_amount),
                "amount_usd": float(source_amount),
                "amount_nio": 0,
                "equivalent_currency": "",
                "equivalent_amount": 0,
                "fx_rate": 0,
                "fx_basis": "",
                "manual_fx_rate": 0,
                "source_fx_rate": 0,
            })
            continue

        source_fx_basis = clean_text(record.get("fx_basis"))
        source_usd_equivalent = (
            money(record.get("equivalent_amount"))
            if record.get("equivalent_currency") == "USD" and source_fx_basis
            else Decimal(0)
        )
        used_source_equivalent = source_usd_equivalent > 0
        amount_usd = money(
            source_usd_equivalent
            if used_source_equivalent else source_amount / rate_decimal
        )
        record.update({
            "currency": "USD",
            "amount": float(amount_usd),
            "amount_usd": float(amount_usd),
            "amount_nio": float(source_amount),
            "equivalent_currency": "NIO",
            "equivalent_amount": float(source_amount),
            "fx_rate": (
                float((source_amount / amount_usd).quantize(RATE_PRECISION, rounding=ROUND_HALF_UP))
                if used_source_equivalent and amount_usd
                else 0
            ),
            "fx_basis": source_fx_basis if used_source_equivalent else "",
            "manual_fx_rate": 0 if used_source_equivalent else rate,
        })
        record["source_fx_rate"] = record["manual_fx_rate"] or record["fx_rate"]
    return records


def _source_record(
    *,
    row_number: int,
    event_type: str,
    event_date: date | None,
    reference: Any,
    voucher: Any = None,
    accounting_entry: Any = None,
    receipt: Any = None,
    employer: Any = None,
    client_number: Any = None,
    employee_number: Any = None,
    client_name: Any = None,
    national_id: Any = None,
    loan_number: Any = None,
    installment_number: Any = None,
    currency: str,
    amount: float,
    description: Any = None,
    equivalent_currency: str = "",
    equivalent_amount: float = 0,
    fx_basis: str = "",
) -> dict[str, Any]:
    cleaned = {
        "source_row": row_number,
        "event_type": event_type,
        "event_date": event_date,
        "reference": clean_text(reference),
        "voucher": clean_text(voucher),
        "accounting_entry": clean_text(accounting_entry),
        "receipt": clean_text(receipt),
        "employer_text": clean_text(employer),
        "client_number": clean_text(client_number),
        "employee_number": clean_text(employee_number),
        "client_name": clean_text(client_name),
        "national_id": clean_text(national_id),
        "loan_number": clean_text(loan_number),
        "installment_number": clean_text(installment_number),
        "currency": currency,
        "amount": money_float(amount),
        "description": clean_text(description),
        "equivalent_currency": equivalent_currency,
        "equivalent_amount": money_float(equivalent_amount or 0),
        "fx_basis": fx_basis,
    }
    cleaned["fx_rate"] = 0.0
    if cleaned["amount"] > 0 and cleaned["equivalent_amount"] > 0:
        if currency == "NIO" and equivalent_currency == "USD":
            cleaned["fx_rate"] = float((money(cleaned["amount"]) / money(cleaned["equivalent_amount"])).quantize(RATE_PRECISION, rounding=ROUND_HALF_UP))
        elif currency == "USD" and equivalent_currency == "NIO":
            cleaned["fx_rate"] = float((money(cleaned["equivalent_amount"]) / money(cleaned["amount"])).quantize(RATE_PRECISION, rounding=ROUND_HALF_UP))
    cleaned["amount_usd"] = cleaned["amount"] if currency == "USD" else 0
    cleaned["amount_nio"] = cleaned["amount"] if currency == "NIO" else 0
    cleaned["source_key"] = source_key(
        event_type,
        event_date,
        cleaned["reference"],
        cleaned["loan_number"],
        currency,
        cleaned["amount"],
    )
    return cleaned


def parse_source_file(source_type: str, file_name: str, content: bytes) -> list[dict[str, Any]]:
    if source_type == SOURCE_ACCOUNTING:
        return parse_accounting_movements(file_name, content)
    raise SourceFileError(f"Tipo de fuente no soportado: {source_type}")


PORTFOLIO_SOURCE_FIELDS = (
    "fecha_reporte", "sucursal", "codigo_verificacion", "agencia", "metodologia",
    "fecha_desembolso", "fecha_vencimiento", "no_credito", "ciclo",
    "no_cliente_migrado", "no_cliente_siaf", "nombre_cliente", "genero",
    "direcc_domicilio", "direcc_trabajo", "estado_credito",
    "agrupacion_crediticia", "actividad_economica", "producto_credito", "moneda",
    "dias_en_mora", "cuotas_mora", "tasa_interes_corriente",
    "tasa_interes_moratoria", "comision_desembolso", "tcea", "saldo_principal",
    "saldo_mant_valor", "saldo_intereses", "saldo_intereses_mora", "saldo_cargos",
    "saldo_comision", "monto_desembolsado", "segregacion", "empresa_de_convenio",
    "no_identificacion", "tipo_identificacion", "fecha_nacimiento", "destino_credito",
    "no_dependientes", "generacion_de_empleo", "es_reestructurado", "tipo_garantia",
    "monto_garantia", "plazo_credito", "numero_cuotas", "cuotas_pagadas", "municipio",
    "departamento", "asesor_credito", "periodicidad", "clasificacion", "porc_provision",
    "provision_principal", "provision_interes", "fecha_saneamiento",
    "fecha_estado_vencido", "monto_mant_valor_dev", "monto_interes_devengado",
    "monto_mora_devengada", "monto_comis_devengada", "monto_cargo_devengado",
    "monto_princ_pag_total", "monto_mant_v_pag_total", "monto_interes_pag_total",
    "monto_mora_pag_total", "monto_com_pag_total", "monto_cargo_pag_total",
    "monto_princ_pagado_mes", "monto_mant_pagado_mes", "monto_interes_pag_mes",
    "monto_int_mora_pag_mes", "monto_comision_pag_mes", "monto_cargo_pagado_mes",
    "monto_mora_dispensado", "monto_int_dispensado", "monto_cargo_dispensado",
    "monto_princ_saneado", "monto_mant_v_saneado", "monto_interes_saneado",
    "monto_int_mora_saneado", "monto_cargo_saneado", "principal_vencido",
    "interes_vencido", "tipo_cambio_fecha_desemb", "tipo_cambio_fecha_reporte",
    "creditos_refinanciados", "monto_refinanciado", "garantia_hipotecaria",
    "garantia_prendaria", "garantia_fiduciaria", "otras_garantias",
    "monto_garantia_hipotecaria", "monto_garantia_prendaria",
    "monto_garantia_fiduciaria", "monto_otras_garantias", "fecha_ult_pago_principal",
    "fecha_ult_pago_interes", "monto_refinanciado_principal", "es_convenio",
)
PORTFOLIO_SOURCE_FIELD_SET = frozenset(PORTFOLIO_SOURCE_FIELDS)
PORTFOLIO_DATE_FIELDS = frozenset({
    "fecha_reporte", "fecha_desembolso", "fecha_vencimiento", "fecha_nacimiento",
    "fecha_saneamiento", "fecha_estado_vencido", "fecha_ult_pago_principal",
    "fecha_ult_pago_interes",
})
PORTFOLIO_MONEY_FIELDS = frozenset({
    "comision_desembolso", "saldo_principal", "saldo_mant_valor",
    "saldo_intereses", "saldo_intereses_mora", "saldo_cargos",
    "saldo_comision", "monto_desembolsado", "monto_garantia",
    "provision_principal", "provision_interes", "monto_mant_valor_dev",
    "monto_interes_devengado", "monto_mora_devengada",
    "monto_comis_devengada", "monto_cargo_devengado", "monto_princ_pag_total",
    "monto_mant_v_pag_total", "monto_interes_pag_total", "monto_mora_pag_total",
    "monto_com_pag_total", "monto_cargo_pag_total", "monto_princ_pagado_mes",
    "monto_mant_pagado_mes", "monto_interes_pag_mes", "monto_int_mora_pag_mes",
    "monto_comision_pag_mes", "monto_cargo_pagado_mes", "monto_mora_dispensado",
    "monto_int_dispensado", "monto_cargo_dispensado", "monto_princ_saneado",
    "monto_mant_v_saneado", "monto_interes_saneado", "monto_int_mora_saneado",
    "monto_cargo_saneado", "principal_vencido", "interes_vencido",
    "monto_refinanciado", "garantia_hipotecaria", "garantia_prendaria",
    "garantia_fiduciaria", "otras_garantias", "monto_garantia_hipotecaria",
    "monto_garantia_prendaria", "monto_garantia_fiduciaria", "monto_otras_garantias",
    "monto_refinanciado_principal",
})


def portfolio_source_values_from_raw_data(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Map original portfolio headers to typed child-row fields."""
    values = {}
    for header, value in (raw_data or {}).items():
        fieldname = normalize_header(header)
        if fieldname not in PORTFOLIO_SOURCE_FIELD_SET:
            continue
        # Frappe stores numeric fields as NOT NULL columns. Empty source cells
        # should leave the DocField default untouched (and remain available in
        # raw_data), rather than writing SQL NULL during import or backfill.
        if value in (None, ""):
            continue
        if fieldname in PORTFOLIO_DATE_FIELDS:
            parsed = parse_date(value)
            if parsed is None and isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value).date()
                except ValueError:
                    pass
            if parsed is None:
                continue
            value = parsed
        elif fieldname in PORTFOLIO_MONEY_FIELDS:
            value = parse_amount(value)
        values[fieldname] = value
    return values


def _portfolio_json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not (float("-inf") < value < float("inf")):
        return None
    return value


def parse_credit_portfolio(file_name: str, content: bytes) -> list[dict[str, Any]]:
    """Parse a monthly credit cut while preserving every original column.

    The source report is allowed to have title rows above the table. Operational
    fields are extracted for lookups, each recognized source column is mapped
    to a child-row field, and ``raw_data`` retains source values for traceability.
    """
    rows = read_table(file_name, content)
    mapping = None
    original_headers = []
    parsed = []
    seen_credits = set()

    for row_number, row in enumerate(rows, start=1):
        normalized = [normalize_header(value) for value in row]
        if {"fecha_reporte", "no_credito", "nombre_cliente"}.issubset(normalized):
            mapping = {header: index for index, header in enumerate(normalized) if header}
            original_headers = []
            used_headers = set()
            for index, value in enumerate(row):
                if value in (None, ""):
                    original = f"Columna {index + 1}"
                else:
                    original = str(value).strip()
                candidate = original
                suffix = 2
                while candidate in used_headers:
                    candidate = f"{original} [{suffix}]"
                    suffix += 1
                used_headers.add(candidate)
                original_headers.append(candidate)
            continue

        if not mapping or not any(value not in (None, "") for value in row):
            continue

        def value(fieldname):
            index = mapping.get(fieldname)
            return row[index] if index is not None and index < len(row) else None

        credit_number = normalize_credit_number(value("no_credito"))
        client_name = clean_text(value("nombre_cliente"))
        if not credit_number and not client_name:
            continue
        if credit_number:
            credit_key = canonical_identifier(credit_number)
            if credit_key in seen_credits:
                raise SourceFileError(
                    f"El crédito {credit_number} aparece más de una vez en el archivo."
                )
            seen_credits.add(credit_key)

        report_date = parse_date(value("fecha_reporte"))
        if not report_date:
            raise SourceFileError(
                f"La fila {row_number} no tiene una FECHA_REPORTE válida."
            )

        raw_data = {
            header: _portfolio_json_value(row[index] if index < len(row) else None)
            for index, header in enumerate(original_headers)
        }
        source_fields = portfolio_source_values_from_raw_data(raw_data)
        parsed.append({
            **source_fields,
            "source_row": row_number,
            "report_date": report_date,
            "credit_number": credit_number,
            "client_number_migrated": clean_text(value("no_cliente_migrado")),
            "client_number_core": clean_text(value("no_cliente_siaf")),
            "client_name": client_name,
            "credit_status": clean_text(value("estado_credito")),
            "employer_text": clean_text(value("empresa_de_convenio")),
            "national_id": clean_text(value("no_identificacion")),
            "is_convenio": clean_text(value("es_convenio")),
            "raw_data": json.dumps(raw_data, ensure_ascii=False, default=_portfolio_json_value),
        })

    if not parsed:
        raise SourceFileError(
            "No se encontraron filas de cartera. Se requieren FECHA_REPORTE, "
            "NO_CREDITO y NOMBRE_CLIENTE."
        )

    report_dates = {record["report_date"] for record in parsed}
    if len(report_dates) != 1:
        raise SourceFileError(
            "El archivo debe contener un solo corte: todas las filas deben compartir FECHA_REPORTE."
        )
    return parsed
