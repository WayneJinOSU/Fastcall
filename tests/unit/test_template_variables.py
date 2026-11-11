import unittest

from collection_agents.services.call_config import (
    create_system_prompt,
    prepare_text_for_tts,
)


class TemplateVariableTests(unittest.TestCase):
    def test_prompt_uses_runtime_variables(self):
        prompt = create_system_prompt(
            "Call {{user_name}} from {{app_type}}.",
            {},
            {"user_name": "Juan", "app_type": "Retail"},
        )
        self.assertEqual(prompt, "Call Juan from Retail.")

    def test_prompt_and_flow_tts_share_runtime_variables(self):
        variables = {"user_name": "Juan", "app_type": "Retail"}
        prompt = create_system_prompt("Call {{user_name}} from {{app_type}}.", {}, variables)
        welcome = prepare_text_for_tts(
            "Am I speaking with {{user_name}}?",
            {"locale": "en-US"},
            variables,
        )
        self.assertEqual(prompt, "Call Juan from Retail.")
        self.assertEqual(welcome, "Am I speaking with Juan?")

    def test_missing_runtime_values_keep_legacy_template_behavior(self):
        self.assertEqual(
            create_system_prompt("Call {{user_name}}.", {}),
            "Call {{user_name}}.",
        )


if __name__ == "__main__":
    unittest.main()
