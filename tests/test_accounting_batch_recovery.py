from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation import accounting_batch_store as store


class BatchRecoveryTests(unittest.TestCase):
    def state(self):
        return {"user": "owner", "status": "En cola", "phase": "create", "job_id": "attempt-1",
                "options": {"employer_assignments": {"2": "Elegida"}}, "completed_blocks": 2,
                "total_blocks": 4, "created_count": 50}

    def setup_calls(self, stack, state, status):
        stack.enter_context(patch.object(bulk, "_state", return_value=state))
        stack.enter_context(patch.object(bulk, "_", side_effect=lambda value: value))
        stack.enter_context(patch.object(store, "ensure_mutex"))
        stack.enter_context(patch.object(store, "results", return_value=[]))
        job = Mock()
        job.get_status.return_value = status
        stack.enter_context(patch("frappe.utils.background_jobs.get_job", return_value=job if status else None))
        stack.enter_context(patch.object(bulk.frappe, "throw", side_effect=lambda message, *args: (_ for _ in ()).throw(ValueError(message))))
        return stack.enter_context(patch.object(bulk, "_enqueue"))

    def test_live_worker_and_queued_job_cannot_be_resumed(self):
        for status in ("started", "queued", "deferred", "scheduled"):
            with self.subTest(status=status), ExitStack() as stack:
                enqueue = self.setup_calls(stack, self.state(), status)
                with self.assertRaisesRegex(ValueError, "sigue activo"):
                    bulk.resume_bulk_import("token")
                enqueue.assert_not_called()

    def test_resume_retains_cursor_and_choices_after_lost_delivery(self):
        for status in (None, "failed", "finished", "stopped"):
            with self.subTest(status=status), ExitStack() as stack:
                state = self.state()
                enqueue = self.setup_calls(stack, state, status)
                bulk.resume_bulk_import("token")
                saved = enqueue.call_args.args[1]
                self.assertEqual(saved["completed_blocks"], 2)
                self.assertEqual(saved["options"]["employer_assignments"], {"2": "Elegida"})

    def test_status_does_not_overwrite_durable_state_when_worker_missing(self):
        with ExitStack() as stack:
            state = self.state()
            self.setup_calls(stack, state, None)
            save = stack.enter_context(patch.object(bulk, "_store"))
            result = bulk.get_bulk_import_status("token")
            self.assertTrue(result["resumable"])
            self.assertEqual(result["status"], "Error")
            self.assertEqual(state["status"], "En cola")
            save.assert_not_called()

    def test_unavailable_queue_is_not_evidence_of_dead_worker(self):
        with ExitStack() as stack:
            enqueue = self.setup_calls(stack, self.state(), "started")
            stack.enter_context(patch("frappe.utils.background_jobs.get_job", side_effect=ConnectionError("Redis unavailable")))
            with self.assertRaises(ConnectionError):
                bulk.resume_bulk_import("token")
            enqueue.assert_not_called()

    def test_confirmation_rejects_unanalyzed_server_choices(self):
        with ExitStack() as stack:
            state = self.state()
            state.update(status="Vista previa", phase="preview", summary={"issues_count": 0, "rows": 1},
                         draft_assignments={"2": "Otra"})
            enqueue = self.setup_calls(stack, state, "finished")
            with self.assertRaisesRegex(ValueError, "sin analizar"):
                bulk.confirm_bulk_import("token")
            enqueue.assert_not_called()

    def test_state_owner_is_checked_even_with_import_permissions(self):
        with patch.object(bulk, "_permissions"), patch.object(store, "load_state", return_value=self.state()), \
             patch.object(bulk.frappe, "session", SimpleNamespace(user="someone-else")), \
             patch.object(bulk, "_", side_effect=lambda value: value), \
             patch.object(bulk.frappe, "throw", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                bulk._state("token")
