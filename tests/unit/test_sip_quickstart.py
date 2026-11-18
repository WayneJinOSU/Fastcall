from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sip_quickstart


class SipQuickstartTests(unittest.TestCase):
    def run_quickstart(self, call_status="succeeded", extra_args=None):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.compose"
            env_file.write_text(
                "FASTCALL_OPENAPI_TOKEN=token\n"
                "FASTCALL_HTTP_PORT=8080\n"
                "FREESWITCH_LOCAL_USER=8615908280801\n"
            )
            requests = []

            def request(base_url, token, method, path, payload=None):
                self.assertEqual(base_url, "http://localhost:8080")
                requests.append((method, path, payload))
                if method == "PUT":
                    return {"task_id": "task_1", "state_version": 1}
                if path.endswith("/contacts"):
                    return {"accepted_contacts": [{"contact_id": "contact_1"}]}
                if path.endswith("/start"):
                    return {"status": "running"}
                if path.endswith("/calls"):
                    return {"items": [{
                        "call_id": "call_1", "status": call_status,
                        "result_code": "NORMAL_CLEARING",
                    }]}
                return {"status": "completed"}

            argv = ["sip_quickstart.py", "--robot-code", "robot-1", *(extra_args or [])]
            with patch.object(sip_quickstart, "ENV_FILE", env_file), patch.object(
                sip_quickstart, "api_request", side_effect=request
            ), patch("sys.argv", argv):
                result = sip_quickstart.main()
        return result, requests

    def test_quickstart_uses_the_normal_outbound_task_flow(self):
        result, requests = self.run_quickstart()
        self.assertEqual(result, 0)
        self.assertEqual(requests[0][0], "PUT")
        self.assertTrue(requests[0][1].startswith("/openapi/v1/outbound-tasks/sip-"))
        self.assertEqual(requests[0][2]["robot_code"], "robot-1")
        self.assertEqual(
            requests[1][2]["contacts"][0]["phone"], "+8615908280801"
        )
        self.assertEqual(
            requests[2],
            ("POST", "/openapi/v1/outbound-tasks/task_1/start", {"expected_state_version": 1}),
        )

    def test_customer_task_id_rejects_url_path_separators(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_quickstart(extra_args=[
                "--customer-task-id", "customer task/1",
            ])
        self.assertEqual(raised.exception.code, 2)

    def test_customer_task_id_accepts_safe_characters(self):
        result, requests = self.run_quickstart(extra_args=[
            "--customer-task-id", "customer-task_1.2",
        ])
        self.assertEqual(result, 0)
        self.assertEqual(
            requests[0][1], "/openapi/v1/outbound-tasks/customer-task_1.2"
        )

    def test_help_works_without_deployment_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                sip_quickstart, "ENV_FILE", Path(directory) / "missing.env"
            ), patch("sys.argv", ["sip_quickstart.py", "--help"]):
                with self.assertRaises(SystemExit) as raised:
                    sip_quickstart.main()
        self.assertEqual(raised.exception.code, 0)

    def test_completed_task_with_failed_call_returns_failure(self):
        result, _ = self.run_quickstart(call_status="failed")
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
