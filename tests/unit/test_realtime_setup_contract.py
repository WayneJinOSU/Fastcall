import inspect
import unittest

from collection_agents.bots.realtime.orchestrator import (
    _setup_realtime_call,
    run_realtime_bot,
)


class RealtimeSetupContractTests(unittest.TestCase):
    def test_asr_config_is_returned_and_unpacked(self):
        setup_source = inspect.getsource(_setup_realtime_call)
        run_source = inspect.getsource(run_realtime_bot)
        self.assertIn('"asr_config": asr_config', setup_source)
        self.assertIn('asr_config = svc["asr_config"]', run_source)


if __name__ == "__main__":
    unittest.main()
