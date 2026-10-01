"""User-confirmed TMOV/TDOC classifications, separate from monetary effect."""

APPLICATION = "Aplicación de pago"
DEBIT_NOTE = "ND de Aplicación de pago"
INTERNAL = "Movimiento interno"
REVIEW = "Por revisar"

TYPES = {
    ("12", "05"): APPLICATION,
    ("12", "19"): APPLICATION,
    ("12", "06"): APPLICATION,
    ("12", "16"): DEBIT_NOTE,
    ("01", "01"): INTERNAL,
    ("12", "01"): INTERNAL,
    ("05", "01"): INTERNAL,
}


def accounting_code(value):
    text = str(value if value is not None else "").strip().lstrip("'")
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(2) if text.isdigit() else text


def classify_movement(tmov, tdoc, debit, credit, description=""):
    kind = TYPES.get((accounting_code(tmov), accounting_code(tdoc)), REVIEW)
    if kind == REVIEW:
        return kind, False, "TMOV/TDOC sin regla confirmada; revisar antes de conciliar"
    if kind == DEBIT_NOTE:
        return kind, False, "Nota de débito: identificar la aplicación original; no es un pago nuevo"
    if kind == INTERNAL:
        return kind, False, "Movimiento interno: revisar su concepto y vínculo antes de conciliar"
    if debit <= 0 or credit != 0 or "REVERS" in description.upper():
        return kind, False, "Ajuste o reversión de aplicación: revisar el efecto y la aplicación original"
    return kind, True, "Aplicación identificada por TMOV/TDOC y débito positivo sin crédito"
