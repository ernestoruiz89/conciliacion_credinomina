"""Application cash-status vocabulary; historical labels remain readable."""

RENAMED = {
    "Depósito conciliado": "Conciliado",
    "Sin deposito": "No conciliado",
    "Depósito parcial": "Conciliación parcial",
    "Aplicación compensada": "Compensada",
}


def current(value):
    return RENAMED.get(value, value)


# Read old snapshots as well as migrated rows. These labels never establish
# cash coverage: the existing amount and link validations remain authoritative.
SETTLED = frozenset({"Conciliado", "Compensada", "Conciliada: depósito + ajuste",
                     "Depósito conciliado", "Aplicación compensada"})
