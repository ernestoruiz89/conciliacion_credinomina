"""Check exception follow-up rules without retaining records on a demo site."""

import frappe


TEST_SITES = {"cn-reconciliation-test.local", "copilot-demo.local"}


def _must_reject(document):
    try:
        document.save()
    except frappe.ValidationError:
        return
    raise AssertionError("Se aceptó una transición o alteración inválida de la excepción.")


def run():
    if frappe.local.site not in TEST_SITES:
        raise RuntimeError(f"Solo puede ejecutarse en {', '.join(sorted(TEST_SITES))}")
    frappe.set_user("Administrator")
    frappe.db.savepoint("cn_exception_workflow_smoke")
    try:
        exception = frappe.get_doc({
            "doctype": "CN Reconciliation Exception",
            "exception_type": "Deducción parcial",
            "status": "Abierta",
            "description": "Prueba de seguimiento sin datos productivos.",
        }).insert()
        assert exception.cause_category == "Por determinar"

        exception.status = "En revision"
        _must_reject(exception)
        exception.reload()
        exception.status = "En revision"
        exception.assigned_to = "Administrator"
        exception.next_action = "Solicitar aclaración a la empresa."
        exception.commitment_date = "2027-01-10"
        exception.append("follow_up_actions", {
            "action_type": "Contacto con empresa",
            "details": "Se solicitó el soporte de planilla.",
            "external_reference": "OFICIO-TEST-001",
        })
        exception.save()
        exception.reload()
        assert exception.status == "En revision"
        action = exception.follow_up_actions[0]
        assert action.action_by == "Administrator" and action.action_at

        action.details = "Cambio retroactivo"
        _must_reject(exception)
        exception.reload()
        exception.next_action = ""
        _must_reject(exception)
        exception.reload()
        exception.set("follow_up_actions", [])
        _must_reject(exception)
        exception.reload()

        exception.status = "Resuelta"
        exception.resolution = "La empresa confirmó el descuento parcial."
        _must_reject(exception)
        exception.reload()
        exception.status = "Resuelta"
        exception.resolution = "La empresa confirmó el descuento parcial."
        exception.cause_category = "Ingreso insuficiente"
        exception.save()
        assert exception.status == "Resuelta"
        return {"status": exception.status, "actions": len(exception.follow_up_actions)}
    finally:
        frappe.db.rollback(save_point="cn_exception_workflow_smoke")
