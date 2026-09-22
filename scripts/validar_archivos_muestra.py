"""Valida los cuatro formatos de entrada sin necesitar un sitio Frappe."""

from pathlib import Path
import sys

from credinomina_reconciliation.parsers import (
    SOURCE_ACCOUNTING,
    SOURCE_DEPOSITS,
    SOURCE_TRANSACTIONS,
    parse_collection_file,
    parse_source_file,
)


def main(arguments):
    if len(arguments) != 4:
        raise SystemExit(
            "Uso: validar_archivos_muestra.py COBRANZA MOVIMIENTOS TRANSACCIONES DEPOSITOS"
        )
    collection, accounting, transactions, deposits = map(Path, arguments)
    results = {
        "cobranza": len(
            parse_collection_file(collection.name, collection.read_bytes())
        ),
        "movimientos": len(
            parse_source_file(SOURCE_ACCOUNTING, accounting.name, accounting.read_bytes())
        ),
        "transacciones": len(
            parse_source_file(
                SOURCE_TRANSACTIONS, transactions.name, transactions.read_bytes()
            )
        ),
        "depositos": len(
            parse_source_file(SOURCE_DEPOSITS, deposits.name, deposits.read_bytes())
        ),
    }
    for label, count in results.items():
        print(f"{label}: {count} filas reconocidas")


if __name__ == "__main__":
    main(sys.argv[1:])

