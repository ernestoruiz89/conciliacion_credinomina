"""First-stage application quality; never evidence of payroll deduction or cash."""
from credinomina_reconciliation.rounding import decimal_value, money, money_float

PENDING_DEDUCTION = {None, "", "Pendiente de detalle", "Inferida por depósito"}
VALID_DEDUCTION = {"Deduccion total", "Deduccion parcial", "No deducido", "Deduccion en exceso"}
COLLECTION = "Cobranza"
EMPLOYER_DETAIL = "Detalle de empresa"
BASES = {COLLECTION, EMPLOYER_DETAIL}


def collection_quality(row, basis=COLLECTION):
    status = row.get("deduction_status")
    expected = money(row.get("expected_usd"))
    expected_nio = money(row.get("expected_nio"))
    reference = expected if basis in BASES else None
    if basis == EMPLOYER_DETAIL:
        if status not in VALID_DEDUCTION:
            reference = None
        else:
            reference = money(row.get("deducted_usd"))
            nio = money(row.get("deducted_nio"))
            if not reference and nio:
                reference = (money(nio * expected / expected_nio)
                             if expected > 0 and expected_nio > 0 else None)
    elif basis == COLLECTION and not expected and expected_nio:
        reference = None  # Missing conversion is not a zero-valued collection.
    if reference is None:
        return {"quality_basis": basis or "", "quality_status": "Revisar base de comparación",
                "quality_expected_usd": None, "quality_difference_usd": None}
    reference = money(max(reference - money(row.get("complementary_usd")), 0))
    difference = money(money(row.get("applied_usd")) - reference)
    result = ("Conforme según cobranza" if basis == "Cobranza" else "Conforme según deducción")
    if difference:
        result = "Aplicación insuficiente" if difference < 0 else "Aplicación en exceso"
    return {"quality_basis": basis, "quality_status": result,
            "quality_expected_usd": money_float(reference),
            "quality_difference_usd": money_float(difference)}


def update_collection_quality(rows, basis=COLLECTION):
    for row in rows:
        row.update(collection_quality(row, basis))


def quality_conforms(row, basis):
    return collection_quality(row, basis)["quality_status"].startswith("Conforme")


def source_quality(targets, period_bases=None):
    """A linked partial source is not proof that the complete quota was applied."""
    results = [collection_quality(row, period_bases.get(row.get("parent"))
               if period_bases is not None else COLLECTION) for row in targets]
    if not results:
        return {"quality_basis": "", "quality_status": "Sin cobranza vinculada"}
    bases = {row["quality_basis"] for row in results}
    basis = next(iter(bases)) if len(bases) == 1 else "Bases distintas por período"
    if any(row["quality_status"] == "Revisar base de comparación" for row in results):
        result = "Revisar base de comparación"
    elif any(decimal_value(row["quality_difference_usd"]) for row in results):
        result = "Con diferencias"
    elif len(bases) > 1:
        result = "Conforme según bases indicadas"
    else:
        result = results[0]["quality_status"]
    return {"quality_basis": basis, "quality_status": result}
