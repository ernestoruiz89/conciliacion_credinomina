"""Bookkeeping evidence is independent of the financial reconciliation result."""
from credinomina_reconciliation.rounding import money


def base_registration_status(item):
    """A typed voucher is a claim, not proof of a posting in the external core.

    Imported evidence describes the original ledger row, not an approval of its
    subsequent financial classification. Explicit exception verification is
    applied separately and is the only route to `Registrada` for manual items.
    """
    if item.get("category") == "Diferencia por tolerancia":
        return "No requiere registro"
    required = ("accounting_source_key", "source_file", "source_file_hash", "source_row",
                "source_account", "source_voucher", "source_date")
    debit, credit = money(item.get("source_debit")), money(item.get("source_credit"))
    single_side = (debit > 0 and credit == 0) or (credit > 0 and debit == 0)
    if (all(item.get(field) for field in required) and single_side
            and item.get("source_currency") in {"NIO", "USD"}):
        return "Importada del core"
    if str(item.get("voucher") or "").strip() or str(item.get("source_voucher") or "").strip():
        return "Asiento informado"
    return "Pendiente de registro"


def exception_registration_status(exception):
    if exception.get("status") == "Resuelta" and exception.get("core_evidence_key"):
        return "Registrada"
    return "Asiento informado" if str(exception.get("core_voucher") or "").strip() else "Pendiente de registro"
