import unittest

from collection_agents.metrics.collector import call_source_from_payload
from collection_agents.storage.reports import CallReportRepository


class MockCallSourceTests(unittest.TestCase):
    def test_explicit_mock_source_precedes_fastcall_session(self):
        self.assertEqual(call_source_from_payload({"data": {
            "claimGeneration": 1,
            "callSource": "mock_call",
        }}), "mock_call")

    def test_existing_sources_are_unchanged(self):
        self.assertEqual(call_source_from_payload({"data": {"claimGeneration": 1}}), "fastcall_outbound")
        self.assertEqual(call_source_from_payload({"data": {"callUUID": "console-test-1"}}), "console_voice_test")

    def test_formal_reports_exclude_mock_but_mock_detail_remains_queryable(self):
        formal, _ = CallReportRepository._where(None, None, None, None, "formal", None)
        detail, params = CallReportRepository._where(None, None, None, None, "mock_call", "call-1")
        self.assertIn("<> 'mock_call'", formal)
        self.assertIn("= :source", detail)
        self.assertEqual(params["source"], "mock_call")


if __name__ == "__main__":
    unittest.main()
